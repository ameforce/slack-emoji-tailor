#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

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
  local last_path tag
  case "$ref" in
    *@sha256:*) return 0 ;;
    ""|latest|dev-latest) return 1 ;;
  esac
  last_path=${ref##*/}
  case "$last_path" in
    *:*)
      tag=${last_path##*:}
      case "$tag" in
        ""|latest|dev-latest) return 1 ;;
        *) return 0 ;;
      esac
      ;;
    *) return 1 ;;
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
}

require_env DEPLOY_HOST
require_env DEPLOY_SSH_USER
require_env DEPLOY_PATH
require_env IMAGE_REF

is_immutable_image_ref "$IMAGE_REF" || fail "IMAGE_REF must be an immutable tag or digest, not latest/dev-latest: ${IMAGE_REF}"

DEPLOY_COMPOSE_FILE=${DEPLOY_COMPOSE_FILE:-docker-compose.dev.deploy.yml}
LOCAL_COMPOSE_FILE=${LOCAL_COMPOSE_FILE:-$DEPLOY_COMPOSE_FILE}
DEPLOY_ENV_FILE=${DEPLOY_ENV_FILE:-.env.dev}
DEPLOY_COMPOSE_PROJECT=${DEPLOY_COMPOSE_PROJECT:-slack-emoji-tailor-dev}
APP_HOST_PORT=${APP_HOST_PORT:-${DEPLOY_APP_PORT:-18082}}
LOCAL_HEALTHCHECK_URL=${LOCAL_HEALTHCHECK_URL:-http://127.0.0.1:${APP_HOST_PORT}/healthz}
DEPLOY_MARKER_DIR=${DEPLOY_MARKER_DIR:-.deploy-state}
DEPLOY_LOCK_FILE=${DEPLOY_LOCK_FILE:-${DEPLOY_PATH}/.deploy.lock}
REMOTE_DOCKER_CONFIG=${REMOTE_DOCKER_CONFIG:-${DEPLOY_PATH}/.docker-config-pull}
REGISTRY_URL=${REGISTRY_URL:-}
REGISTRY_PULL_USERNAME=${REGISTRY_PULL_USERNAME:-}
REGISTRY_PULL_PASSWORD=${REGISTRY_PULL_PASSWORD:-}
HEALTH_RETRIES=${HEALTH_RETRIES:-12}
HEALTH_SLEEP_SECONDS=${HEALTH_SLEEP_SECONDS:-5}
HEALTH_TIMEOUT_SECONDS=${HEALTH_TIMEOUT_SECONDS:-5}
SKIP_COMPOSE_UPLOAD=${SKIP_COMPOSE_UPLOAD:-false}
SSH_TARGET=${DEPLOY_SSH_USER}@${DEPLOY_HOST}
SSH_OPTS=${SSH_OPTS:-}
HAS_PULL_PASSWORD=0

if [ -n "$REGISTRY_PULL_USERNAME" ] || [ -n "$REGISTRY_PULL_PASSWORD" ]; then
  require_env REGISTRY_URL
  require_env REGISTRY_PULL_USERNAME
  require_env REGISTRY_PULL_PASSWORD
  HAS_PULL_PASSWORD=1
fi

if truthy "${DEPLOY_DRY_RUN:-false}"; then
  cat <<DRYRUN
Deploy dry-run preview:
  target: ${SSH_TARGET}
  deploy path: ${DEPLOY_PATH}
  compose upload: $(truthy "$SKIP_COMPOSE_UPLOAD" && printf 'skipped' || printf '%s -> %s' "$LOCAL_COMPOSE_FILE" "$DEPLOY_COMPOSE_FILE")
  env file: ${DEPLOY_ENV_FILE}
  compose project: ${DEPLOY_COMPOSE_PROJECT}
  image ref: ${IMAGE_REF}
  app host port: ${APP_HOST_PORT}
  local health: ${LOCAL_HEALTHCHECK_URL}
  lock file: ${DEPLOY_LOCK_FILE}
  marker dir: ${DEPLOY_MARKER_DIR}
  remote docker config: ${REMOTE_DOCKER_CONFIG}
DRYRUN
  exit 0
fi

ssh_command=(ssh)
if [ -n "$SSH_OPTS" ]; then
  # Jenkins may provide standard OpenSSH options in this string.
  # shellcheck disable=SC2206
  extra_ssh_opts=($SSH_OPTS)
  ssh_command+=("${extra_ssh_opts[@]}")
fi
ssh_command+=("$SSH_TARGET")

remote_run() {
  "${ssh_command[@]}" "$@"
}

remote_mkdir_cmd="mkdir -p $(shell_quote "$DEPLOY_PATH")"
remote_run "$remote_mkdir_cmd"

incoming_compose_path=
if ! truthy "$SKIP_COMPOSE_UPLOAD"; then
  [ -f "$LOCAL_COMPOSE_FILE" ] || fail "compose file not found: ${LOCAL_COMPOSE_FILE}"
  incoming_compose_path="${DEPLOY_PATH}/.${DEPLOY_COMPOSE_FILE}.incoming.$(date +%s).$$"
  log "uploading compose file to ${SSH_TARGET}:${incoming_compose_path}"
  remote_run "cat > $(shell_quote "$incoming_compose_path")" <"$LOCAL_COMPOSE_FILE"
fi

if [ "$HAS_PULL_PASSWORD" = "1" ]; then
  log "performing remote registry pull login with isolated Docker config"
  login_cmd="set -euo pipefail; mkdir -p $(shell_quote "$REMOTE_DOCKER_CONFIG"); chmod 700 $(shell_quote "$REMOTE_DOCKER_CONFIG"); docker --config $(shell_quote "$REMOTE_DOCKER_CONFIG") login $(shell_quote "$REGISTRY_URL") --username $(shell_quote "$REGISTRY_PULL_USERNAME") --password-stdin >/dev/null"
  printf '%s\n' "$REGISTRY_PULL_PASSWORD" | remote_run "$login_cmd"
fi

remote_args=(
  "$DEPLOY_PATH"
  "$incoming_compose_path"
  "$DEPLOY_COMPOSE_FILE"
  "$DEPLOY_ENV_FILE"
  "$DEPLOY_COMPOSE_PROJECT"
  "$IMAGE_REF"
  "$APP_HOST_PORT"
  "$LOCAL_HEALTHCHECK_URL"
  "$DEPLOY_MARKER_DIR"
  "$DEPLOY_LOCK_FILE"
  "$REMOTE_DOCKER_CONFIG"
  "$HEALTH_RETRIES"
  "$HEALTH_SLEEP_SECONDS"
  "$HEALTH_TIMEOUT_SECONDS"
  "$HAS_PULL_PASSWORD"
)
quoted_remote_args=()
for arg in "${remote_args[@]}"; do
  quoted_remote_args+=("$(shell_quote "$arg")")
done

log "starting remote deploy on ${SSH_TARGET}"
remote_run "bash -s -- ${quoted_remote_args[*]}" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

if [ "$#" -ne 15 ]; then
  printf '[deploy:remote] ERROR: expected 15 args, got %s\n' "$#" >&2
  exit 2
fi

deploy_path=$1
incoming_compose_path=$2
compose_file=$3
env_file=$4
compose_project=$5
image_ref=$6
app_host_port=$7
local_health_url=$8
marker_dir_name=$9
lock_file=${10}
remote_docker_config=${11}
health_retries=${12}
health_sleep_seconds=${13}
health_timeout_seconds=${14}
has_pull_auth=${15}

log_remote() {
  printf '[deploy:remote] %s\n' "$*" >&2
}

fail_remote() {
  log_remote "ERROR: $*"
  exit 1
}

is_immutable_image_ref() {
  local ref=$1
  local last_path tag
  case "$ref" in
    *@sha256:*) return 0 ;;
    ""|latest|dev-latest) return 1 ;;
  esac
  last_path=${ref##*/}
  case "$last_path" in
    *:*)
      tag=${last_path##*:}
      case "$tag" in
        ""|latest|dev-latest) return 1 ;;
        *) return 0 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

read_first_file() {
  local path
  for path in "$@"; do
    if [ -s "$path" ]; then
      head -n 1 "$path"
      return 0
    fi
  done
  return 1
}

read_env_image_ref() {
  local env_path=$1
  [ -f "$env_path" ] || return 1
  awk -F= '$1 == "IMAGE_REF" {print substr($0, index($0,"=")+1); exit}' "$env_path"
}

write_env_with_image() {
  local env_path=$1
  local next_image_ref=$2
  local tmp_path
  tmp_path=$(mktemp "${env_path}.tmp.XXXXXX")
  if [ -f "$env_path" ]; then
    grep -v -E '^(IMAGE_REF|APP_HOST_PORT)=' "$env_path" >"$tmp_path" || true
  fi
  printf 'IMAGE_REF=%s\n' "$next_image_ref" >>"$tmp_path"
  printf 'APP_HOST_PORT=%s\n' "$app_host_port" >>"$tmp_path"
  chmod 600 "$tmp_path"
  mv "$tmp_path" "$env_path"
}

health_check() {
  local url=$1
  local attempt
  for attempt in $(seq 1 "$health_retries"); do
    if curl -fsS --max-time "$health_timeout_seconds" "$url"; then
      log_remote "local health passed on attempt ${attempt}"
      return 0
    fi
    log_remote "local health attempt ${attempt}/${health_retries} failed"
    sleep "$health_sleep_seconds"
  done
  return 1
}

capture_evidence() {
  local marker_dir=$1
  local prefix=$2
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" ps >"$marker_dir/${prefix}-ps.txt" 2>&1 || true
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" logs --tail=120 >"$marker_dir/${prefix}-logs.txt" 2>&1 || true
}

first_install_failure() {
  local marker_dir=$1
  local reason=$2
  printf 'NO_PREVIOUS_IMAGE_AVAILABLE\n' >"$marker_dir/rollback-status"
  printf '%s\n' "$reason" >"$marker_dir/last-failure-reason"
  capture_evidence "$marker_dir" "failed-first-install"
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" down || true
  fail_remote "${reason}; no previous image marker exists, compose was stopped"
}

rollback_or_down() {
  local marker_dir=$1
  local previous_image_ref=$2
  local reason=$3

  printf '%s\n' "$reason" >"$marker_dir/last-failure-reason"
  capture_evidence "$marker_dir" "failed-deploy"

  if [ -z "$previous_image_ref" ]; then
    first_install_failure "$marker_dir" "$reason"
  fi
  is_immutable_image_ref "$previous_image_ref" || first_install_failure "$marker_dir" "previous image marker is not immutable: ${previous_image_ref}"

  log_remote "attempting rollback to ${previous_image_ref} after: ${reason}"
  write_env_with_image "$env_file" "$previous_image_ref"
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" pull
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" up -d
  if ! health_check "$local_health_url"; then
    capture_evidence "$marker_dir" "failed-rollback"
    fail_remote "${reason}; rollback to previous image also failed health"
  fi
  printf '%s\n' "$previous_image_ref" >"$marker_dir/current-image-ref"
  printf '%s\n' "$image_ref" >"$marker_dir/rolled-back-from-image-ref"
  date -u +%Y-%m-%dT%H:%M:%SZ >"$marker_dir/last-rollback-at"
  capture_evidence "$marker_dir" "rollback"
  fail_remote "${reason}; rolled back to previous image ${previous_image_ref}"
}

is_immutable_image_ref "$image_ref" || fail_remote "IMAGE_REF is not immutable: ${image_ref}"
mkdir -p "$deploy_path"
touch "$lock_file"
exec 9>"$lock_file"
flock 9

cd "$deploy_path"
if [ -n "$incoming_compose_path" ]; then
  [ -f "$incoming_compose_path" ] || fail_remote "incoming compose file missing: ${incoming_compose_path}"
  mv "$incoming_compose_path" "$compose_file"
fi
[ -f "$compose_file" ] || fail_remote "compose file not found: ${deploy_path}/${compose_file}"

marker_dir=$marker_dir_name
case "$marker_dir" in
  /*) ;;
  *) marker_dir="${deploy_path}/${marker_dir}" ;;
esac
mkdir -p "$marker_dir"

previous_image_ref=$(read_first_file "$marker_dir/current-image-ref" "$marker_dir/current_image_ref" || true)
if [ -z "$previous_image_ref" ]; then
  previous_image_ref=$(read_env_image_ref "$env_file" || true)
fi
if [ -n "$previous_image_ref" ] && is_immutable_image_ref "$previous_image_ref"; then
  printf '%s\n' "$previous_image_ref" >"$marker_dir/previous-image-ref"
else
  previous_image_ref=
  printf 'NO_PREVIOUS_IMAGE_AVAILABLE\n' >"$marker_dir/previous-image-ref"
fi

printf '%s\n' "$image_ref" >"$marker_dir/pending-image-ref"
write_env_with_image "$env_file" "$image_ref"

if [ "$has_pull_auth" = "1" ]; then
  export DOCKER_CONFIG="$remote_docker_config"
fi

if ! docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" pull; then
  printf 'pull failed before activation\n' >"$marker_dir/last-failure-reason"
  capture_evidence "$marker_dir" "failed-pull"
  fail_remote "compose pull failed before activation; running service was not changed"
fi

if ! docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" up -d; then
  rollback_or_down "$marker_dir" "$previous_image_ref" "compose up failed after activation began"
fi

if ! health_check "$local_health_url"; then
  rollback_or_down "$marker_dir" "$previous_image_ref" "local health failed after activation"
fi

printf '%s\n' "$image_ref" >"$marker_dir/current-image-ref"
date -u +%Y-%m-%dT%H:%M:%SZ >"$marker_dir/last-deploy-at"
capture_evidence "$marker_dir" "deploy"
log_remote "deploy completed with IMAGE_REF=${image_ref}"
REMOTE_SCRIPT

log "deploy completed"
