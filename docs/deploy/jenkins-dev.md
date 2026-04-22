# Jenkins branch-routed deployment

This repository's `slack-emoji-tailor` deployment is Jenkins-mediated only. Recurring app deployments to `enm-server` must be started and executed by the Jenkins job; do not deploy the app with ad-hoc server-side Docker or Compose commands.

## Scope

The Jenkins deployment path is responsible for:

- running the project checks before deployment;
- building a Docker image on the selected Docker-capable Jenkins agent and either keeping it as a deploy-host local image (`remote-build`) or pushing it to the approved registry (`registry`);
- deploying the immutable image reference on `enm-server` through Jenkins-called scripts;
- verifying the server-local `/healthz` endpoint; and
- verifying the branch-routed public `/healthz` URL from a public-check Jenkins agent/probe.

Branch routing is automatic: `main` deploys to production (`emoji.enmsoftware.com`, port `3100`, project/path suffix `prod`), while every non-`main` branch deploys to dev (`dev.emoji.enmsoftware.com`, port `18082`, project/path suffix `dev`). DNS records, Nginx virtual hosts, Certbot, and reverse-proxy changes are external prerequisites. The Jenkins job must not apply them, but a broken public health check still fails the deployment.

All non-main branches share dev. The last successful non-main deploy wins and overwrites the shared `dev.emoji.enmsoftware.com` runtime, so branch jobs may build/index freely but must not mutate the live service unless `RUN_DEPLOY=true` is explicitly set. Keep `RUN_DEPLOY=false` as the safe default for branch indexing and routine build verification. A `main` deployment targets production and is production-impacting; validate it with `DEPLOY_DRY_RUN=true` first unless there is explicit production deployment authorization.

## Required Jenkins labels

| Parameter | Requirement |
| --- | --- |
| `BUILD_AGENT_LABEL` | Linux Jenkins agent with `python3`, `uv`, Docker CLI/daemon access, and `docker buildx`. The current controller on `enm-server` is not sufficient by itself. |
| `PUBLIC_CHECK_AGENT_LABEL` | Jenkins agent/probe outside `enm-server` used only for public HTTPS health validation. It must not run through SSH on the target host. |

## Required credentials and parameters

Keep all secrets in Jenkins credentials, never in committed files or archived plaintext logs.

| Name | Purpose |
| --- | --- |
| `DEPLOY_SSH_CREDENTIALS_ID` | SSH credential Jenkins uses to reach `enm-server`. |
| `IMAGE_DISTRIBUTION_MODE` | `remote-build` for local deploy-host images, or `registry` for registry push/pull. |
| `LOCAL_IMAGE_REPOSITORY` | Local Docker repository used in `remote-build` mode. |
| `REGISTRY_CREDENTIALS_ID` | Registry push credential for the build agent when `IMAGE_DISTRIBUTION_MODE=registry`. |
| `REGISTRY_PULL_CREDENTIALS_ID` | Optional registry pull credential for `enm-server`; when used, Jenkins-called scripts must use `docker login --password-stdin` with an isolated `DOCKER_CONFIG`. |
| `REGISTRY_IMAGE` | Registry repository name for the application image. |
| `IMAGE_REF` | Immutable image tag or digest selected by Jenkins for deployment. Do not deploy moving aliases such as `dev-latest` or `prod-latest`. |
| `APP_HOST_PORT` / `DEPLOY_APP_PORT` | Optional override. Auto target: `main -> 3100`, non-`main -> 18082`. |
| `PUBLIC_HEALTHCHECK_URL` | Optional override. Auto target: `main -> https://emoji.enmsoftware.com/healthz`, non-`main -> https://dev.emoji.enmsoftware.com/healthz`. |
| `DEPLOY_PATH` | Server-side deployment directory managed by Jenkins-called scripts. |
| `DEPLOY_COMPOSE_PROJECT` | Optional override. Auto target: `slack-emoji-tailor-prod` for `main`, `slack-emoji-tailor-dev` otherwise. |

Moving aliases such as `dev-latest` or `prod-latest` may be created as convenience aliases for humans, but the deployed `IMAGE_REF` must be an immutable build tag or digest so rollback evidence remains reproducible.

## Compose runtime contract

`docker-compose.dev.deploy.yml` is the repo-managed runtime definition for both branch-routed targets. It intentionally:

- uses `image: ${IMAGE_REF}` and no `build:` block;
- binds only to `127.0.0.1:${APP_HOST_PORT:-18082}:8000`;
- uses `restart: unless-stopped`;
- omits a fixed `container_name` so dev/prod Compose projects can coexist; and
- health-checks `http://127.0.0.1:8000/healthz` inside the container.

External proxy/TLS configuration must route `emoji.enmsoftware.com` to `127.0.0.1:3100` and `dev.emoji.enmsoftware.com` to `127.0.0.1:18082` on `enm-server` before public Jenkins deployments can pass.

## Deployment flow

1. Jenkins checks out the repository.
2. Jenkins runs the project test suite.
3. Jenkins preflights the selected build agent for Python, `uv`, Docker, and buildx.
4. Jenkins builds the Docker image from this repository's `Dockerfile`.
5. In `registry` mode Jenkins pushes an immutable image tag or digest to the configured registry; in `remote-build` mode the image remains on the Docker-capable deploy-host agent and the deploy script skips `docker compose pull`.
6. Jenkins prepares a deploy preview containing the target host, path, compose project, image reference, ports, and health URLs.
7. If deployment is enabled and not a dry run, Jenkins invokes the repo-managed deploy script over SSH.
8. The Jenkins-called script updates the server-side env/markers under a remote lock, pulls `IMAGE_REF` when registry mode is enabled, starts the Compose project, and verifies server-local `/healthz`.
9. Jenkins runs the public health check from `PUBLIC_CHECK_AGENT_LABEL`, outside `enm-server`, with TLS verification enabled.
10. Jenkins archives sanitized evidence and marks the deployment successful only if every gate passes.

No step in the success path requires a person or Codex/OMX to run server-side app deployment commands manually.

## Dry run and evidence

Use the Jenkins job's dry-run mode before the first real deployment and after pipeline changes. Dry run should archive the planned target host, deploy path, compose project, immutable image reference, app port, and health URLs without mutating the server.

Every deployment should archive or print sanitized evidence for:

- test result;
- immutable image reference and digest when available;
- deploy preview;
- target host/path/project/port;
- server-local health output;
- external public health output and proof of the checking agent;
- previous/current image markers;
- Compose service status after deployment; and
- rollback result when rollback is attempted.

## Rollback flow

Rollback is Jenkins-mediated. A failed post-activation local or public health check must trigger the Jenkins rollback path when a previous image marker exists:

1. Jenkins records the previous image marker before activating the new image.
2. If the new deployment fails after activation, Jenkins restores the previous `IMAGE_REF` through the repo-managed rollback script or rollback mode.
3. Jenkins reruns server-local health and external public health.
4. Jenkins archives rollback evidence and fails the original build so the failed rollout remains visible.

If this is a first install and no previous image marker exists, Jenkins should stop the failed first-install service, record `NO_PREVIOUS_IMAGE_AVAILABLE`, archive evidence, and fail the build rather than claiming success.

## Operator guardrails

- Do not commit `.env` files, registry tokens, SSH keys, or Jenkins secrets.
- Do not deploy mutable tags such as `dev-latest` or `prod-latest`.
- Do not apply or edit DNS, Nginx, Certbot, or reverse-proxy configuration from this repo's Jenkins deployment.
- Do not treat on-host curl output as proof of public availability; public validation must run from `PUBLIC_CHECK_AGENT_LABEL` outside `enm-server`.
- Do not bypass Jenkins for recurring app deployment.
- Treat every non-main branch as a shared-dev deploy candidate only; non-main jobs must resolve to `dev.emoji.enmsoftware.com`, `/home/ameforce/slack-emoji-tailor-dev`, port `18082`, and `slack-emoji-tailor-dev`.
- Treat `main` as production-only; prod deploys are production-impacting and must resolve to `emoji.enmsoftware.com`, `/home/ameforce/slack-emoji-tailor-prod`, port `3100`, and `slack-emoji-tailor-prod`.
- Reject target override mismatches instead of silently accepting a non-main branch pointed at production or `main` pointed at shared dev.
