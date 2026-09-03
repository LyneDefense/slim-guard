from __future__ import annotations

import json
import os
import urllib.request


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


api_key = required("OPENAI_API_KEY")
admin_key = required("ADMIN_API_KEY")
base_url = required("ZHIPU_BASE_URL").rstrip("/")
dimensions = int(required("MEM0_EMBEDDING_DIMS"))

payload = {
    "llm": {
        "provider": "openai",
        "config": {
            "api_key": api_key,
            "model": required("MEM0_LLM_MODEL"),
            "openai_base_url": base_url,
            "temperature": 0.2,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "api_key": api_key,
            "model": required("MEM0_EMBEDDER_MODEL"),
            "openai_base_url": base_url,
            "embedding_dims": dimensions,
        },
    },
    "vector_store": {
        "provider": "pgvector",
        "config": {"embedding_model_dims": dimensions},
    },
}

request = urllib.request.Request(
    "http://127.0.0.1:8000/configure",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-API-Key": admin_key,
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    if response.status != 200:
        raise RuntimeError(f"Mem0 configure returned HTTP {response.status}")
print("Mem0 provider configuration applied successfully")
