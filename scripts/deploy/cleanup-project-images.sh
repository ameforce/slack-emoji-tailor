#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

log_file=

write_log() {
  printf '%s\n' "$*" >>"$log_file"
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

image_repository_from_ref() {
  local ref=$1
  local last_path
  case "$ref" in
    *@sha256:*)
      printf '%s\n' "${ref%@sha256:*}"
      return 0
      ;;
  esac
  last_path=${ref##*/}
  case "$last_path" in
    *:*) printf '%s\n' "${ref%:*}" ;;
    *) return 1 ;;
  esac
}

image_cleanup_tag_prefix() {
  local ref=$1
  local last_path tag
  case "$ref" in
    *@sha256:*) return 1 ;;
  esac
  last_path=${ref##*/}
  case "$last_path" in
    *:*) tag=${last_path##*:} ;;
    *) return 1 ;;
  esac
  case "$tag" in
    dev-*) printf 'dev-' ;;
    prod-*) printf 'prod-' ;;
    *) return 1 ;;
  esac
}

append_image_id_if_present() {
  local ref=$1
  local output_file=$2
  local image_id
  [ -n "$ref" ] || return 0
  image_id=$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)
  if [ -n "$image_id" ]; then
    printf '%s\n' "$image_id" >>"$output_file"
  fi
}

write_disabled_log() {
  local marker_dir=$1
  log_file="$marker_dir/image-cleanup.txt"
  printf 'project-scoped image cleanup disabled\n' >"$log_file"
}

main() {
  if [ "$#" -ne 5 ]; then
    printf '[image-cleanup] ERROR: expected 5 args, got %s\n' "$#" >&2
    return 2
  fi

  local marker_dir=$1
  local current_ref=$2
  local previous_ref=$3
  local image_cleanup_enabled=$4
  local image_retention_count=$5
  local repository tag_prefix candidates_file preserve_ids_file containers_file
  local candidate_ref candidate_id retained_count removed_count skipped_count

  mkdir -p "$marker_dir"
  log_file="$marker_dir/image-cleanup.txt"

  if ! truthy "$image_cleanup_enabled"; then
    write_disabled_log "$marker_dir"
    return 0
  fi

  case "$image_retention_count" in
    ""|*[!0-9]*)
      printf 'DEPLOY_IMAGE_RETENTION_COUNT must be numeric\n' >"$log_file"
      return 2
      ;;
  esac
  if [ "$image_retention_count" -lt 2 ]; then
    printf 'DEPLOY_IMAGE_RETENTION_COUNT must be at least 2\n' >"$log_file"
    return 2
  fi

  repository=$(image_repository_from_ref "$current_ref" || true)
  tag_prefix=$(image_cleanup_tag_prefix "$current_ref" || true)
  if [ -z "$repository" ] || [ -z "$tag_prefix" ]; then
    {
      printf 'project-scoped image cleanup skipped\n'
      printf 'reason=unsupported image ref for same-environment tag cleanup\n'
      printf 'image_ref=%s\n' "$current_ref"
    } >"$log_file"
    return 0
  fi

  candidates_file=$(mktemp "$marker_dir/image-cleanup-candidates.XXXXXX")
  preserve_ids_file=$(mktemp "$marker_dir/image-cleanup-preserve-ids.XXXXXX")
  containers_file=$(mktemp "$marker_dir/image-cleanup-containers.XXXXXX")
  : >"$preserve_ids_file"

  {
    printf 'project-scoped image cleanup\n'
    printf 'repository=%s\n' "$repository"
    printf 'same_environment_tag_prefix=%s\n' "$tag_prefix"
    printf 'retention_count=%s\n' "$image_retention_count"
    printf 'preserves=current-image-ref, previous-image-ref, running container image IDs, and recent image tags\n'
  } >"$log_file"

  if ! docker image ls "$repository" --format '{{.Repository}}:{{.Tag}}' >"$candidates_file.raw" 2>>"$log_file"; then
    write_log 'docker image ls failed; cleanup skipped'
    rm -f "$candidates_file" "$candidates_file.raw" "$preserve_ids_file" "$containers_file"
    return 0
  fi
  while IFS= read -r candidate_ref; do
    case "$candidate_ref" in
      "$repository:$tag_prefix"*) printf '%s\n' "$candidate_ref" ;;
    esac
  done <"$candidates_file.raw" >"$candidates_file"
  rm -f "$candidates_file.raw"

  append_image_id_if_present "$current_ref" "$preserve_ids_file"
  append_image_id_if_present "$previous_ref" "$preserve_ids_file"

  if docker ps -q >"$containers_file" 2>>"$log_file"; then
    while IFS= read -r container_id; do
      [ -n "$container_id" ] || continue
      docker inspect --format '{{.Image}}' "$container_id" >>"$preserve_ids_file" 2>>"$log_file" || true
    done <"$containers_file"
  else
    write_log 'docker ps failed; running-container preservation could not be expanded'
  fi

  retained_count=0
  while IFS= read -r candidate_ref; do
    [ -n "$candidate_ref" ] || continue
    append_image_id_if_present "$candidate_ref" "$preserve_ids_file"
    retained_count=$((retained_count + 1))
    [ "$retained_count" -ge "$image_retention_count" ] && break
  done <"$candidates_file"

  removed_count=0
  skipped_count=0
  while IFS= read -r candidate_ref; do
    [ -n "$candidate_ref" ] || continue
    if [ "$candidate_ref" = "$current_ref" ] || [ "$candidate_ref" = "$previous_ref" ]; then
      skipped_count=$((skipped_count + 1))
      continue
    fi
    candidate_id=$(docker image inspect --format '{{.Id}}' "$candidate_ref" 2>>"$log_file" || true)
    if [ -z "$candidate_id" ] || grep -Fxq "$candidate_id" "$preserve_ids_file"; then
      skipped_count=$((skipped_count + 1))
      continue
    fi
    if docker image rm "$candidate_ref" >>"$log_file" 2>&1; then
      removed_count=$((removed_count + 1))
    else
      skipped_count=$((skipped_count + 1))
    fi
  done <"$candidates_file"

  {
    printf 'removed_tag_count=%s\n' "$removed_count"
    printf 'skipped_tag_count=%s\n' "$skipped_count"
  } >>"$log_file"
  rm -f "$candidates_file" "$preserve_ids_file" "$containers_file"
}

main "$@"
