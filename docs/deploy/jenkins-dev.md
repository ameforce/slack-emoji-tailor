# Jenkins dev deployment

This repository's dev deployment for `slack-emoji-tailor` is Jenkins-mediated only. Recurring app deployments to `enm-server` must be started and executed by the Jenkins job; do not deploy the app with ad-hoc server-side Docker or Compose commands.

## Scope

The Jenkins deployment path is responsible for:

- running the project checks before deployment;
- building and pushing a Docker image to the approved registry;
- deploying the immutable image reference on `enm-server` through Jenkins-called scripts;
- verifying the server-local `/healthz` endpoint; and
- verifying `https://dev.emoji.enmsoftware.com/healthz` from an external Jenkins agent/probe.

DNS records, Nginx virtual hosts, Certbot, and reverse-proxy changes are external prerequisites. The Jenkins job must not apply them, but a broken public health check still fails the deployment.

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
| `REGISTRY_CREDENTIALS_ID` | Registry push credential for the build agent. |
| `REGISTRY_PULL_CREDENTIALS_ID` | Optional registry pull credential for `enm-server`; when used, Jenkins-called scripts must use `docker login --password-stdin` with an isolated `DOCKER_CONFIG`. |
| `REGISTRY_IMAGE` | Registry repository name for the application image. |
| `IMAGE_REF` | Immutable image tag or digest selected by Jenkins for deployment. Do not deploy `dev-latest`. |
| `APP_HOST_PORT` / `DEPLOY_APP_PORT` | Localhost-bound target port on `enm-server`; default proposal is `18082`. |
| `PUBLIC_HEALTHCHECK_URL` | Public health URL, normally `https://dev.emoji.enmsoftware.com/healthz`. |
| `DEPLOY_PATH` | Server-side deployment directory managed by Jenkins-called scripts. |
| `DEPLOY_COMPOSE_PROJECT` | Compose project name, normally `slack-emoji-tailor-dev`. |

`dev-latest` may be pushed as a convenience alias for humans, but the deployed `IMAGE_REF` must be an immutable build tag or digest so rollback evidence remains reproducible.

## Compose runtime contract

`docker-compose.dev.deploy.yml` is the repo-managed runtime definition for the dev server. It intentionally:

- uses `image: ${IMAGE_REF}` and no `build:` block;
- binds only to `127.0.0.1:${APP_HOST_PORT:-18082}:8000`;
- uses `restart: unless-stopped`;
- labels the container as Jenkins-managed dev infrastructure; and
- health-checks `http://127.0.0.1:8000/healthz` inside the container.

External proxy/TLS configuration must route `dev.emoji.enmsoftware.com` to `127.0.0.1:${APP_HOST_PORT}` on `enm-server` before a public Jenkins deployment can pass.

## Deployment flow

1. Jenkins checks out the repository.
2. Jenkins runs the project test suite.
3. Jenkins preflights the selected build agent for Python, `uv`, Docker, and buildx.
4. Jenkins builds the Docker image from this repository's `Dockerfile`.
5. Jenkins pushes an immutable image tag or digest to the configured registry.
6. Jenkins prepares a deploy preview containing the target host, path, compose project, image reference, ports, and health URLs.
7. If deployment is enabled and not a dry run, Jenkins invokes the repo-managed deploy script over SSH.
8. The Jenkins-called script updates the server-side env/markers under a remote lock, pulls `IMAGE_REF`, starts the Compose project, and verifies server-local `/healthz`.
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
- Do not deploy mutable tags such as `dev-latest`.
- Do not apply or edit DNS, Nginx, Certbot, or reverse-proxy configuration from this repo's Jenkins deployment.
- Do not treat on-host curl output as proof of public availability; public validation must run from `PUBLIC_CHECK_AGENT_LABEL` outside `enm-server`.
- Do not bypass Jenkins for recurring app deployment.
