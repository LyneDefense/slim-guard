#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[[ "${1:-}" == "--confirm" ]] || die "usage: ./deploy.sh cleanup-legacy --confirm"
validate_environment

log "verifying the unified stack before removing stopped legacy containers"
"$DEPLOY_DIR/scripts/smoke-test.sh"

legacy_containers=(
  slim-guard-app-1
  slim-guard-admin-web-1
  mem0-dev-mem0-1
  mem0-dev-mem0-dashboard-1
  slim-guard-postgres-1
  mem0-dev-postgres-1
)

for container in "${legacy_containers[@]}"; do
  docker inspect "$container" >/dev/null 2>&1 || continue
  running="$(docker inspect --format '{{.State.Running}}' "$container")"
  [[ "$running" == "false" ]] \
    || die "legacy container is still running, refusing cleanup: $container"
done

for container in "${legacy_containers[@]}"; do
  docker inspect "$container" >/dev/null 2>&1 || continue
  log "removing stopped legacy container: $container"
  docker rm "$container" >/dev/null
done

legacy_networks=(slim-guard_default mem0-dev_mem0_network slim-memory)
for network in "${legacy_networks[@]}"; do
  docker network inspect "$network" >/dev/null 2>&1 || continue
  if docker network rm "$network" >/dev/null 2>&1; then
    log "removed unused legacy network: $network"
  else
    log "kept legacy network because another endpoint still uses it: $network"
  fi
done

mkdir -p "$STATE_DIR"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE_DIR/legacy-cleaned-at"
log "legacy cleanup completed; database volumes and source directories were preserved"
