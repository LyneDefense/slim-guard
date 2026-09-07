from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select

from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.nutrition.contracts import KnowledgeCandidate
from slim_guard.agents.nutrition.knowledge import KnowledgeCandidateBinder
from slim_guard.db.models import (
    NutritionKnowledgeChunkRecord,
    NutritionKnowledgeImportBatchRecord,
    NutritionKnowledgeReviewRecord,
    NutritionKnowledgeSourceRecord,
)
from slim_guard.db.session import Database
from slim_guard.nutrition_knowledge import (
    DeterministicKnowledgeChunker,
    KnowledgeDocument,
    KnowledgeMetadataFilter,
    KnowledgeSourceConflict,
    NutritionKnowledgeRepository,
    NutritionKnowledgeService,
    VectorCandidateInput,
)


def document(
    *,
    source_key: str = "guideline",
    version: str = "v1",
    content: str = "成年人膳食应当保持食物多样，并结合个人情况安排蛋白质来源。",
    publisher: str = "权威机构",
    applicability: tuple[str, ...] = ("adult",),
) -> KnowledgeDocument:
    return KnowledgeDocument(
        source_key=source_key,
        version=version,
        title="成年人膳食指南",
        publisher=publisher,
        published_at=date(2026, 1, 1),
        source_url="https://example.org/guideline",
        content=content,
        tags=("nutrition",),
        applicability=applicability,
    )


def test_chunking_is_bounded_and_deterministic() -> None:
    content = (
        "第一段介绍均衡饮食。第二段介绍蛋白质选择！"
        "第三段说明需要结合个人情况？第四段提供一般性建议。"
    )
    chunker = DeterministicKnowledgeChunker(max_chars=64, min_chars=16)

    first = chunker.split(content)
    second = chunker.split(content)

    assert first == second
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert all(len(chunk.content) <= 64 for chunk in first)
    assert all(
        hashlib.sha256(chunk.content.encode()).hexdigest() == chunk.content_sha256
        for chunk in first
    )


def test_admin_knowledge_view_separates_candidates_and_adopted_without_content() -> None:
    citation = {
        "candidate_id": "candidate-1",
        "citation_id": "citation-1",
        "source_id": "source-1",
        "chunk_id": "chunk-1",
        "title": "公开指南标题",
        "publisher": "权威机构",
        "version": "v1",
        "applicability": ["adult"],
        "review_status": "approved",
        "active": True,
        "content": "不应默认展示的知识片段",
        "content_sha256": "a" * 64,
        "keyword_score": 0.8,
        "vector_score": 0.1,
        "rerank_score": 0.835,
        "match_reasons": ["content_token_match"],
        "adoption_status": "adopted",
    }

    view = AdminQueryRepository._safe_knowledge(
        {
            "corpus_status": "available",
            "candidates": [citation, {**citation, "candidate_id": "candidate-2"}],
            "adopted_citations": [citation],
            "citations": [citation],
            "query_summary": "sensitive query",
        }
    )

    assert view is not None
    assert len(view["candidates"]) == 2
    assert len(view["adopted_citations"]) == 1
    assert view["candidates"][0]["title"] == "公开指南标题"
    assert view["candidates"][0]["applicability"] == ["adult"]
    assert view["candidates"][0]["content"] is None
    assert view["query_summary"] is None
    assert "不应默认展示的知识片段" not in str(view)


async def test_migration_creates_governed_knowledge_tables_without_user_namespace(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'knowledge-schema.sqlite3'}")
    try:
        await database.create_schema()
        async with database.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            source_columns = await connection.run_sync(
                lambda sync: {
                    column["name"]
                    for column in inspect(sync).get_columns("nutrition_knowledge_sources")
                }
            )

        assert {
            "nutrition_knowledge_import_batches",
            "nutrition_knowledge_sources",
            "nutrition_knowledge_chunks",
            "nutrition_knowledge_reviews",
        }.issubset(table_names)
        assert "user_id" not in source_columns
        assert "namespace" not in source_columns
    finally:
        await database.close()


async def test_import_deduplicates_content_and_marks_conflicting_version_failed(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'knowledge-import.sqlite3'}")
    await database.create_schema()
    repository = NutritionKnowledgeRepository(database)
    service = NutritionKnowledgeService(repository)
    imported_at = datetime(2026, 9, 7, tzinfo=UTC)
    try:
        result = await service.import_documents(
            (
                document(),
                document(source_key="same-content", version="2026"),
            ),
            imported_by="offline-importer",
            created_at=imported_at,
        )

        assert result.batch.status == "completed"
        assert result.batch.source_count == 1
        assert result.batch.duplicate_count == 1
        assert result.documents[0].created is True
        assert result.documents[1].created is False
        assert result.documents[1].duplicate_of_source_id == result.documents[0].source.id
        async with database.session() as session:
            assert await session.scalar(select(func.count(NutritionKnowledgeSourceRecord.id))) == 1
            assert await session.scalar(select(func.count(NutritionKnowledgeChunkRecord.id))) >= 1

        with pytest.raises(KnowledgeSourceConflict):
            await service.import_documents(
                (document(content="同一版本却试图写入不同内容。"),),
                imported_by="offline-importer",
                created_at=imported_at + timedelta(minutes=1),
            )
        async with database.session() as session:
            failed = await session.scalar(
                select(NutritionKnowledgeImportBatchRecord)
                .where(NutritionKnowledgeImportBatchRecord.status == "failed")
                .order_by(NutritionKnowledgeImportBatchRecord.created_at.desc())
            )
            assert failed is not None
            assert failed.failure_code == "KnowledgeSourceConflict"
    finally:
        await database.close()


async def test_only_published_sources_are_retrieved_but_retired_source_is_auditable(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'knowledge-review.sqlite3'}")
    await database.create_schema()
    repository = NutritionKnowledgeRepository(database)
    service = NutritionKnowledgeService(repository)
    base = datetime(2026, 9, 7, tzinfo=UTC)
    try:
        imported = await service.import_documents(
            (document(),),
            imported_by="importer",
            created_at=base,
        )
        source_id = imported.documents[0].source.id

        draft_result = await service.search(query="蛋白质", max_results=3)
        assert draft_result["corpus_status"] == "empty"
        assert draft_result["candidates"] == []
        draft_source = await service.get_source(source_id=source_id)
        assert draft_source["source"] is None
        assert draft_source["eligibility"]["publication_status"] == "draft"

        await service.approve_source(
            source_id,
            reviewer="reviewer",
            reason="来源与版本已核验",
            decided_at=base + timedelta(minutes=1),
        )
        await service.publish_source(
            source_id,
            reviewer="publisher",
            decided_at=base + timedelta(minutes=2),
        )
        published_result = await service.search(query="蛋白质", max_results=3)
        assert published_result["corpus_status"] == "available"
        assert published_result["candidates"]
        candidate = KnowledgeCandidate.model_validate(published_result["candidates"][0])
        assert candidate.active is True
        assert candidate.review_status == "approved"
        assert candidate.applicability == ("adult",)
        assert candidate.content_sha256 == hashlib.sha256(candidate.content.encode()).hexdigest()
        published_source = await service.get_source(
            source_id=source_id,
            chunk_id=candidate.chunk_id,
        )
        assert "content" not in published_source["source"]
        assert published_source["chunk"]["content"] == candidate.content
        assert published_source["chunk"]["content_sha256"] == candidate.content_sha256
        bound = KnowledgeCandidateBinder().bind_search_result(
            invocation_id="invocation-1",
            result=published_result,
        )
        assert bound.citations[0].retrieved_in_invocation_id == "invocation-1"

        await service.retire_source(
            source_id,
            reviewer="publisher",
            reason="已由新版本替代",
            decided_at=base + timedelta(minutes=3),
        )
        retired_result = await service.search(query="蛋白质", max_results=3)
        assert retired_result["corpus_status"] == "empty"
        assert retired_result["candidates"] == []
        historical = await repository.get_source(source_id)
        assert historical is not None
        assert historical.status == "retired"
        source_view = await service.get_source(source_id=source_id)
        assert source_view["source"] is None
        assert source_view["eligibility"]["active"] is False
        assert source_view["eligibility"]["publication_status"] == "retired"
        assert "膳食" not in str(source_view)
        assert [review.decision for review in await repository.list_reviews(source_id)] == [
            "approve",
            "publish",
            "retire",
        ]
        async with database.session() as session:
            assert await session.scalar(select(func.count(NutritionKnowledgeReviewRecord.id))) == 3
    finally:
        await database.close()


async def test_metadata_filter_and_vector_candidates_are_rechecked_against_database(
    tmp_path,
) -> None:
    class FixedVectorScorer:
        seen: tuple[VectorCandidateInput, ...] = ()

        async def candidate_scores(
            self,
            query: str,
            *,
            candidates: tuple[VectorCandidateInput, ...],
            limit: int,
        ) -> dict[str, float]:
            del query, limit
            self.seen = candidates
            return {
                candidates[-1].chunk_id: 0.9,
                "untrusted-or-retired-chunk": 1.0,
            }

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'knowledge-search.sqlite3'}")
    await database.create_schema()
    repository = NutritionKnowledgeRepository(database)
    vector = FixedVectorScorer()
    service = NutritionKnowledgeService(repository, vector_scorer=vector)
    now = datetime(2026, 9, 7, tzinfo=UTC)
    try:
        imported = await service.import_documents(
            (
                document(
                    source_key="adult-source",
                    content="成年人需要食物多样。",
                    applicability=("adult",),
                ),
                document(
                    source_key="athlete-source",
                    content="运动人群需要结合训练安排饮食。",
                    applicability=("athlete",),
                ),
            ),
            imported_by="importer",
            created_at=now,
        )
        for index, imported_document in enumerate(imported.documents):
            await service.approve_source(
                imported_document.source.id,
                reviewer="reviewer",
                decided_at=now + timedelta(minutes=index + 1),
            )
            await service.publish_source(
                imported_document.source.id,
                reviewer="publisher",
                decided_at=now + timedelta(minutes=index + 3),
            )

        result = await service.search(
            query="没有词法匹配的 omega",
            max_results=2,
            metadata_filter=KnowledgeMetadataFilter(applicability=("athlete",)),
        )
        repeated = await service.search(
            query="没有词法匹配的 omega",
            max_results=2,
            metadata_filter=KnowledgeMetadataFilter(applicability=("athlete",)),
        )

        assert len(vector.seen) == 1
        assert result["candidates"][0]["vector_score"] == 0.9
        assert result["candidates"][0]["applicability"] == ["athlete"]
        assert [
            (candidate["candidate_id"], candidate["rank"], candidate["rerank_score"])
            for candidate in result["candidates"]
        ] == [
            (candidate["candidate_id"], candidate["rank"], candidate["rerank_score"])
            for candidate in repeated["candidates"]
        ]
        assert all(
            candidate["chunk_id"] != "untrusted-or-retired-chunk"
            for candidate in result["candidates"]
        )
    finally:
        await database.close()
