from __future__ import annotations

import hashlib

import pytest

from slim_guard.db.session import Database
from slim_guard.nutrition_knowledge import NutritionKnowledgeRepository
from slim_guard.nutrition_rag.gateways import EmbeddingBatch
from slim_guard.nutrition_rag.ingestion import (
    NutritionKnowledgeIngestionService,
    NutritionKnowledgeWorker,
    NutritionRemoteDocumentFetcher,
    NutritionRemoteFetchError,
)
from slim_guard.nutrition_rag.processing import (
    ChineseNutritionLexicalAnalyzer,
    NutritionDocumentParser,
    ParentChildNutritionChunker,
)
from slim_guard.nutrition_rag.repository import NutritionRagRepository
from slim_guard.nutrition_rag.storage import (
    InMemoryNutritionObjectStore,
    NutritionObjectIntegrityError,
)


class FakeEmbeddingGateway:
    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=tuple((0.01,) * 1024 for _ in texts),
            model="fake-embedding",
            request_id="embedding-request-1",
            prompt_tokens=len(texts),
            latency_ms=2,
        )

    async def close(self) -> None:
        return None


async def test_ingestion_review_release_and_activation_are_separate(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'nutrition-v2.sqlite3'}")
    await database.create_schema()
    store = InMemoryNutritionObjectStore()
    repository = NutritionRagRepository(database)
    raw = (
        "# 成年人减重饮食\n\n"
        "减重期间仍应保持食物多样，合理搭配蔬菜、全谷物和优质蛋白质。"
        "烹饪时注意少油、少盐、少糖，并根据个人情况调整。"
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    key = store.object_key(sha256=digest, filename="guide.md")
    stored = await store.put(
        key=key,
        content=raw,
        sha256=digest,
        media_type="text/markdown",
    )
    asset = await repository.create_asset(
        stored=stored,
        original_filename="guide.md",
        source_method="upload",
        source_url=None,
        created_by="admin",
    )
    job = await repository.enqueue_job(
        job_type="ingest",
        subject_type="asset",
        subject_id=asset.id,
        input={
            "asset_id": asset.id,
            "source_key": "adult-weight-guide",
            "version": "2026-v1",
            "title": "成年人减重饮食指南",
            "publisher": "测试机构",
            "language": "zh-CN",
            "tags": ["减重", "饮食搭配"],
            "applicability": ["adult", "china"],
        },
        idempotency_key=f"ingest:{asset.sha256}:adult-weight-guide:2026-v1",
        created_by="admin",
    )
    ingestion = NutritionKnowledgeIngestionService(
        repository=repository,
        object_store=store,
        embedding_gateway=FakeEmbeddingGateway(),
        max_asset_bytes=1024 * 1024,
        legacy_repository=NutritionKnowledgeRepository(database),
    )
    worker = NutritionKnowledgeWorker(
        ingestion=ingestion,
        worker_id="test-worker",
        poll_seconds=0.01,
        lease_seconds=60,
    )

    try:
        assert await worker.run_once() is True
        completed = await repository.get_job(job.id)
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.output is not None
        source_id = completed.output["source_id"]
        assert completed.output["embedding_count"] >= 1
        assert [event.stage for event in await repository.list_job_events(job.id)] == [
            "queued",
            "claimed",
            "downloading",
            "parsing",
            "embedding",
            "embedding",
            "review_ready",
            "completed",
        ]

        for review_type in ("content", "applicability", "rights"):
            state = await repository.append_source_review(
                source_id=source_id,
                review_type=review_type,
                decision="approve",
                actor="admin",
                attestations={"confirmed": True},
            )
        assert state.approved is True
        await repository.mark_source_approved(source_id)

        release = await repository.create_release(
            version="nutrition-corpus-2026-09-10-v1",
            source_ids=(source_id,),
            created_by="admin",
        )
        assert release.status == "review_ready"
        approved = await repository.review_release(
            release.id,
            decision="approve",
            actor="admin",
            reason=None,
        )
        assert approved.status == "approved"
        runtime = await repository.activate_release(
            release.id,
            actor="admin",
            reason="开发环境首次启用",
        )
        assert runtime.active_release_id == release.id
        active = await repository.get_active_profile()
        assert active is not None
        assert active[0].id == release.id
        assert active[1].dimensions == 1024
    finally:
        await database.close()


async def test_job_idempotency_and_required_reject_reason(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite3'}")
    await database.create_schema()
    repository = NutritionRagRepository(database)
    try:
        first = await repository.enqueue_job(
            job_type="ingest",
            subject_type="asset",
            subject_id="asset-1",
            input={"asset_id": "asset-1"},
            idempotency_key="same-import",
            created_by="admin",
        )
        second = await repository.enqueue_job(
            job_type="ingest",
            subject_type="asset",
            subject_id="asset-1",
            input={"asset_id": "asset-1"},
            idempotency_key="same-import",
            created_by="admin",
        )
        assert second.id == first.id
        with pytest.raises(ValueError, match="requires a reason"):
            await repository.append_source_review(
                source_id="missing",
                review_type="content",
                decision="reject",
                actor="admin",
                attestations={},
            )
    finally:
        await database.close()


def test_parser_chunker_and_chinese_analyzer_preserve_structure() -> None:
    document = NutritionDocumentParser().parse(
        content=(
            "<html><body><h1>减重建议</h1><p>成年人应保持食物多样，少油少盐。</p>"
            "<script>不要索引</script><h2>搭配</h2><p>搭配蔬菜和蛋白质。</p></body></html>"
        ).encode(),
        media_type="text/html",
        filename="guide.html",
    )
    analyzer = ChineseNutritionLexicalAnalyzer()
    chunks = ParentChildNutritionChunker(child_target_chars=128, child_max_chars=180).split(
        source_content_sha256=document.content_sha256,
        title="减重建议",
        sections=document.sections,
        analyzer=analyzer,
    )

    assert "不要索引" not in document.content
    assert [section.heading_path[-1] for section in document.sections] == ["减重建议", "搭配"]
    assert any(chunk.chunk_kind == "context_parent" for chunk in chunks)
    assert any(chunk.chunk_kind == "retrieval_child" for chunk in chunks)
    assert "食物多样" in " ".join(chunk.lexical_terms for chunk in chunks)


async def test_object_store_rejects_hash_mismatch_and_private_remote_url() -> None:
    store = InMemoryNutritionObjectStore()
    content = b"nutrition"
    digest = hashlib.sha256(content).hexdigest()
    key = store.object_key(sha256=digest, filename="note.txt")
    with pytest.raises(NutritionObjectIntegrityError):
        await store.put(
            key=key,
            content=b"tampered",
            sha256=digest,
            media_type="text/plain",
        )
    with pytest.raises(NutritionRemoteFetchError, match="private_address"):
        await NutritionRemoteDocumentFetcher._require_public_http_url("http://127.0.0.1/internal")
