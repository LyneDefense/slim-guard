from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from slim_guard.agent_models.embeddings import EmbeddingBatch
from slim_guard.agents.nutrition import KnowledgeCandidateBinder
from slim_guard.db.models import NutritionRetrievalRunRecord
from slim_guard.db.session import Database
from slim_guard.nutrition_knowledge import (
    KnowledgeDocument,
    KnowledgeMetadataFilter,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
)
from slim_guard.nutrition_rag.answerability import (
    AnswerabilityDocument,
    AnswerabilityResult,
)
from slim_guard.nutrition_rag.evaluation import NutritionEvaluationService
from slim_guard.nutrition_rag.gateways import (
    RerankItem,
    RerankResult,
)
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
from slim_guard.nutrition_rag.profiles import ANSWERABILITY_MODE
from slim_guard.nutrition_rag.repository import (
    NutritionRagGovernanceError,
    NutritionRagRepository,
)
from slim_guard.nutrition_rag.retrieval import (
    HybridNutritionRagService,
    _answerability_exclusion_reason,
    _FusedHit,
)
from slim_guard.nutrition_rag.storage import (
    InMemoryNutritionObjectStore,
    NutritionObjectIntegrityError,
)


class FakeEmbeddingGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        return EmbeddingBatch(
            vectors=tuple((0.01,) * 1024 for _ in texts),
            model="fake-embedding",
            request_id="embedding-request-1",
            prompt_tokens=len(texts),
            latency_ms=2,
        )

    async def close(self) -> None:
        return None


class FakeRerankGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> RerankResult:
        self.calls += 1
        assert query
        base_score = 0.1 if "无结果" in query else 0.9
        return RerankResult(
            items=tuple(
                RerankItem(index=index, score=base_score - (index * 0.01))
                for index in range(min(top_n, len(documents)))
            ),
            model="fake-rerank",
            request_id="rerank-request-1",
            prompt_tokens=len(documents),
            latency_ms=1,
        )

    async def close(self) -> None:
        return None


class FakeAnswerabilityGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def assess(
        self,
        *,
        query: str,
        documents: Sequence[AnswerabilityDocument],
        mode: str = ANSWERABILITY_MODE,
    ) -> AnswerabilityResult:
        del mode
        self.calls += 1
        insufficient = "无结果" in query or "资料没有直接答案" in query
        return AnswerabilityResult(
            outcome="insufficient" if insufficient else "supported",
            supported_document_indices=() if insufficient else tuple(range(len(documents))),
            reason_code="missing_requested_fact" if insufficient else "directly_supported",
            model="fake-answerability",
            request_id="answerability-request-1",
            input_tokens=len(documents),
            output_tokens=1,
        )


def test_answerability_exclusion_reason_distinguishes_unselected_from_unsupported() -> None:
    supported = AnswerabilityResult(
        outcome="supported",
        supported_document_indices=(0,),
        reason_code="directly_supported",
        model="fake-answerability",
        request_id="supported-request",
        input_tokens=2,
        output_tokens=1,
    )
    insufficient = AnswerabilityResult(
        outcome="insufficient",
        supported_document_indices=(),
        reason_code="missing_requested_fact",
        model="fake-answerability",
        request_id="insufficient-request",
        input_tokens=2,
        output_tokens=1,
    )

    assert _answerability_exclusion_reason(supported) == "answerability_not_selected"
    assert _answerability_exclusion_reason(insufficient) == "answerability_missing_requested_fact"


async def test_ingestion_review_release_and_activation_are_separate(tmp_path: Path) -> None:
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
        assert release.status == "indexing_check"
        release = await repository.complete_release_index_check(release.id)
        assert release.status == "evaluating"
        cases = tuple(
            {
                "case_key": f"positive-{index}",
                "query_plan_input": {
                    "query": f"成年人减重 食物多样 {index}",
                    "metadata_filter": {"applicability": ["adult", "china"]},
                },
                "expected_source_keys": ["adult-weight-guide"],
                "expected_chunk_concepts": ["食物多样"],
                "forbidden_source_keys": [],
                "expected_outcome": "evidence",
            }
            for index in range(75)
        ) + tuple(
            {
                "case_key": f"negative-{index}",
                "query_plan_input": {
                    "query": f"无结果 火箭发动机 {index}",
                    "metadata_filter": {"applicability": ["adult", "china"]},
                },
                "expected_source_keys": [],
                "expected_chunk_concepts": [],
                "forbidden_source_keys": [],
                "expected_outcome": "insufficient",
            }
            for index in range(25)
        )
        dataset = await repository.create_evaluation_dataset(
            version="nutrition-eval-v1",
            cases=cases,
            created_by="admin",
        )
        assert dataset.status == "ready"
        run_id, evaluation_job = await repository.create_evaluation_run(
            release_id=release.id,
            dataset_id=dataset.id,
            created_by="admin",
            idempotency_key="first-release-evaluation",
        )
        duplicate_run_id, duplicate_job = await repository.create_evaluation_run(
            release_id=release.id,
            dataset_id=dataset.id,
            created_by="admin",
            idempotency_key="rapid-second-click-with-another-key",
        )
        assert duplicate_run_id == run_id
        assert duplicate_job.id == evaluation_job.id
        retrieval = HybridNutritionRagService(
            repository=repository,
            embedding_gateway=FakeEmbeddingGateway(),
            rerank_gateway=FakeRerankGateway(),
            answerability_gateway=FakeAnswerabilityGateway(),
        )
        evaluation_worker = NutritionKnowledgeWorker(
            ingestion=ingestion,
            worker_id="evaluation-worker",
            poll_seconds=0.01,
            lease_seconds=60,
            evaluation=NutritionEvaluationService(
                repository=repository,
                retrieval=retrieval,
            ),
        )
        assert await evaluation_worker.run_once() is True
        evaluation_job_result = await repository.get_job(evaluation_job.id)
        assert evaluation_job_result is not None
        assert evaluation_job_result.status == "succeeded"
        evaluation_run = await repository.get_evaluation_run(run_id)
        assert evaluation_run is not None
        assert evaluation_run["metrics"]["gates_passed"] is True
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
        snapshot = await retrieval.get_runtime_snapshot()
        assert snapshot is not None
        assert snapshot.corpus_release_id == release.id
        assert snapshot.corpus_release_version == "nutrition-corpus-2026-09-10-v1"
        assert snapshot.corpus_manifest_sha256 == release.manifest_sha256
        assert snapshot.retrieval_profile_id == active[1].id
    finally:
        await database.close()


async def test_job_idempotency_and_required_reject_reason(tmp_path: Path) -> None:
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
        legacy = NutritionKnowledgeService(NutritionKnowledgeRepository(database))
        imported = await legacy.import_documents(
            (
                KnowledgeDocument(
                    source_key="not-indexed",
                    version="v1",
                    title="尚未索引的资料",
                    publisher="测试机构",
                    content="这是尚未建立检索切片的营养资料。",
                ),
            ),
            imported_by="admin",
        )
        source_id = imported.documents[0].source.id
        for review_type in ("content", "applicability", "rights"):
            await repository.append_source_review(
                source_id=source_id,
                review_type=review_type,
                decision="approve",
                actor="admin",
                attestations={"confirmed": True},
            )
        with pytest.raises(NutritionRagGovernanceError, match="indexing has not completed"):
            await repository.mark_source_approved(source_id)
    finally:
        await database.close()


async def test_hybrid_retrieval_filters_before_search_and_returns_receipt(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retrieval.sqlite3'}")
    await database.create_schema()
    store = InMemoryNutritionObjectStore()
    repository = NutritionRagRepository(database)
    embeddings = FakeEmbeddingGateway()
    raw = (
        "# 烹饪与搭配\n\n"
        "减重期间可以吃番茄炒蛋。建议搭配蔬菜和全谷物，烹饪时控制用油和盐，"
        "不需要因为减重而完全禁食普通菜品。"
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    stored = await store.put(
        key=store.object_key(sha256=digest, filename="meal.md"),
        content=raw,
        sha256=digest,
        media_type="text/markdown",
    )
    asset = await repository.create_asset(
        stored=stored,
        original_filename="meal.md",
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
            "source_key": "meal-composition",
            "version": "v1",
            "title": "减重餐食搭配",
            "publisher": "测试机构",
            "tags": ["减重", "烹饪方式"],
            "applicability": ["adult", "china"],
        },
        idempotency_key="ingest-meal-v1",
        created_by="admin",
    )
    worker = NutritionKnowledgeWorker(
        ingestion=NutritionKnowledgeIngestionService(
            repository=repository,
            object_store=store,
            embedding_gateway=embeddings,
            max_asset_bytes=1024 * 1024,
            legacy_repository=NutritionKnowledgeRepository(database),
        ),
        worker_id="test-worker",
        poll_seconds=0.01,
        lease_seconds=60,
    )
    try:
        await worker.run_once()
        completed = await repository.get_job(job.id)
        assert completed is not None and completed.output is not None
        source_id = completed.output["source_id"]
        for review_type in ("content", "applicability", "rights"):
            await repository.append_source_review(
                source_id=source_id,
                review_type=review_type,
                decision="approve",
                actor="admin",
                attestations={"confirmed": True},
            )
        await repository.mark_source_approved(source_id)
        release = await repository.create_release(
            version="nutrition-test-v1",
            source_ids=(source_id,),
            created_by="admin",
        )
        await repository.complete_release_index_check(release.id)
        reranker = FakeRerankGateway()
        service = HybridNutritionRagService(
            repository=repository,
            embedding_gateway=embeddings,
            rerank_gateway=reranker,
            answerability_gateway=FakeAnswerabilityGateway(),
        )

        # SQLite otherwise skips the foreign-key checks enforced in production.
        async with database.engine.connect() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
            await connection.commit()

        raw_result = await service.search(
            query="番茄炒蛋 减重 少油 搭配",
            max_results=3,
            metadata_filter={"applicability": ["adult", "china"]},
            retrieved_in_invocation_id="nutrition-invocation-1",
            release_id=release.id,
        )
        bound = KnowledgeCandidateBinder().bind_search_result(
            invocation_id="nutrition-invocation-1",
            result=raw_result,
        )
        assert raw_result["retrieval_run_id"]
        persisted_run = await repository.get_retrieval_run(str(raw_result["retrieval_run_id"]))
        assert persisted_run is not None
        assert persisted_run["invocation_id"] == "nutrition-invocation-1"
        assert persisted_run["release_id"] == release.id
        assert bound.corpus_status.value == "available"
        assert len(bound.citations) == 1
        assert "番茄炒蛋" in bound.candidates[0].content
        assert reranker.calls == 1

        unsupported = await service.search(
            query="资料没有直接答案，但主题仍然与减重相关",
            max_results=3,
            metadata_filter={"applicability": ["adult", "china"]},
            release_id=release.id,
        )
        assert unsupported["candidates"]
        assert all(
            candidate["adoption_status"] == "candidate_only"
            for candidate in unsupported["candidates"]
        )
        assert "answerability=insufficient" in unsupported["query_summary"]

        lab_result = await service.search(
            query="晚餐可以吃番茄炒蛋吗",
            max_results=3,
            metadata_filter={"applicability": ["adult", "china"]},
            retrieved_in_invocation_id="admin-lab-reviewer",
            release_id=release.id,
        )
        lab_dataset = await repository.create_evaluation_dataset_from_lab_run(
            run_id=str(lab_result["retrieval_run_id"]),
            base_dataset_id=None,
            version="lab-regressions-v1",
            case={
                "case_key": "tomato-eggs-dinner",
                "expected_source_keys": ["meal-composition"],
                "expected_chunk_concepts": ["番茄炒蛋"],
                "forbidden_source_keys": [],
                "expected_outcome": "evidence",
            },
            created_by="admin",
        )
        assert lab_dataset.status == "draft"
        assert lab_dataset.cases[0].query_plan_input["query"] == "晚餐可以吃番茄炒蛋吗"
        assert lab_dataset.cases[0].query_plan_input["metadata_filter"]["applicability"] == [
            "adult",
            "china",
        ]
        with pytest.raises(NutritionRagGovernanceError, match="admin retrieval-lab run"):
            await repository.create_evaluation_dataset_from_lab_run(
                run_id=str(raw_result["retrieval_run_id"]),
                base_dataset_id=None,
                version="must-not-copy-user-query",
                case={
                    "case_key": "unsafe-copy",
                    "expected_outcome": "insufficient",
                },
                created_by="admin",
            )

        model_calls = embeddings.calls
        rerank_calls = reranker.calls
        excluded = await service.search(
            query="番茄炒蛋",
            max_results=3,
            metadata_filter={"applicability": ["child"]},
            release_id=release.id,
        )
        assert excluded["candidates"] == []
        assert embeddings.calls == model_calls
        assert reranker.calls == rerank_calls
        source = await service.get_source(source_id=source_id)
        assert source["eligibility"]["active"] is False

        # Flushing the parent must not commit it independently of the candidates.
        profile = await repository.get_release_profile(release.id)
        assert profile is not None
        async with database.session() as session:
            run_count = await session.scalar(select(func.count(NutritionRetrievalRunRecord.id)))
        with pytest.raises(IntegrityError):
            await service._record_run(
                release=release,
                profile=profile[1],
                query="事务回滚回归",
                filters=KnowledgeMetadataFilter(),
                status="succeeded",
                started=time.monotonic(),
                usage={},
                hits=(_FusedHit(chunk_id="nonexistent-chunk"),),
                invocation_id="rollback-regression",
            )
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count(NutritionRetrievalRunRecord.id)))
                == run_count
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
