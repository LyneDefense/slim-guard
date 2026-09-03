#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

validate_environment
[[ -s "$STATE_DIR/previous-release" ]] || die "no previous release is recorded"

current="$(current_release)"
previous="$(cat "$STATE_DIR/previous-release")"
app_repository="$(env_value SLIM_GUARD_APP_IMAGE_REPOSITORY)"
app_repository="${app_repository:-slim-guard/app}"
admin_repository="$(env_value SLIM_GUARD_ADMIN_IMAGE_REPOSITORY)"
admin_repository="${admin_repository:-slim-guard/admin-web}"

docker image inspect "$app_repository:$previous" >/dev/null 2>&1 \
  || die "missing rollback image: $app_repository:$previous"
docker image inspect "$admin_repository:$previous" >/dev/null 2>&1 \
  || die "missing rollback image: $admin_repository:$previous"

log "rolling application containers back from $current to $previous"
RELEASE_TAG_OVERRIDE="$previous"
export RELEASE_TAG_OVERRIDE
compose up -d app admin-web
wait_for_healthy app
wait_for_healthy admin-web
"$DEPLOY_DIR/scripts/smoke-test.sh"

printf '%s\n' "$current" > "$STATE_DIR/previous-release"
printf '%s\n' "$previous" > "$STATE_DIR/current-release"
log "rollback completed; database migrations were intentionally not reversed"
