#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[[ "${1:-}" == "--cutover" ]] || die "usage: deploy/scripts/bootstrap.sh --cutover"
validate_environment
require_command gzip

slim_volume="$(configured_volume_name SLIM_GUARD_DB_VOLUME slim-guard-prod-db)"
mem0_volume="$(configured_volume_name MEM0_DB_VOLUME slim-guard-prod-mem0-db)"
mem0_history_volume="$(configured_volume_name MEM0_HISTORY_VOLUME slim-guard-prod-mem0-history)"
if docker volume inspect slim-guard_slim_guard_postgres_data >/dev/null 2>&1; then
  [[ "$slim_volume" == "slim-guard_slim_guard_postgres_data" ]] \
    || die "set SLIM_GUARD_DB_VOLUME=slim-guard_slim_guard_postgres_data to preserve the current database"
fi
if docker volume inspect mem0-dev_postgres_db >/dev/null 2>&1; then
  [[ "$mem0_volume" == "mem0-dev_postgres_db" ]] \
    || die "set MEM0_DB_VOLUME=mem0-dev_postgres_db to preserve the current Mem0 database"
fi

for volume in "$slim_volume" "$mem0_volume" "$mem0_history_volume"; do
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    log "creating external data volume: $volume"
    docker volume create "$volume" >/dev/null
  fi
done

"$DEPLOY_DIR/scripts/build-mem0.sh"
release="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD)"
build_release_images "$release"

backup_dir="$(safe_backup_dir)/pre-unified-cutover-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

if docker inspect slim-guard-postgres-1 >/dev/null 2>&1; then
  log "backing up the current SlimGuard database before cutover"
  docker exec slim-guard-postgres-1 sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "$backup_dir/slim-guard.dump"
  test -s "$backup_dir/slim-guard.dump" || die "legacy SlimGuard database backup is empty"
fi
if docker inspect mem0-dev-postgres-1 >/dev/null 2>&1; then
  log "backing up the current Mem0 databases before cutover"
  docker exec mem0-dev-postgres-1 sh -c \
    'pg_dumpall -U "$POSTGRES_USER"' \
    | gzip -9 > "$backup_dir/mem0.sql.gz"
  test -s "$backup_dir/mem0.sql.gz" || die "legacy Mem0 database backup is empty"
fi
chmod 600 "$backup_dir"/* 2>/dev/null || true

legacy_containers=(
  slim-guard-app-1
  slim-guard-admin-web-1
  mem0-dev-mem0-1
  mem0-dev-mem0-dashboard-1
  slim-guard-postgres-1
  mem0-dev-postgres-1
)
stopped_containers=()

restore_legacy() {
  local exit_code=$?
  trap - EXIT
  if ((exit_code == 0)); then
    return
  fi
  log "cutover failed; stopping the unified stack and restoring legacy containers"
  compose down --remove-orphans >/dev/null 2>&1 || true
  local restore_order=(
    slim-guard-postgres-1
    mem0-dev-postgres-1
    mem0-dev-mem0-1
    mem0-dev-mem0-dashboard-1
    slim-guard-app-1
    slim-guard-admin-web-1
  )
  for container in "${restore_order[@]}"; do
    [[ " ${stopped_containers[*]} " == *" $container "* ]] || continue
    docker start "$container" >/dev/null 2>&1 || true
  done
  exit "$exit_code"
}
trap restore_legacy EXIT

log "stopping legacy application and database containers without deleting volumes"
for container in "${legacy_containers[@]}"; do
  if docker inspect "$container" >/dev/null 2>&1; then
    docker stop "$container" >/dev/null
    stopped_containers+=("$container")
  fi
done

legacy_history_dir="$(env_value MEM0_SOURCE_DIR)"
legacy_history_dir="${legacy_history_dir:-/home/ubuntu/mem0/server}/history"
if [[ -f "$legacy_history_dir/history.db" ]]; then
  log "preserving the legacy Mem0 change history"
  cp "$legacy_history_dir/history.db" "$backup_dir/mem0-history.db"
  chmod 600 "$backup_dir/mem0-history.db"
  mem0_image="$(env_value MEM0_IMAGE)"
  mem0_image="${mem0_image:-slim-guard/mem0:2.0.19-sg1}"
  if docker run --rm -v "$mem0_history_volume:/target" "$mem0_image" \
    sh -c 'test -z "$(find /target -mindepth 1 -maxdepth 1 -print -quit)"'; then
    docker run --rm \
      -v "$legacy_history_dir:/source:ro" \
      -v "$mem0_history_volume:/target" \
      "$mem0_image" sh -c 'cp -a /source/. /target/'
  else
    log "Mem0 history volume already contains data; leaving it unchanged"
  fi
fi

"$DEPLOY_DIR/scripts/deploy-release.sh" --skip-backup --skip-build

trap - EXIT
log "unified Compose cutover completed; legacy containers remain stopped for rollback safety"
log "pre-cutover backup: $backup_dir"
