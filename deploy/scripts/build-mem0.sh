#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

validate_environment

mem0_source="$(env_value MEM0_SOURCE_DIR)"
mem0_source="${mem0_source:-/home/ubuntu/mem0/server}"
mem0_image="$(env_value MEM0_IMAGE)"
mem0_image="${mem0_image:-slim-guard/mem0:2.0.19-sg1}"
mem0ai_version="$(env_value MEM0AI_VERSION)"
mem0ai_version="${mem0ai_version:-2.0.19}"

[[ -f "$mem0_source/requirements.txt" ]] || die "Mem0 source is missing: $mem0_source/requirements.txt"
[[ -f "$mem0_source/main.py" ]] || die "Mem0 source is missing: $mem0_source/main.py"
[[ -f "$mem0_source/alembic.ini" ]] || die "Mem0 source is missing: $mem0_source/alembic.ini"
[[ -d "$mem0_source/alembic" ]] || die "Mem0 source is missing: $mem0_source/alembic"
[[ -d "$mem0_source/routers" ]] || die "Mem0 source is missing: $mem0_source/routers"
[[ -d "$mem0_source/scripts" ]] || die "Mem0 source is missing: $mem0_source/scripts"

safe_context="$(mktemp -d)"
cleanup_context() {
  [[ -n "$safe_context" && -d "$safe_context" && "$safe_context" != "/" ]] || return
  rm -rf -- "$safe_context"
}
trap cleanup_context EXIT

mkdir -p "$safe_context/alembic" "$safe_context/routers" "$safe_context/scripts"
cp "$mem0_source/requirements.txt" "$mem0_source/alembic.ini" "$safe_context/"
find "$mem0_source" -maxdepth 1 -type f -name '*.py' -exec cp {} "$safe_context/" \;
cp -a "$mem0_source/alembic/." "$safe_context/alembic/"
cp -a "$mem0_source/routers/." "$safe_context/routers/"
cp -a "$mem0_source/scripts/." "$safe_context/scripts/"

log "building pinned Mem0 image $mem0_image with mem0ai==$mem0ai_version"
docker build \
  --build-arg "MEM0AI_VERSION=$mem0ai_version" \
  -f "$DEPLOY_DIR/mem0/Dockerfile" \
  -t "$mem0_image" \
  "$safe_context"

installed_version="$(docker run --rm "$mem0_image" python -c 'import importlib.metadata; print(importlib.metadata.version("mem0ai"))')"
[[ "$installed_version" == "$mem0ai_version" ]] \
  || die "Mem0 image contains mem0ai==$installed_version, expected $mem0ai_version"

log "Mem0 image is ready: $mem0_image"
