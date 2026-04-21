# Jenkins dev deployment security checklist

Use this checklist before enabling or re-running the Jenkins dev deployment for `slack-emoji-tailor`. It is intentionally operational: every item should have Jenkins evidence, a credential record, or an explicit owner before a deployment is considered safe.

## Docker-capable build agent risk

- [ ] `BUILD_AGENT_LABEL` points to a dedicated Docker-capable Jenkins agent, not the current Jenkins controller on `enm-server`.
- [ ] The selected agent proves `python3 --version`, `uv --version`, `docker version`, and `docker buildx version` in the Jenkins preflight log.
- [ ] The agent's Docker daemon/BuildKit access is approved as host-powerful access; do not mount the host Docker socket into the controller unless that separate security exception is recorded.
- [ ] Jenkins fails fast when the Docker-capable toolchain is absent; it must not fall back to a remote server build or an on-the-fly internet bootstrap of `uv`.
- [ ] The deployed `IMAGE_REF` is an immutable tag or digest. `dev-latest` may exist only as a convenience alias and must never be the deployment ref.

## Registry credential handling

- [ ] `REGISTRY_CREDENTIALS_ID` is a Jenkins credential used only for image push from the build agent.
- [ ] If the server needs pull auth, `REGISTRY_PULL_CREDENTIALS_ID` is supplied by Jenkins or pull auth is pre-provisioned on `enm-server` with an explicit owner.
- [ ] Any Jenkins-managed remote login uses `docker login --password-stdin`; never place a password/token in command arguments, compose files, `.env`, or archived evidence.
- [ ] Remote pull login uses an isolated `DOCKER_CONFIG` (isolated DOCKER_CONFIG) under the deploy path with restricted permissions, not the default user Docker config unless that is the approved pre-provisioned path.
- [ ] Registry credential IDs may appear in sanitized previews; registry secrets, tokens, and passwords must not appear in console logs or artifacts.

## External public-check proof

- [ ] `PUBLIC_CHECK_AGENT_LABEL` points to a Jenkins agent/probe outside enm-server and outside the Jenkins controller host.
- [ ] The public-health stage archives proof such as `hostname` and `hostnamectl` output and fails if that proof identifies `enm-server` or the controller host.
- [ ] The public check curls `PUBLIC_HEALTHCHECK_URL` directly from the public-check agent with TLS verification enabled; it is not an SSH/on-host curl.
- [ ] A failed public health check fails the deployment. When a previous image exists, it must trigger rollback even if local health passed.

## Secret redaction and artifact hygiene

- [ ] Shell tracing (`set -x`) is disabled anywhere credentials are in scope.
- [ ] Deploy previews include target host, path, compose project, app port, health URLs, image ref, and credential IDs only; they do not include secret values.
- [ ] Failure artifacts are sanitized before archival: no registry passwords, SSH private keys, tokens, cookie values, or `.env` secrets.
- [ ] No `.env`, Docker auth config, private key, or generated credential file is committed to the repository.

## Rollback and first-install controls

- [ ] Before activation, Jenkins records previous and current image markers in the remote deploy path.
- [ ] `scripts/deploy/jenkins-enm-deploy.sh` runs compose mutations under remote `flock` to prevent concurrent writes.
- [ ] If local or external public health fails after activation and `PREVIOUS_IMAGE` exists, Jenkins invokes the rollback path and archives rollback evidence.
- [ ] If no previous image exists, Jenkins records `NO_PREVIOUS_IMAGE_AVAILABLE`, runs `docker compose down` for the failed first install, archives evidence, and fails the build without claiming success.
- [ ] Rollback remains Jenkins-mediated; do not perform manual server deployment commands as the success path.

## Jenkins job setup

- [ ] Job parameters are configured for `BUILD_AGENT_LABEL`, `PUBLIC_CHECK_AGENT_LABEL`, `DEPLOY_HOST`, `DEPLOY_SSH_USER`, `DEPLOY_PATH`, `DEPLOY_SSH_CREDENTIALS_ID`, `REGISTRY_URL`, `REGISTRY_IMAGE`, `REGISTRY_CREDENTIALS_ID`, optional `REGISTRY_PULL_CREDENTIALS_ID`, `DEPLOY_APP_PORT`, `LOCAL_HEALTHCHECK_URL`, `PUBLIC_HEALTHCHECK_URL`, `DEPLOY_COMPOSE_PROJECT`, and `DEPLOY_ALLOWED_BRANCHES`.
- [ ] `RUN_DEPLOY=false` is the safe default for new or unreviewed jobs; first execution should use `DEPLOY_DRY_RUN=true` and archive the preview.
- [ ] Build serialization is enabled (`disableConcurrentBuilds` plus remote `flock`) so two deploys cannot mutate the same compose project at once.
- [ ] The deploy path and app port are dedicated to `slack-emoji-tailor-dev`; confirm the external reverse proxy points to `127.0.0.1:${DEPLOY_APP_PORT}` before expecting public health to pass.
- [ ] No nginx, no DNS, Certbot, or reverse-proxy mutation is performed by this repo or Jenkins job. Those changes are external prerequisites only, and failure to meet them must keep the Jenkins deployment red.
