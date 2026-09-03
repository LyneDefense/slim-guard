#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

validate_environment

app_port="$(env_value APP_HOST_PORT)"
app_port="${app_port:-18083}"
admin_port="$(env_value ADMIN_WEB_HOST_PORT)"
admin_port="${admin_port:-18084}"

log "checking databases and internal services"
compose exec -T slim-guard-db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
compose exec -T mem0-db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
compose exec -T app python -c \
  'import os, urllib.request; request=urllib.request.Request("http://mem0:8000/auth/setup-status", headers={"X-API-Key": os.environ["MEM0_API_KEY"]}); print(urllib.request.urlopen(request, timeout=10).status)'

log "checking host loopback endpoints"
curl --fail --silent --show-error "http://127.0.0.1:$app_port/health/live" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$app_port/health/ready" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$admin_port/" >/dev/null

if [[ "$(env_value MOBILE_API_ENABLED)" == "true" ]]; then
  curl --fail --silent --show-error \
    "http://127.0.0.1:$app_port/api/mobile/v1/auth/options" >/dev/null
fi

public_base_url="$(env_value PUBLIC_BASE_URL)"
if [[ -n "$public_base_url" ]]; then
  public_base_url="${public_base_url%/}"
  log "checking public Nginx route: $public_base_url"
  curl --fail --silent --show-error "$public_base_url/health/live" >/dev/null
  if [[ "$(env_value MOBILE_API_ENABLED)" == "true" ]]; then
    curl --fail --silent --show-error \
      "$public_base_url/api/mobile/v1/auth/options" >/dev/null
  fi
fi

log "all smoke tests passed"
