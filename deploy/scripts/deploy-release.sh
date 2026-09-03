#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

skip_backup=false
skip_build=false
for option in "$@"; do
  case "$option" in
    --skip-backup) skip_backup=true ;;
    --skip-build) skip_build=true ;;
    *) die "unknown deploy-release option: $option" ;;
  esac
done

mkdir -p "$STATE_DIR"
require_command flock
exec 9>"$STATE_DIR/deploy.lock"
flock -n 9 || die "another deployment is already running"

validate_environment
require_data_volumes

release="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD)"
previous_release="$(current_release)"
application_update_started=false
mem0_image="$(env_value MEM0_IMAGE)"
mem0_image="${mem0_image:-slim-guard/mem0:2.0.19-sg1}"

restore_previous_application() {
  local exit_code=$?
  trap - EXIT
  if ((exit_code == 0)); then
    return
  fi
  if [[ "$application_update_started" == true && "$previous_release" != "development" ]]; then
    log "deployment failed after the application update started; restoring release $previous_release"
    RELEASE_TAG_OVERRIDE="$previous_release"
    export RELEASE_TAG_OVERRIDE
    compose up -d app admin-web >/dev/null 2>&1 || true
    wait_for_healthy app 30 >/dev/null 2>&1 || true
    wait_for_healthy admin-web 30 >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap restore_previous_application EXIT

docker image inspect "$mem0_image" >/dev/null 2>&1 \
  || die "Mem0 image $mem0_image is missing; run ./deploy.sh bootstrap --cutover first"

if [[ "$skip_build" == false ]]; then
  build_release_images "$release"
fi

RELEASE_TAG_OVERRIDE="$release"
export RELEASE_TAG_OVERRIDE
compose config --quiet

if [[ "$skip_backup" == false && -n "$(compose ps -q slim-guard-db)" ]]; then
  "$DEPLOY_DIR/scripts/backup.sh"
fi

log "starting data and memory services"
compose up -d slim-guard-db mem0-db mem0
wait_for_healthy slim-guard-db
wait_for_healthy mem0-db
wait_for_healthy mem0

log "applying pinned Mem0 provider configuration"
compose exec -T mem0 python /opt/slim-guard/configure.py

log "applying SlimGuard database migrations"
compose run --rm app python -m slim_guard.db.migrate

log "starting application services"
application_update_started=true
compose up -d app admin-web
wait_for_healthy app
wait_for_healthy admin-web

"$DEPLOY_DIR/scripts/smoke-test.sh"

if [[ "$previous_release" != "development" && "$previous_release" != "$release" ]]; then
  printf '%s\n' "$previous_release" > "$STATE_DIR/previous-release"
fi
printf '%s\n' "$release" > "$STATE_DIR/current-release"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/deployed-at"

trap - EXIT
log "deployment completed: $release"
