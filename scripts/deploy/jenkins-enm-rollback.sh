#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

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
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"
}

require_env DEPLOY_HOST
require_env DEPLOY_SSH_USER
require_env DEPLOY_PATH

DEPLOY_COMPOSE_FILE=${DEPLOY_COMPOSE_FILE:-docker-compose.dev.deploy.yml}
DEPLOY_ENV_FILE=${DEPLOY_ENV_FILE:-.env.dev}
DEPLOY_COMPOSE_PROJECT=${DEPLOY_COMPOSE_PROJECT:-slack-emoji-tailor-dev}
APP_HOST_PORT=${APP_HOST_PORT:-${DEPLOY_APP_PORT:-18082}}
LOCAL_HEALTHCHECK_URL=${LOCAL_HEALTHCHECK_URL:-http://127.0.0.1:${APP_HOST_PORT}/healthz}
DEPLOY_MARKER_DIR=${DEPLOY_MARKER_DIR:-.deploy-state}
DEPLOY_LOCK_FILE=${DEPLOY_LOCK_FILE:-${DEPLOY_PATH}/.deploy.lock}
ROLLBACK_IMAGE_REF=${ROLLBACK_IMAGE_REF:-}
REMOTE_DOCKER_CONFIG=${REMOTE_DOCKER_CONFIG:-${DEPLOY_PATH}/.docker-config-pull}
REGISTRY_URL=${REGISTRY_URL:-}
REGISTRY_PULL_USERNAME=${REGISTRY_PULL_USERNAME:-}
REGISTRY_PULL_PASSWORD=${REGISTRY_PULL_PASSWORD:-}
SKIP_IMAGE_PULL=${SKIP_IMAGE_PULL:-false}
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
  rollback image override: ${ROLLBACK_IMAGE_REF:-<read previous marker on host>}
  local health: ${LOCAL_HEALTHCHECK_URL}
  public health: ${PUBLIC_HEALTHCHECK_URL:-<expected as separate Jenkins public check>}
  lock file: ${DEPLOY_LOCK_FILE}
  marker dir: ${DEPLOY_MARKER_DIR}
  skip image pull: ${SKIP_IMAGE_PULL}
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

if [ "$HAS_PULL_PASSWORD" = "1" ]; then
  log "performing remote registry pull login with isolated Docker config"
  login_cmd="set -euo pipefail; mkdir -p $(shell_quote "$REMOTE_DOCKER_CONFIG"); chmod 700 $(shell_quote "$REMOTE_DOCKER_CONFIG"); docker --config $(shell_quote "$REMOTE_DOCKER_CONFIG") login $(shell_quote "$REGISTRY_URL") --username $(shell_quote "$REGISTRY_PULL_USERNAME") --password-stdin >/dev/null"
  printf '%s\n' "$REGISTRY_PULL_PASSWORD" | remote_run "$login_cmd"
fi

run_public_health() {
  local url=$1
  local retries=$2
  local sleep_seconds=$3
  local timeout_seconds=$4
  local attempt output_file
  [ -n "$url" ] || return 0
  output_file=$(mktemp)
  log "checking public health from Jenkins agent: ${url}"
  for attempt in $(seq 1 "$retries"); do
    if curl --fail --show-error --silent --location --max-time "$timeout_seconds" "$url" >"$output_file"; then
      log "public health passed on attempt ${attempt}: $(cat "$output_file")"
      rm -f "$output_file"
      return 0
    fi
    log "public health attempt ${attempt}/${retries} failed"
    sleep "$sleep_seconds"
  done
  rm -f "$output_file"
  return 1
}

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
  "$REMOTE_DOCKER_CONFIG"
  "$HEALTH_RETRIES"
  "$HEALTH_SLEEP_SECONDS"
  "$HEALTH_TIMEOUT_SECONDS"
  "$HAS_PULL_PASSWORD"
  "$SKIP_IMAGE_PULL"
)
quoted_remote_args=()
for arg in "${remote_args[@]}"; do
  quoted_remote_args+=("$(shell_quote "$arg")")
done

log "starting remote rollback on ${SSH_TARGET}"
remote_run "bash -s -- ${quoted_remote_args[*]}" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

if [ "$#" -ne 15 ]; then
  printf '[rollback:remote] ERROR: expected 15 args, got %s\n' "$#" >&2
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
remote_docker_config=${10}
health_retries=${11}
health_sleep_seconds=${12}
health_timeout_seconds=${13}
has_pull_auth=${14}
skip_image_pull=${15}

log_remote() {
  printf '[rollback:remote] %s\n' "$*" >&2
}

fail_remote() {
  log_remote "ERROR: $*"
  exit 1
}

truthy_remote() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

is_immutable_image_ref() {
  local ref=$1
  local last_path tag
  case "$ref" in
    *@sha256:*) return 0 ;;
    ""|latest|dev-latest|prod-latest) return 1 ;;
  esac
  last_path=${ref##*/}
  case "$last_path" in
    *:*)
      tag=${last_path##*:}
      case "$tag" in
        ""|latest|dev-latest|prod-latest) return 1 ;;
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

mkdir -p "$deploy_path"
touch "$lock_file"
exec 9>"$lock_file"
flock 9

cd "$deploy_path"
[ -f "$compose_file" ] || fail_remote "compose file not found: ${deploy_path}/${compose_file}"

marker_dir=$marker_dir_name
case "$marker_dir" in
  /*) ;;
  *) marker_dir="${deploy_path}/${marker_dir}" ;;
esac
mkdir -p "$marker_dir"

rollback_image_ref=${rollback_image_override:-}
if [ -z "$rollback_image_ref" ]; then
  rollback_image_ref=$(read_first_file \
    "$marker_dir/previous-image-ref" \
    "$marker_dir/previous_image_ref" \
    "$deploy_path/.previous-image-ref" \
    "$deploy_path/.previous-image" \
  ) || true
fi
if [ "$rollback_image_ref" = "NO_PREVIOUS_IMAGE_AVAILABLE" ]; then
  rollback_image_ref=
fi
[ -n "$rollback_image_ref" ] || fail_remote "NO_PREVIOUS_IMAGE_AVAILABLE"
is_immutable_image_ref "$rollback_image_ref" || fail_remote "rollback IMAGE_REF is not immutable: ${rollback_image_ref}"

current_image_ref=$(read_first_file "$marker_dir/current-image-ref" "$marker_dir/current_image_ref" || true)
if [ -z "$current_image_ref" ]; then
  current_image_ref=$(read_env_image_ref "$env_file" || true)
fi

write_env_with_image "$env_file" "$rollback_image_ref"
if [ "$has_pull_auth" = "1" ]; then
  export DOCKER_CONFIG="$remote_docker_config"
fi

if truthy_remote "$skip_image_pull"; then
  docker image inspect "$rollback_image_ref" >/dev/null
else
  docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" pull
fi
docker compose -p "$compose_project" -f "$compose_file" --env-file "$env_file" up -d
if ! health_check "$local_health_url"; then
  capture_evidence "$marker_dir" "failed-rollback"
  fail_remote "rollback local health failed; evidence written to ${marker_dir}"
fi

printf '%s\n' "$rollback_image_ref" >"$marker_dir/current-image-ref"
printf '%s\n' "$rollback_image_ref" >"$marker_dir/previous-image-ref"
if [ -n "${current_image_ref:-}" ]; then
  printf '%s\n' "$current_image_ref" >"$marker_dir/rolled-back-from-image-ref"
fi
date -u +%Y-%m-%dT%H:%M:%SZ >"$marker_dir/last-rollback-at"
capture_evidence "$marker_dir" "rollback"
log_remote "rollback restored IMAGE_REF=${rollback_image_ref}"
REMOTE_SCRIPT

if [ -n "$PUBLIC_HEALTHCHECK_URL" ]; then
  run_public_health "$PUBLIC_HEALTHCHECK_URL" "$PUBLIC_HEALTH_RETRIES" "$PUBLIC_HEALTH_SLEEP_SECONDS" "$PUBLIC_HEALTH_TIMEOUT_SECONDS" || fail "public health failed after rollback"
else
  log "PUBLIC_HEALTHCHECK_URL not set; Jenkins pipeline must run the external public health stage separately"
fi

log "rollback completed"
