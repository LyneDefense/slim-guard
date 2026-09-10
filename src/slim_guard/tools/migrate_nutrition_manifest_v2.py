"""Queue normalized legacy nutrition documents through the production COS pipeline.

This is a one-time compatibility entry point. It does not approve, release, or activate
the imported sources; those governance actions remain in the admin console.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.nutrition_rag.repository import NutritionRagRepository
from slim_guard.nutrition_rag.storage import TencentCosNutritionObjectStore


class LegacyManifestDocument(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    source_key: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    content_path: str = Field(min_length=1, max_length=1000)
    published_at: str | None = None
    source_url: HttpUrl | None = None
    language: str = Field(default="zh-CN", min_length=1, max_length=32)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a legacy nutrition manifest to COS and queue v2 indexing jobs.",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--actor", required=True)
    return parser


def _documents(path: Path) -> tuple[tuple[LegacyManifestDocument, Path], ...]:
    manifest_path = path.expanduser().resolve(strict=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("Legacy manifest must contain a non-empty documents array")
    root = manifest_path.parent
    documents: list[tuple[LegacyManifestDocument, Path]] = []
    for raw in raw_documents:
        document = LegacyManifestDocument.model_validate(raw)
        content_path = (root / document.content_path).resolve(strict=True)
        if not content_path.is_relative_to(root) or not content_path.is_file():
            raise ValueError("Legacy manifest content_path must be a file below the manifest")
        documents.append((document, content_path))
    return tuple(documents)


async def migrate(
    *,
    manifest: Path,
    actor: str,
    settings: Settings,
) -> tuple[dict[str, Any], ...]:
    if not settings.tencent_cos_is_configured:
        raise ValueError("Tencent COS configuration is required")
    database = Database(settings.database_url)
    store = TencentCosNutritionObjectStore(
        region=settings.tencent_cos_region,
        bucket=settings.tencent_cos_bucket,
        prefix=settings.tencent_cos_prefix,
        secret_id=settings.tencent_cos_secret_id,
        secret_key=settings.tencent_cos_secret_key,
        session_token=settings.tencent_cos_session_token,
        domain=settings.tencent_cos_domain,
    )
    try:
        await database.create_schema()
        repository = NutritionRagRepository(database)
        results: list[dict[str, Any]] = []
        for document, content_path in _documents(manifest):
            content = content_path.read_bytes()
            if not content or len(content) > settings.nutrition_knowledge_max_upload_bytes:
                raise ValueError(f"Legacy document is empty or too large: {content_path}")
            digest = hashlib.sha256(content).hexdigest()
            key = store.object_key(sha256=digest, filename=content_path.name)
            stored = await store.put(
                key=key,
                content=content,
                sha256=digest,
                media_type="text/markdown",
            )
            asset = await repository.create_asset(
                stored=stored,
                original_filename=content_path.name,
                source_method="legacy_manifest",
                source_url=str(document.source_url) if document.source_url else None,
                created_by=actor,
            )
            job = await repository.enqueue_job(
                job_type="ingest",
                subject_type="asset",
                subject_id=asset.id,
                input={
                    "asset_id": asset.id,
                    "source_key": document.source_key,
                    "version": document.version,
                    "title": document.title,
                    "publisher": document.publisher,
                    "published_at": document.published_at,
                    "source_url": str(document.source_url) if document.source_url else None,
                    "language": document.language,
                    "tags": list(document.tags),
                    "applicability": list(document.applicability),
                },
                idempotency_key=(f"ingest:{digest}:{document.source_key}:{document.version}")[:256],
                created_by=actor,
            )
            results.append(
                {
                    "source_key": document.source_key,
                    "version": document.version,
                    "asset": asdict(asset),
                    "job": asdict(job),
                }
            )
        return tuple(results)
    finally:
        await database.close()


async def _run(arguments: argparse.Namespace) -> int:
    result = await migrate(
        manifest=arguments.manifest,
        actor=arguments.actor,
        settings=Settings(),
    )
    print(json.dumps({"items": result}, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
