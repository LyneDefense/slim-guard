#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

validate_environment
require_command gzip

backup_dir="$(safe_backup_dir)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
release="$(current_release)"
target_dir="$backup_dir/$timestamp-$release"
mkdir -p "$target_dir"
chmod 700 "$backup_dir" "$target_dir"

log "backing up SlimGuard PostgreSQL"
compose exec -T slim-guard-db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$target_dir/slim-guard.dump.partial"
test -s "$target_dir/slim-guard.dump.partial" || die "SlimGuard database backup is empty"
mv "$target_dir/slim-guard.dump.partial" "$target_dir/slim-guard.dump"

log "backing up Mem0 PostgreSQL databases"
compose exec -T mem0-db sh -c \
  'pg_dumpall -U "$POSTGRES_USER"' \
  | gzip -9 > "$target_dir/mem0.sql.gz.partial"
test -s "$target_dir/mem0.sql.gz.partial" || die "Mem0 database backup is empty"
mv "$target_dir/mem0.sql.gz.partial" "$target_dir/mem0.sql.gz"

log "backing up Mem0 change history"
compose exec -T mem0 python /opt/slim-guard/export-history.py \
  > "$target_dir/mem0-history.db.partial"
test -s "$target_dir/mem0-history.db.partial" || die "Mem0 history backup is empty"
mv "$target_dir/mem0-history.db.partial" "$target_dir/mem0-history.db"

printf '%s\n' "$release" > "$target_dir/release.txt"
chmod 600 "$target_dir"/*

retention_days="$(env_value BACKUP_RETENTION_DAYS)"
retention_days="${retention_days:-14}"
[[ "$retention_days" =~ ^[0-9]+$ ]] || die "BACKUP_RETENTION_DAYS must be an integer"
if ((retention_days > 0)); then
  find "$backup_dir" -mindepth 1 -maxdepth 1 -type d -mtime "+$retention_days" -exec rm -rf -- {} +
fi

log "backup completed: $target_dir"
