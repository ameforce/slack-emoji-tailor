#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '[deploy] %s\n' "$*" >&2
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_env() {
  local name=$1
  if [ -z "${!name:-}" ]; then
    fail "required environment variable ${name} is not set"
  fi
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"
}

is_immutable_image_ref() {
  local ref=$1
  local last_segment=${ref##*/}

  case "$ref" in
    *:latest|*:dev-latest|latest|dev-latest) return 1 ;;
  esac

  if [[ "$ref" == *@sha256:* ]]; then
    return 0
  fi

  [[ "$last_segment" == *:* ]]
}

build_ssh_command() {
  SSH_COMMAND=(ssh)
  if [ -n "${SSH_OPTS:-}" ]; then
    # Jenkins may provide vetted OpenSSH options as a single parameter.
    # shellcheck disable=SC2206
    local extra_ssh_opts=($SSH_OPTS)
    SSH_COMMAND+=("${extra_ssh_opts[@]}")
  fi
  SSH_COMMAND+=("${SSH_TARGET}")
}

build_scp_command() {
  SCP_COMMAND=(scp)
  if [ -n "${SCP_OPTS:-${SSH_OPTS:-}}" ]; then
    # Jenkins may provide vetted OpenSSH options as a single parameter.
    # shellcheck disable=SC2206
    local extra_scp_opts=(${SCP_OPTS:-${SSH_OPTS:-}})
    SCP_COMMAND+=("${extra_scp_opts[@]}")
  fi
}

remote_env_prefix() {
  local prefix="env"
  local name
  for name in "$@"; do
    prefix+=" ${name}=$(shell_quote "${!name:-}")"
  done
  printf '%s' "$prefix"
}

upload_compose_file() {
  if [ ! -f "$DEPLOY_COMPOSE_SOURCE" ]; then
    fail "compose source file not found: ${DEPLOY_COMPOSE_SOURCE}"
  fi

  log "uploading compose definition to ${SSH_TARGET}:${DEPLOY_PATH}/${REMOTE_COMPOSE_TMP}"
  "${SSH_COMMAND[@]}" "mkdir -p $(shell_quote "$DEPLOY_PATH")"
  "${SCP_COMMAND[@]}" "$DEPLOY_COMPOSE_SOURCE" "${SSH_TARGET}:${DEPLOY_PATH}/${REMOTE_COMPOSE_TMP}"
}

run_remote_deploy() {
  local env_prefix
  env_prefix=$(remote_env_prefix \
    DEPLOY_PATH DEPLOY_COMPOSE_FILE DEPLOY_ENV_FILE DEPLOY_COMPOSE_PROJECT \
    DEPLOY_MARKER_DIR DEPLOY_LOCK_FILE IMAGE_REF APP_HOST_PORT LOCAL_HEALTHCHECK_URL \
    HEALTH_RETRIES HEALTH_SLEEP_SECONDS HEALTH_TIMEOUT_SECONDS REGISTRY_URL \
    REGISTRY_PULL_USERNAME REGISTRY_PULL_PASSWORD REMOTE_DOCKER_CONFIG REMOTE_COMPOSE_TMP)

  # Keep tracing disabled around the remote invocation so Jenkins does not echo
  # credential-bearing environment values. The remote login uses password-stdin.
  set +x
  "${SSH_COMMAND[@]}" "${env_prefix} bash -s" <<'REMOTE_DEPLOY'
set -Eeuo pipefail

log() {
  printf '[remote-deploy] %s\n' "$*" >&2
}

fail() {
  log "ERROR: $*"
  exit 1
}

write_env_file() {
  local image_ref=$1
  local env_tmp="${DEPLOY_PATH}/${DEPLOY_ENV_FILE}.tmp"
  umask 077
  {
    printf 'IMAGE_REF=%s\n' "$image_ref"
    printf 'APP_HOST_PORT=%s\n' "$APP_HOST_PORT"
  } >"$env_tmp"
  mv "$env_tmp" "${DEPLOY_PATH}/${DEPLOY_ENV_FILE}"
}

compose_cmd() {
  docker compose \
    --env-file "${DEPLOY_PATH}/${DEPLOY_ENV_FILE}" \
    -f "${DEPLOY_PATH}/${DEPLOY_COMPOSE_FILE}" \
    -p "$DEPLOY_COMPOSE_PROJECT" \
    "$@"
}

check_local_health() {
  local attempt
  for attempt in $(seq 1 "$HEALTH_RETRIES"); do
    if curl --fail --show-error --silent --max-time "$HEALTH_TIMEOUT_SECONDS" "$LOCAL_HEALTHCHECK_URL"; then
      printf '\n' >&2
      log "local health passed on attempt ${attempt}"
      return 0
    fi
    log "local health attempt ${attempt}/${HEALTH_RETRIES} failed"
    sleep "$HEALTH_SLEEP_SECONDS"
  done
  return 1
}

compose_down_after_failed_first_install() {
  log "no previous image marker exists; stopping failed first install"
  compose_cmd down --remove-orphans || true
  printf 'NO_PREVIOUS_IMAGE_AVAILABLE\n' >"${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/first-install-failed.marker"
}

rollback_to_previous_image() {
  local previous_image=$1
  log "rolling back to previous image marker"
  write_env_file "$previous_image"
  compose_cmd pull
  compose_cmd up -d
  if ! check_local_health; then
    compose_cmd ps || true
    compose_cmd logs --tail=80 || true
    fail "rollback image failed local health"
  fi
  printf '%s\n' "$previous_image" >"${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/current-image.ref"
  log "rollback local health passed"
}

cd "$DEPLOY_PATH"
mkdir -p "$DEPLOY_MARKER_DIR"
exec 9>"$DEPLOY_LOCK_FILE"
flock -x 9

if [ -n "$REMOTE_COMPOSE_TMP" ]; then
  if [ ! -f "${DEPLOY_PATH}/${REMOTE_COMPOSE_TMP}" ]; then
    fail "uploaded compose file missing: ${REMOTE_COMPOSE_TMP}"
  fi
  mv "${DEPLOY_PATH}/${REMOTE_COMPOSE_TMP}" "${DEPLOY_PATH}/${DEPLOY_COMPOSE_FILE}"
fi

previous_image=""
if [ -s "${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/current-image.ref" ]; then
  previous_image=$(cat "${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/current-image.ref")
elif [ -f "${DEPLOY_PATH}/${DEPLOY_ENV_FILE}" ]; then
  previous_image=$(awk -F= '$1 == "IMAGE_REF" {print substr($0, index($0, "=") + 1)}' "${DEPLOY_PATH}/${DEPLOY_ENV_FILE}" | tail -n 1)
fi

if [ -n "$previous_image" ]; then
  printf '%s\n' "$previous_image" >"${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/previous-image.ref"
fi
printf '%s\n' "$IMAGE_REF" >"${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/pending-image.ref"

if [ -n "$REGISTRY_PULL_USERNAME" ] || [ -n "$REGISTRY_PULL_PASSWORD" ]; then
  [ -n "$REGISTRY_URL" ] || fail "REGISTRY_URL is required with pull credentials"
  [ -n "$REGISTRY_PULL_USERNAME" ] || fail "REGISTRY_PULL_USERNAME is required when pull auth is configured"
  [ -n "$REGISTRY_PULL_PASSWORD" ] || fail "REGISTRY_PULL_PASSWORD is required when pull auth is configured"
  mkdir -p "$REMOTE_DOCKER_CONFIG"
  chmod 700 "$REMOTE_DOCKER_CONFIG"
  log "logging in to registry with isolated Docker config"
  printf '%s' "$REGISTRY_PULL_PASSWORD" | DOCKER_CONFIG="$REMOTE_DOCKER_CONFIG" docker login "$REGISTRY_URL" --username "$REGISTRY_PULL_USERNAME" --password-stdin >/dev/null
  export DOCKER_CONFIG="$REMOTE_DOCKER_CONFIG"
fi

write_env_file "$IMAGE_REF"
log "pulling ${IMAGE_REF}"
if ! compose_cmd pull; then
  compose_cmd ps || true
  fail "compose pull failed before activation"
fi

log "starting compose project ${DEPLOY_COMPOSE_PROJECT}"
if ! compose_cmd up -d; then
  compose_cmd ps || true
  compose_cmd logs --tail=80 || true
  if [ -n "$previous_image" ]; then
    rollback_to_previous_image "$previous_image"
  else
    compose_down_after_failed_first_install
  fi
  fail "compose up failed"
fi

if ! check_local_health; then
  compose_cmd ps || true
  compose_cmd logs --tail=120 || true
  if [ -n "$previous_image" ]; then
    rollback_to_previous_image "$previous_image"
  else
    compose_down_after_failed_first_install
  fi
  fail "local health failed after activation"
fi

printf '%s\n' "$IMAGE_REF" >"${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/current-image.ref"
rm -f "${DEPLOY_PATH}/${DEPLOY_MARKER_DIR}/pending-image.ref"
compose_cmd ps
log "deployment completed for ${IMAGE_REF}"
REMOTE_DEPLOY
  set -x 2>/dev/null || true
}

require_env DEPLOY_HOST
require_env DEPLOY_SSH_USER
require_env DEPLOY_PATH
require_env IMAGE_REF

if ! is_immutable_image_ref "$IMAGE_REF"; then
  fail "IMAGE_REF must be an immutable tag or digest, not latest/dev-latest/unqualified"
fi

DEPLOY_COMPOSE_SOURCE=${DEPLOY_COMPOSE_SOURCE:-docker-compose.dev.deploy.yml}
DEPLOY_COMPOSE_FILE=${DEPLOY_COMPOSE_FILE:-docker-compose.dev.deploy.yml}
DEPLOY_ENV_FILE=${DEPLOY_ENV_FILE:-.env.dev}
DEPLOY_COMPOSE_PROJECT=${DEPLOY_COMPOSE_PROJECT:-slack-emoji-tailor-dev}
APP_HOST_PORT=${APP_HOST_PORT:-${DEPLOY_APP_PORT:-18082}}
LOCAL_HEALTHCHECK_URL=${LOCAL_HEALTHCHECK_URL:-http://127.0.0.1:${APP_HOST_PORT}/healthz}
DEPLOY_MARKER_DIR=${DEPLOY_MARKER_DIR:-.deploy-state}
DEPLOY_LOCK_FILE=${DEPLOY_LOCK_FILE:-${DEPLOY_PATH}/.deploy.lock}
REGISTRY_URL=${REGISTRY_URL:-}
REGISTRY_PULL_USERNAME=${REGISTRY_PULL_USERNAME:-}
REGISTRY_PULL_PASSWORD=${REGISTRY_PULL_PASSWORD:-}
REMOTE_DOCKER_CONFIG=${REMOTE_DOCKER_CONFIG:-${DEPLOY_PATH}/.docker-config}
HEALTH_RETRIES=${HEALTH_RETRIES:-12}
HEALTH_SLEEP_SECONDS=${HEALTH_SLEEP_SECONDS:-5}
HEALTH_TIMEOUT_SECONDS=${HEALTH_TIMEOUT_SECONDS:-5}
REMOTE_COMPOSE_TMP=".${DEPLOY_COMPOSE_FILE}.incoming.$$.tmp"
SSH_TARGET=${DEPLOY_SSH_USER}@${DEPLOY_HOST}
SSH_COMMAND=()
SCP_COMMAND=()

if [ -n "$REGISTRY_PULL_USERNAME" ] || [ -n "$REGISTRY_PULL_PASSWORD" ]; then
  require_env REGISTRY_URL
  require_env REGISTRY_PULL_USERNAME
  require_env REGISTRY_PULL_PASSWORD
fi

if truthy "${DEPLOY_DRY_RUN:-false}"; then
  cat <<DRYRUN
Deploy dry-run preview:
  target: ${SSH_TARGET}
  deploy path: ${DEPLOY_PATH}
  compose source: ${DEPLOY_COMPOSE_SOURCE}
  compose file: ${DEPLOY_COMPOSE_FILE}
  env file: ${DEPLOY_ENV_FILE}
  compose project: ${DEPLOY_COMPOSE_PROJECT}
  lock file: ${DEPLOY_LOCK_FILE}
  marker dir: ${DEPLOY_MARKER_DIR}
  image ref: ${IMAGE_REF}
  app host port: ${APP_HOST_PORT}
  local health: ${LOCAL_HEALTHCHECK_URL}
  registry pull auth: $(if [ -n "$REGISTRY_PULL_USERNAME" ]; then printf 'configured'; else printf 'not configured'; fi)
DRYRUN
  exit 0
fi

build_ssh_command
build_scp_command
upload_compose_file
run_remote_deploy
