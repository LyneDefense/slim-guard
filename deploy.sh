#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command="${1:-deploy}"

case "$command" in
  deploy)
    if [[ "${2:-}" != "--no-pull" ]]; then
      [[ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]] \
        || { printf 'working tree is not clean; refusing to deploy\n' >&2; exit 1; }
      git -C "$ROOT_DIR" pull --ff-only
    fi
    exec "$ROOT_DIR/deploy/scripts/deploy-release.sh"
    ;;
  bootstrap)
    [[ "${2:-}" == "--cutover" ]] \
      || { printf 'usage: ./deploy.sh bootstrap --cutover\n' >&2; exit 2; }
    exec "$ROOT_DIR/deploy/scripts/bootstrap.sh" --cutover
    ;;
  build-mem0)
    exec "$ROOT_DIR/deploy/scripts/build-mem0.sh"
    ;;
  cleanup-legacy)
    [[ "${2:-}" == "--confirm" ]] \
      || { printf 'usage: ./deploy.sh cleanup-legacy --confirm\n' >&2; exit 2; }
    exec "$ROOT_DIR/deploy/scripts/cleanup-legacy.sh" --confirm
    ;;
  status)
    source "$ROOT_DIR/deploy/scripts/lib.sh"
    validate_environment
    compose ps
    ;;
  logs)
    source "$ROOT_DIR/deploy/scripts/lib.sh"
    validate_environment
    compose logs --tail=200 -f app mem0
    ;;
  backup)
    exec "$ROOT_DIR/deploy/scripts/backup.sh"
    ;;
  rollback)
    exec "$ROOT_DIR/deploy/scripts/rollback.sh"
    ;;
  *)
    printf 'usage: ./deploy.sh [deploy [--no-pull]|bootstrap --cutover|build-mem0|cleanup-legacy --confirm|status|logs|backup|rollback]\n' >&2
    exit 2
    ;;
esac
