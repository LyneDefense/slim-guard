#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
ENV_FILE="${SLIM_GUARD_SERVER_ENV_FILE:-$DEPLOY_DIR/.env.server}"
COMPOSE_FILE="$DEPLOY_DIR/compose.production.yaml"
STATE_DIR="$DEPLOY_DIR/.state"
PROJECT_NAME="slim-guard-prod"

log() {
  printf '[slim-guard-deploy] %s\n' "$*"
}

die() {
  printf '[slim-guard-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

env_value() {
  local key="$1"
  awk -v wanted="$key" '
    /^[[:space:]]*(#|$)/ { next }
    {
      separator = index($0, "=")
      if (separator == 0) next
      name = substr($0, 1, separator - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == wanted) value = substr($0, separator + 1)
    }
    END {
      sub(/\r$/, "", value)
      print value
    }
  ' "$ENV_FILE"
}

validate_unique_env_keys() {
  local duplicates
  duplicates="$({
    awk '
      /^[[:space:]]*(#|$)/ { next }
      {
        separator = index($0, "=")
        if (separator == 0) next
        name = substr($0, 1, separator - 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
        if (++seen[name] == 2) print name
      }
    ' "$ENV_FILE"
  } | sort)"
  [[ -z "$duplicates" ]] || die "duplicate keys in $ENV_FILE: $duplicates"
}

require_env_value() {
  local key="$1"
  local minimum_length="${2:-1}"
  local value
  value="$(env_value "$key")"
  [[ -n "$value" ]] || die "$key is missing or empty in $ENV_FILE"
  [[ "$value" != "CHANGE_ME" ]] || die "$key still contains CHANGE_ME in $ENV_FILE"
  ((${#value} >= minimum_length)) || die "$key must contain at least $minimum_length characters"
}

validate_environment() {
  require_command docker
  require_command curl
  require_command git
  require_command awk
  [[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE; copy deploy/env.server.example and fill it first"
  [[ -f "$COMPOSE_FILE" ]] || die "missing production Compose file: $COMPOSE_FILE"
  validate_unique_env_keys

  require_env_value SLIM_GUARD_POSTGRES_PASSWORD 16
  require_env_value MEM0_POSTGRES_PASSWORD 16
  require_env_value MEM0_API_KEY 16
  require_env_value MEM0_JWT_SECRET 32
  require_env_value ZHIPU_API_KEY 8
  require_env_value ADMIN_USERNAME
  require_env_value ADMIN_PASSWORD 6

  if [[ "$(env_value MOBILE_API_ENABLED)" == "true" ]]; then
    require_env_value MOBILE_AUTH_SECRET 32
  fi

  local dimensions
  dimensions="$(env_value MEM0_EMBEDDING_DIMS)"
  dimensions="${dimensions:-1024}"
  [[ "$dimensions" =~ ^[0-9]+$ ]] || die "MEM0_EMBEDDING_DIMS must be an integer"

  local env_permissions
  env_permissions="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)"
  if [[ -n "$env_permissions" && "$env_permissions" != "600" ]]; then
    die "$ENV_FILE permissions are $env_permissions; run: chmod 600 $ENV_FILE"
  fi

  RELEASE_TAG_OVERRIDE="${RELEASE_TAG_OVERRIDE:-validation}" compose config --quiet
}

current_release() {
  if [[ -s "$STATE_DIR/current-release" ]]; then
    cat "$STATE_DIR/current-release"
  else
    printf 'development\n'
  fi
}

build_release_images() {
  local release="$1"
  local app_repository
  local admin_repository
  app_repository="$(env_value SLIM_GUARD_APP_IMAGE_REPOSITORY)"
  app_repository="${app_repository:-slim-guard/app}"
  admin_repository="$(env_value SLIM_GUARD_ADMIN_IMAGE_REPOSITORY)"
  admin_repository="${admin_repository:-slim-guard/admin-web}"

  log "building SlimGuard release $release while the current containers stay online"
  docker build -t "$app_repository:$release" "$ROOT_DIR"
  docker build -t "$admin_repository:$release" "$ROOT_DIR/frontend"
  docker image inspect "$app_repository:$release" "$admin_repository:$release" >/dev/null
}

configured_volume_name() {
  local key="$1"
  local fallback="$2"
  local value
  value="$(env_value "$key")"
  printf '%s\n' "${value:-$fallback}"
}

require_data_volumes() {
  local slim_volume
  local mem0_volume
  local mem0_history_volume
  slim_volume="$(configured_volume_name SLIM_GUARD_DB_VOLUME slim-guard-prod-db)"
  mem0_volume="$(configured_volume_name MEM0_DB_VOLUME slim-guard-prod-mem0-db)"
  mem0_history_volume="$(configured_volume_name MEM0_HISTORY_VOLUME slim-guard-prod-mem0-history)"
  docker volume inspect "$slim_volume" >/dev/null 2>&1 \
    || die "database volume is missing: $slim_volume; run ./deploy.sh bootstrap --cutover"
  docker volume inspect "$mem0_volume" >/dev/null 2>&1 \
    || die "Mem0 database volume is missing: $mem0_volume; run ./deploy.sh bootstrap --cutover"
  docker volume inspect "$mem0_history_volume" >/dev/null 2>&1 \
    || die "Mem0 history volume is missing: $mem0_history_volume; run ./deploy.sh bootstrap --cutover"
}

compose() {
  local release="${RELEASE_TAG_OVERRIDE:-$(current_release)}"
  RELEASE_TAG="$release" \
    SLIM_GUARD_ENV_FILE="$ENV_FILE" \
    docker compose \
      --project-name "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      "$@"
}

wait_for_healthy() {
  local service="$1"
  local attempts="${2:-60}"
  local container_id
  local status

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    container_id="$(compose ps -q "$service")"
    if [[ -n "$container_id" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
      if [[ "$status" == "healthy" || "$status" == "running" ]]; then
        log "$service is $status"
        return 0
      fi
      if [[ "$status" == "unhealthy" || "$status" == "exited" || "$status" == "dead" ]]; then
        compose logs --tail=120 "$service" >&2 || true
        die "$service entered terminal state: $status"
      fi
    fi
    sleep 2
  done

  compose logs --tail=120 "$service" >&2 || true
  die "$service did not become healthy in time"
}

safe_backup_dir() {
  local backup_dir
  backup_dir="$(env_value BACKUP_DIR)"
  [[ -n "$backup_dir" ]] || backup_dir="/home/ubuntu/backups/slim-guard"
  [[ "$backup_dir" == /* ]] || die "BACKUP_DIR must be an absolute path"
  [[ "$backup_dir" != "/" && "$backup_dir" != "/home" && "$backup_dir" != "/home/ubuntu" ]] \
    || die "BACKUP_DIR is too broad: $backup_dir"
  printf '%s\n' "$backup_dir"
}
