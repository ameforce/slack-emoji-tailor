#!/usr/bin/env bash
set -Eeuo pipefail

# Jenkins-called rollback for the dev deployment. The script restores the
# previous registry image recorded on the target host, updates the remote
# compose env under a host-side lock, restarts the compose project, verifies
# server-local health, and optionally verifies the public health URL from the
# Jenkins agent that runs this script.

log() {
  printf '[rollback] %s\n' "$*" >&2
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
  # Quote a value for use inside a single remote shell command.
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"
}

require_env DEPLOY_HOST
require_env DEPLOY_SSH_USER
require_env DEPLOY_PATH

DEPLOY_COMPOSE_FILE=${DEPLOY_COMPOSE_FILE:-docker-compose.dev.deploy.yml}
DEPLOY_ENV_FILE=${DEPLOY_ENV_FILE:-.env.dev}
DEPLOY_COMPOSE_PROJECT=${DEPLOY_COMPOSE_PROJECT:-slack-emoji-tailor-dev}
APP_HOST_PORT=${APP_HOST_PORT:-18082}
LOCAL_HEALTHCHECK_URL=${LOCAL_HEALTHCHECK_URL:-http://127.0.0.1:${APP_HOST_PORT}/healthz}
DEPLOY_MARKER_DIR=${DEPLOY_MARKER_DIR:-.deploy-state}
DEPLOY_LOCK_FILE=${DEPLOY_LOCK_FILE:-${DEPLOY_PATH}/.deploy.lock}
ROLLBACK_IMAGE_REF=${ROLLBACK_IMAGE_REF:-}
REGISTRY_URL=${REGISTRY_URL:-}
REGISTRY_PULL_USERNAME=${REGISTRY_PULL_USERNAME:-}
REGISTRY_PULL_PASSWORD=${REGISTRY_PULL_PASSWORD:-}
REMOTE_DOCKER_CONFIG=${REMOTE_DOCKER_CONFIG:-${DEPLOY_PATH}/.docker-config-rollback}
HEALTH_RETRIES=${HEALTH_RETRIES:-12}
HEALTH_SLEEP_SECONDS=${HEALTH_SLEEP_SECONDS:-5}
HEALTH_TIMEOUT_SECONDS=${HEALTH_TIMEOUT_SECONDS:-5}
PUBLIC_HEALTHCHECK_URL=${PUBLIC_HEALTHCHECK_URL:-}
PUBLIC_HEALTH_RETRIES=${PUBLIC_HEALTH_RETRIES:-6}
PUBLIC_HEALTH_SLEEP_SECONDS=${PUBLIC_HEALTH_SLEEP_SECONDS:-5}
PUBLIC_HEALTH_TIMEOUT_SECONDS=${PUBLIC_HEALTH_TIMEOUT_SECONDS:-15}
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
Rollback dry-run preview:
  target: ${SSH_TARGET}
  deploy path: ${DEPLOY_PATH}
  compose file: ${DEPLOY_COMPOSE_FILE}
  env file: ${DEPLOY_ENV_FILE}
  compose project: ${DEPLOY_COMPOSE_PROJECT}
  lock file: ${DEPLOY_LOCK_FILE}
  marker dir: ${DEPLOY_MARKER_DIR}
  rollback image override: ${ROLLBACK_IMAGE_REF:-<read previous marker on host>}
  local health: ${LOCAL_HEALTHCHECK_URL}
  public health: ${PUBLIC_HEALTHCHECK_URL:-<expected as separate Jenkins public check>}
DRYRUN
  exit 0
fi

run_public_health() {
  local url=$1
  local retries=$2
  local sleep_seconds=$3
  local timeout_seconds=$4
  local attempt

  [ -n "$url" ] || return 0
  log "checking public health from Jenkins agent: ${url}"
  for attempt in $(seq 1 "$retries"); do
    if curl --fail --show-error --silent --location --max-time "$timeout_seconds" "$url" >/tmp/slack-emoji-tailor-public-health.out; then
      log "public health passed on attempt ${attempt}: $(cat /tmp/slack-emoji-tailor-public-health.out)"
      rm -f /tmp/slack-emoji-tailor-public-health.out
      return 0
    fi
    log "public health attempt ${attempt}/${retries} failed"
    sleep "$sleep_seconds"
  done
  rm -f /tmp/slack-emoji-tailor-public-health.out
  return 1
}

remote_command=(ssh)
if [ -n "$SSH_OPTS" ]; then
  # Intentionally allow Jenkins to provide OpenSSH options as a single string.
  # shellcheck disable=SC2206
  extra_ssh_opts=($SSH_OPTS)
  remote_command+=("${extra_ssh_opts[@]}")
fi
remote_command+=("$SSH_TARGET")

remote_args=(
  "$DEPLOY_PATH"
  "$DEPLOY_COMPOSE_FILE"
  "$DEPLOY_ENV_FILE"
  "$DEPLOY_COMPOSE_PROJECT"
  "$APP_HOST_PORT"
  "$LOCAL_HEALTHCHECK_URL"
  "$DEPLOY_MARKER_DIR"
  "$DEPLOY_LOCK_FILE"
  "$ROLLBACK_IMAGE_REF"
  "$REGISTRY_URL"
  "$REGISTRY_PULL_USERNAME"
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

log "starting remote rollback on ${SSH_TARGET}"
{
  cat <<'REMOTE_SCRIPT'
set -Eeuo pipefail

if [ "$#" -ne 16 ]; then
  printf '[rollback:remote] ERROR: expected 16 args, got %s\n' "$#" >&2
  exit 2
fi

deploy_path=$1
compose_file=$2
env_file=$3
compose_project=$4
app_host_port=$5
local_health_url=$6
marker_dir_name=$7
lock_file=$8
rollback_image_override=$9
registry_url=${10}
registry_pull_username=${11}
remote_docker_config=${12}
health_retries=${13}
health_sleep_seconds=${14}
health_timeout_seconds=${15}
has_pull_password=${16}
registry_pull_password=

if [ "$has_pull_password" = "1" ]; then
  IFS= read -r registry_pull_password
fi

log_remote() {
  printf '[rollback:remote] %s\n' "$*" >&2
}

fail_remote() {
  log_remote "ERROR: $*"
  exit 1
}

is_immutable_image_ref() {
  case "$1" in
    *@sha256:*) return 0 ;;
    *:latest|*:dev-latest|latest|dev-latest|"") return 1 ;;
    *:*) return 0 ;;
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

write_env_with_image() {
  local env_path=$1
  local image_ref=$2
  local tmp_path
  tmp_path=$(mktemp "${env_path}.tmp.XXXXXX")
  if [ -f "$env_path" ]; then
    grep -v '^IMAGE_REF=' "$env_path" >"$tmp_path" || true
  fi
  if ! grep -q '^APP_HOST_PORT=' "$tmp_path" 2>/dev/null; then
    printf 'APP_HOST_PORT=%s\n' "$app_host_port" >>"$tmp_path"
  fi
  printf 'IMAGE_REF=%s\n' "$image_ref" >>"$tmp_path"
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

mkdir -p "$deploy_path"
touch "$lock_file"

flock "$lock_file" bash -s <<'LOCKED_REMOTE'
set -Eeuo pipefail

log_remote() {
  printf '[rollback:remote] %s\n' "$*" >&2
}

fail_remote() {
  log_remote "ERROR: $*"
  exit 1
}

is_immutable_image_ref() {
  case "$1" in
    *@sha256:*) return 0 ;;
    *:latest|*:dev-latest|latest|dev-latest|"") return 1 ;;
    *:*) return 0 ;;
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

write_env_with_image() {
  local env_path=$1
  local image_ref=$2
  local tmp_path
  tmp_path=$(mktemp "${env_path}.tmp.XXXXXX")
  if [ -f "$env_path" ]; then
    grep -v '^IMAGE_REF=' "$env_path" >"$tmp_path" || true
  fi
  if ! grep -q '^APP_HOST_PORT=' "$tmp_path" 2>/dev/null; then
    printf 'APP_HOST_PORT=%s\n' "$app_host_port" >>"$tmp_path"
  fi
  printf 'IMAGE_REF=%s\n' "$image_ref" >>"$tmp_path"
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

cd "$deploy_path"
[ -f "$compose_file" ] || fail_remote "compose file not found: ${deploy_path}/${compose_file}"

marker_dir=$marker_dir_name
case "$marker_dir" in
  /*) ;;
  *) marker_dir="${deploy_path}/${marker_dir}" ;;
esac
mkdir -p "$marker_dir"

previous_image_ref=${rollback_image_override:-}
if [ -z "$previous_image_ref" ]; then
  previous_image_ref=$(read_first_file \
    "$marker_dir/previous-image-ref" \
    "$marker_dir/previous_image_ref" \
    "$deploy_path/.previous-image-ref" \
    "$deploy_path/.previous-image" \
  ) || true
fi

[ -n "$previous_image_ref" ] || fail_remote "NO_PREVIOUS_IMAGE_AVAILABLE"
is_immutable_image_ref "$previous_image_ref" || fail_remote "previous IMAGE_REF is not immutable: ${previous_image_ref}"

current_image_ref=$(read_first_file "$marker_dir/current-image-ref" "$marker_dir/current_image_ref" || true)
if [ -z "$current_image_ref" ] && [ -f "$env_file" ]; then
  current_image_ref=$(awk -F= '$1 == "IMAGE_REF" {print substr($0, index($0,"=")+1); exit}' "$env_file")
fi

write_env_with_image "$env_file" "$previous_image_ref"

if [ "$has_pull_password" = "1" ]; then
  [ -n "$registry_url" ] || fail_remote "registry URL is required for remote pull login"
  [ -n "$registry_pull_username" ] || fail_remote "registry pull username is required for remote pull login"
  mkdir -p "$remote_docker_config"
  chmod 700 "$remote_docker_config"
  printf '%s\n' "$registry_pull_password" | docker --config "$remote_docker_config" login "$registry_url" --username "$registry_pull_username" --password-stdin >/dev/null
  export DOCKER_CONFIG="$remote_docker_config"
fi

docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" pull
docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" up -d

if ! health_check "$local_health_url"; then
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" ps >"$marker_dir/last-rollback-ps.txt" 2>&1 || true
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" logs --tail=120 >"$marker_dir/last-rollback-logs.txt" 2>&1 || true
  fail_remote "rollback local health failed; evidence written to ${marker_dir}"
fi

printf '%s\n' "$previous_image_ref" >"$marker_dir/current-image-ref"
printf '%s\n' "$previous_image_ref" >"$marker_dir/previous-image-ref"
if [ -n "${current_image_ref:-}" ]; then
  printf '%s\n' "$current_image_ref" >"$marker_dir/rolled-back-from-image-ref"
fi
date -u +%Y-%m-%dT%H:%M:%SZ >"$marker_dir/last-rollback-at"
docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" ps | tee "$marker_dir/last-rollback-ps.txt"
log_remote "rollback restored IMAGE_REF=${previous_image_ref}"
LOCKED_REMOTE
REMOTE_SCRIPT
  if [ "$HAS_PULL_PASSWORD" = "1" ]; then
    printf '%s\n' "$REGISTRY_PULL_PASSWORD"
  fi
} | "${remote_command[@]}" "bash -s -- ${quoted_remote_args[*]}"

if [ -n "$PUBLIC_HEALTHCHECK_URL" ]; then
  run_public_health "$PUBLIC_HEALTHCHECK_URL" "$PUBLIC_HEALTH_RETRIES" "$PUBLIC_HEALTH_SLEEP_SECONDS" "$PUBLIC_HEALTH_TIMEOUT_SECONDS" || fail "public health failed after rollback"
else
  log "PUBLIC_HEALTHCHECK_URL not set; Jenkins pipeline must run the external public health stage separately"
fi

log "rollback completed"
