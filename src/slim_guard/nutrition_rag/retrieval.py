from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import exists, func, literal, or_, select

from slim_guard.agent_models.embeddings import EmbeddingError, EmbeddingGateway
from slim_guard.db.models import (
    NutritionChunkEmbeddingRecord,
    NutritionCorpusReleaseSourceRecord,
    NutritionKnowledgeSectionRecord,
    NutritionKnowledgeSourceLabelRecord,
    NutritionKnowledgeSourceRecord,
    NutritionRagChunkRecord,
    NutritionRetrievalCandidateRecord,
    NutritionRetrievalRunRecord,
    new_uuid,
)
from slim_guard.nutrition_knowledge import KnowledgeMetadataFilter
from slim_guard.nutrition_rag.answerability import (
    AnswerabilityDocument,
    AnswerabilityGateway,
    AnswerabilityResult,
)
from slim_guard.nutrition_rag.contracts import NutritionRuntimeSnapshot
from slim_guard.nutrition_rag.gateways import (
    NutritionModelGatewayError,
    RerankGateway,
)
from slim_guard.nutrition_rag.processing import ChineseNutritionLexicalAnalyzer
from slim_guard.nutrition_rag.profiles import ANSWERABILITY_MODE, ANSWERABILITY_MODE_V1
from slim_guard.nutrition_rag.repository import (
    NutritionRagRepository,
    NutritionRelease,
    RetrievalProfile,
)


@dataclass(frozen=True, slots=True)
class _ChannelHit:
    chunk_id: str
    rank: int
    score: float


@dataclass(slots=True)
class _FusedHit:
    chunk_id: str
    dense_rank: int | None = None
    dense_score: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    phrase_rank: int | None = None
    phrase_score: float | None = None
    rrf_rank: int = 0
    rrf_score: float = 0.0
    rerank_rank: int | None = None
    rerank_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    selected: bool = False
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _LoadedChunk:
    chunk: NutritionRagChunkRecord
    parent: NutritionRagChunkRecord | None
    section: NutritionKnowledgeSectionRecord
    source: NutritionKnowledgeSourceRecord
    applicability: tuple[str, ...]

    @property
    def context(self) -> str:
        return self.parent.content_text if self.parent is not None else self.chunk.content_text


def _answerability_exclusion_reason(result: AnswerabilityResult) -> str:
    """Describe why a candidate was excluded without mislabeling the group decision."""
    if result.outcome == "supported":
        return "answerability_not_selected"
    return f"answerability_{result.reason_code}"


class HybridNutritionRagService:
    """Filter-first Hybrid RAG over an explicitly active corpus release."""

    def __init__(
        self,
        *,
        repository: NutritionRagRepository,
        embedding_gateway: EmbeddingGateway,
        rerank_gateway: RerankGateway,
        answerability_gateway: AnswerabilityGateway | None = None,
        analyzer: ChineseNutritionLexicalAnalyzer | None = None,
    ) -> None:
        self.repository = repository
        self.database = repository.database
        self.embedding_gateway = embedding_gateway
        self.rerank_gateway = rerank_gateway
        self.answerability_gateway = answerability_gateway
        self.analyzer = analyzer or ChineseNutritionLexicalAnalyzer()

    async def get_runtime_snapshot(self) -> NutritionRuntimeSnapshot | None:
        active = await self.repository.get_active_profile()
        if active is None:
            return None
        release, profile = active
        return NutritionRuntimeSnapshot(
            corpus_release_id=release.id,
            corpus_release_version=release.version,
            corpus_manifest_sha256=release.manifest_sha256,
            retrieval_profile_id=profile.id,
            embedding_profile_id=release.embedding_profile_id,
            lexical_profile_id=release.lexical_profile_id,
            chunker_profile_id=release.chunker_profile_key,
        )

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        metadata_filter: KnowledgeMetadataFilter | Mapping[str, Any] | None = None,
        retrieved_in_invocation_id: str | None = None,
        release_id: str | None = None,
    ) -> Mapping[str, Any]:
        query = " ".join(query.split())
        if not query or len(query) > 1000:
            raise ValueError("query must contain 1 to 1000 characters")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        filters = (
            metadata_filter
            if isinstance(metadata_filter, KnowledgeMetadataFilter)
            else KnowledgeMetadataFilter.model_validate(metadata_filter or {})
        )
        active = (
            await self.repository.get_release_profile(release_id)
            if release_id is not None
            else await self.repository.get_active_profile()
        )
        if active is None:
            return {
                "corpus_status": "empty",
                "candidates": [],
                "citations": [],
                "query_summary": "active_release=none",
                "retrieval_run_id": None,
            }
        release, profile = active
        source_ids = await self._eligible_source_ids(release=release, filters=filters)
        if not source_ids:
            run_id = await self._record_run(
                release=release,
                profile=profile,
                query=query,
                filters=filters,
                status="insufficient",
                started=time.monotonic(),
                usage={},
                hits=(),
                invocation_id=retrieved_in_invocation_id,
            )
            return {
                "corpus_status": "available",
                "candidates": [],
                "citations": [],
                "query_summary": "eligible_sources=0;adopted=0",
                "retrieval_run_id": run_id,
            }

        started = time.monotonic()
        normalized_query, query_terms = self.analyzer.analyze(query)
        failure_stage = "query_embedding"
        try:
            embedded = await self.embedding_gateway.embed((query,))
            vector = embedded.vectors[0]
            if len(vector) != profile.dimensions:
                raise NutritionModelGatewayError("query_embedding_dimensions")
            failure_stage = "candidate_retrieval"
            dense = await self._dense_channel(
                vector=vector,
                profile=profile,
                source_ids=source_ids,
                release=release,
            )
            lexical = await self._lexical_channel(
                normalized_query=normalized_query,
                query_terms=query_terms,
                profile=profile,
                source_ids=source_ids,
                release=release,
            )
            phrase = await self._phrase_channel(
                normalized_query=normalized_query,
                profile=profile,
                source_ids=source_ids,
                release=release,
            )
            fused = self._fuse(dense=dense, lexical=lexical, phrase=phrase, rrf_k=profile.rrf_k)
            rerankable = fused[: profile.rerank_top_n]
            loaded = await self._load_chunks(tuple(item.chunk_id for item in rerankable))
            ordered_loaded = [
                loaded[item.chunk_id] for item in rerankable if item.chunk_id in loaded
            ]
            if ordered_loaded:
                failure_stage = "rerank"
                reranked = await self.rerank_gateway.rerank(
                    query=query,
                    documents=tuple(self._rerank_document(item) for item in ordered_loaded),
                    top_n=len(ordered_loaded),
                )
                by_chunk = {item.chunk.id: item for item in ordered_loaded}
                rerank_order: list[_FusedHit] = []
                for rank, result in enumerate(reranked.items, start=1):
                    loaded_item = ordered_loaded[result.index]
                    hit = next(
                        candidate
                        for candidate in rerankable
                        if candidate.chunk_id == loaded_item.chunk.id
                    )
                    hit.rerank_rank = rank
                    hit.rerank_score = max(0.0, float(result.score))
                    rerank_order.append(hit)
            else:
                by_chunk = {}
                rerank_order = []
                reranked = None
            failure_stage = "answerability"
            answerability = await self._assess_answerability(
                query=query,
                profile=profile,
                rerank_order=rerank_order,
                loaded=by_chunk,
            )
        except (EmbeddingError, NutritionModelGatewayError, ValueError) as error:
            run_id = await self._record_run(
                release=release,
                profile=profile,
                query=query,
                filters=filters,
                status="failed",
                started=started,
                usage={
                    "failure": "model_or_retrieval_error",
                    "failure_stage": failure_stage,
                    "error_code": str(error)[:128] or type(error).__name__,
                },
                hits=(),
                invocation_id=retrieved_in_invocation_id,
            )
            return {
                "corpus_status": "error",
                "candidates": [],
                "citations": [],
                "query_summary": "retrieval_failed_closed",
                "retrieval_run_id": run_id,
            }

        final_limit = min(max_results, profile.final_top_k)
        used_parents: set[str] = set()
        context_chars = 0
        selected_count = 0
        supported_chunk_ids = (
            {rerank_order[index].chunk_id for index in answerability.supported_document_indices}
            if answerability is not None
            else None
        )
        for hit in rerank_order:
            loaded_hit = by_chunk[hit.chunk_id]
            parent_identity = (
                loaded_hit.parent.id if loaded_hit.parent is not None else hit.chunk_id
            )
            if hit.rerank_score is None or hit.rerank_score < profile.min_rerank_score:
                hit.rejection_reason = "below_rerank_threshold"
                continue
            if supported_chunk_ids is not None and hit.chunk_id not in supported_chunk_ids:
                assert answerability is not None
                hit.rejection_reason = _answerability_exclusion_reason(answerability)
                continue
            if parent_identity in used_parents:
                hit.rejection_reason = "duplicate_parent_context"
                continue
            if selected_count >= final_limit:
                hit.rejection_reason = "result_limit"
                continue
            if context_chars + len(loaded_hit.context) > profile.max_context_chars:
                hit.rejection_reason = "context_budget"
                continue
            hit.selected = True
            used_parents.add(parent_identity)
            selected_count += 1
            context_chars += len(loaded_hit.context)
        for hit in rerankable:
            if hit.rerank_rank is None:
                hit.rejection_reason = hit.rejection_reason or "not_returned_by_reranker"

        usage: dict[str, Any] = {
            "embedding": {
                "model": embedded.model,
                "request_id": embedded.request_id,
                "prompt_tokens": embedded.prompt_tokens,
                "latency_ms": embedded.latency_ms,
            }
        }
        if reranked is not None:
            usage["rerank"] = {
                "model": reranked.model,
                "request_id": reranked.request_id,
                "prompt_tokens": reranked.prompt_tokens,
                "latency_ms": reranked.latency_ms,
            }
        if answerability is not None:
            usage["answerability"] = {
                "mode": profile.answerability_mode,
                "outcome": answerability.outcome,
                "reason_code": answerability.reason_code,
                "model": answerability.model,
                "request_id": answerability.request_id,
                "input_tokens": answerability.input_tokens,
                "output_tokens": answerability.output_tokens,
            }
        run_id = await self._record_run(
            release=release,
            profile=profile,
            query=query,
            filters=filters,
            status="succeeded" if selected_count else "insufficient",
            started=started,
            usage=usage,
            hits=tuple(rerankable),
            invocation_id=retrieved_in_invocation_id,
        )
        candidates = [
            self._candidate(
                release=release,
                run_id=run_id,
                loaded=by_chunk[hit.chunk_id],
                hit=hit,
            )
            for hit in sorted(
                rerankable,
                key=lambda item: (
                    item.rerank_rank is None,
                    item.rerank_rank or 100_000,
                    item.rrf_rank,
                ),
            )
            if hit.chunk_id in by_chunk
        ]
        return {
            "corpus_status": "available",
            "candidates": candidates,
            "citations": [],
            "query_summary": (
                f"release={release.version};eligible_sources={len(source_ids)};"
                f"hybrid_candidates={len(fused)};"
                f"answerability={answerability.outcome if answerability else 'legacy'};"
                f"adopted={selected_count}"
            ),
            "retrieval_run_id": run_id,
        }

    async def _assess_answerability(
        self,
        *,
        query: str,
        profile: RetrievalProfile,
        rerank_order: Sequence[_FusedHit],
        loaded: Mapping[str, _LoadedChunk],
    ) -> AnswerabilityResult | None:
        if profile.answerability_mode is None:
            return None
        if profile.answerability_mode not in {ANSWERABILITY_MODE_V1, ANSWERABILITY_MODE}:
            raise NutritionModelGatewayError("unsupported_answerability_mode")
        if self.answerability_gateway is None:
            raise NutritionModelGatewayError("answerability_gateway_unavailable")
        candidates = tuple(
            hit
            for hit in rerank_order
            if hit.rerank_score is not None
            and hit.rerank_score >= profile.min_rerank_score
            and hit.chunk_id in loaded
        )[:8]
        if not candidates:
            return AnswerabilityResult(
                outcome="insufficient",
                supported_document_indices=(),
                reason_code="unrelated",
                model="not-called",
                request_id=None,
                input_tokens=0,
                output_tokens=0,
            )
        result = await self.answerability_gateway.assess(
            query=query,
            documents=tuple(
                AnswerabilityDocument(
                    source_key=loaded[hit.chunk_id].source.source_key,
                    title=loaded[hit.chunk_id].source.title,
                    section=self._section_label(loaded[hit.chunk_id]),
                    content=loaded[hit.chunk_id].context,
                )
                for hit in candidates
            ),
            mode=profile.answerability_mode,
        )
        if any(index >= len(candidates) for index in result.supported_document_indices):
            raise NutritionModelGatewayError("answerability_unknown_document")

        # Answerability indices refer to the compact list passed to the model,
        # while selection uses the complete reranked list.
        supported_ids = {candidates[index].chunk_id for index in result.supported_document_indices}
        return AnswerabilityResult(
            outcome=result.outcome,
            supported_document_indices=tuple(
                index for index, hit in enumerate(rerank_order) if hit.chunk_id in supported_ids
            ),
            reason_code=result.reason_code,
            model=result.model,
            request_id=result.request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    @staticmethod
    def _section_label(item: _LoadedChunk) -> str | None:
        heading = " / ".join(_string_list(item.section.heading_path_json))
        page = f"第 {item.section.page_from} 页" if item.section.page_from is not None else None
        return " · ".join(value for value in (heading, page) if value) or None

    async def get_source(
        self,
        *,
        source_id: str,
        chunk_id: str | None = None,
    ) -> Mapping[str, Any]:
        active = await self.repository.get_active_profile()
        if active is None:
            return self._unavailable_source(source_id, chunk_id, status="empty")
        release, _ = active
        async with self.database.session() as session:
            member = await session.scalar(
                select(NutritionCorpusReleaseSourceRecord.source_id).where(
                    NutritionCorpusReleaseSourceRecord.release_id == release.id,
                    NutritionCorpusReleaseSourceRecord.source_id == source_id,
                )
            )
            source = await session.get(NutritionKnowledgeSourceRecord, source_id)
            if member is None or source is None or source.status != "approved":
                return self._unavailable_source(source_id, chunk_id, status="available")
            labels = tuple(
                await session.scalars(
                    select(NutritionKnowledgeSourceLabelRecord.value).where(
                        NutritionKnowledgeSourceLabelRecord.source_id == source_id,
                        NutritionKnowledgeSourceLabelRecord.kind == "applicability",
                    )
                )
            )
            chunk = (
                await session.get(NutritionRagChunkRecord, chunk_id)
                if chunk_id is not None
                else None
            )
            if chunk is not None and chunk.source_id != source_id:
                chunk = None
            return {
                "corpus_status": "available",
                "source_id": source.id,
                "chunk_id": chunk_id,
                "source": {
                    "source_key": source.source_key,
                    "version": source.version,
                    "title": source.title,
                    "publisher": source.publisher,
                    "published_at": _iso_date(source.published_at),
                    "source_url": source.source_url,
                    "language": source.language,
                    "content_sha256": source.content_sha256,
                    "applicability": list(labels),
                    "review_status": "approved",
                    "publication_status": "active_release",
                    "active": True,
                    "release_id": release.id,
                    "release_version": release.version,
                },
                "chunk": (
                    {
                        "chunk_id": chunk.id,
                        "content": chunk.content_text,
                        "content_sha256": chunk.content_sha256,
                        "ordinal": chunk.ordinal,
                    }
                    if chunk is not None
                    else None
                ),
                "eligibility": {
                    "active": True,
                    "publication_status": "active_release",
                    "reason": None,
                },
            }

    async def _eligible_source_ids(
        self,
        *,
        release: NutritionRelease,
        filters: KnowledgeMetadataFilter,
    ) -> tuple[str, ...]:
        statement = (
            select(NutritionKnowledgeSourceRecord.id)
            .join(
                NutritionCorpusReleaseSourceRecord,
                NutritionCorpusReleaseSourceRecord.source_id == NutritionKnowledgeSourceRecord.id,
            )
            .where(
                NutritionCorpusReleaseSourceRecord.release_id == release.id,
                NutritionKnowledgeSourceRecord.status == "approved",
            )
        )
        if filters.source_ids:
            statement = statement.where(NutritionKnowledgeSourceRecord.id.in_(filters.source_ids))
        if filters.publishers:
            statement = statement.where(
                NutritionKnowledgeSourceRecord.publisher.in_(filters.publishers)
            )
        if filters.languages:
            statement = statement.where(
                NutritionKnowledgeSourceRecord.language.in_(filters.languages)
            )
        if filters.published_from is not None:
            statement = statement.where(
                NutritionKnowledgeSourceRecord.published_at >= filters.published_from
            )
        if filters.published_to is not None:
            statement = statement.where(
                NutritionKnowledgeSourceRecord.published_at <= filters.published_to
            )
        for kind, values in (("tag", filters.tags), ("applicability", filters.applicability)):
            for value in values:
                statement = statement.where(
                    exists(
                        select(literal(1)).where(
                            NutritionKnowledgeSourceLabelRecord.source_id
                            == NutritionKnowledgeSourceRecord.id,
                            NutritionKnowledgeSourceLabelRecord.kind == kind,
                            NutritionKnowledgeSourceLabelRecord.value == value,
                        )
                    )
                )
        async with self.database.session() as session:
            return tuple(
                await session.scalars(statement.order_by(NutritionKnowledgeSourceRecord.id))
            )

    async def _dense_channel(
        self,
        *,
        vector: Sequence[float],
        profile: RetrievalProfile,
        source_ids: Sequence[str],
        release: NutritionRelease,
    ) -> tuple[_ChannelHit, ...]:
        if self.database.engine.dialect.name != "postgresql":
            return await self._sqlite_dense(
                vector=vector,
                profile=profile,
                source_ids=source_ids,
                release=release,
            )
        distance = NutritionChunkEmbeddingRecord.embedding.cosine_distance(list(vector))
        statement = (
            select(NutritionChunkEmbeddingRecord.chunk_id, distance.label("distance"))
            .join(
                NutritionRagChunkRecord,
                NutritionRagChunkRecord.id == NutritionChunkEmbeddingRecord.chunk_id,
            )
            .where(
                NutritionChunkEmbeddingRecord.embedding_profile_id == profile.embedding_profile_id,
                NutritionChunkEmbeddingRecord.status == "ready",
                NutritionChunkEmbeddingRecord.embedding.is_not(None),
                NutritionRagChunkRecord.source_id.in_(tuple(source_ids)),
                NutritionRagChunkRecord.chunker_profile_key == release.chunker_profile_key,
                NutritionRagChunkRecord.chunk_kind == "retrieval_child",
            )
            .order_by(distance)
            .limit(profile.dense_top_k)
        )
        async with self.database.session() as session:
            rows = tuple((await session.execute(statement)).tuples())
        return tuple(
            _ChannelHit(
                chunk_id=chunk_id,
                rank=rank,
                score=max(0.0, min(1.0, 1.0 - float(distance_value))),
            )
            for rank, (chunk_id, distance_value) in enumerate(rows, start=1)
        )

    async def _sqlite_dense(
        self,
        *,
        vector: Sequence[float],
        profile: RetrievalProfile,
        source_ids: Sequence[str],
        release: NutritionRelease,
    ) -> tuple[_ChannelHit, ...]:
        statement = (
            select(
                NutritionChunkEmbeddingRecord.chunk_id,
                NutritionChunkEmbeddingRecord.embedding,
            )
            .join(
                NutritionRagChunkRecord,
                NutritionRagChunkRecord.id == NutritionChunkEmbeddingRecord.chunk_id,
            )
            .where(
                NutritionChunkEmbeddingRecord.embedding_profile_id == profile.embedding_profile_id,
                NutritionChunkEmbeddingRecord.status == "ready",
                NutritionRagChunkRecord.source_id.in_(tuple(source_ids)),
                NutritionRagChunkRecord.chunker_profile_key == release.chunker_profile_key,
                NutritionRagChunkRecord.chunk_kind == "retrieval_child",
            )
        )
        async with self.database.session() as session:
            rows = tuple((await session.execute(statement)).tuples())
        scored = sorted(
            (
                (chunk_id, _cosine_similarity(vector, stored))
                for chunk_id, stored in rows
                if stored is not None and len(stored) == len(vector)
            ),
            key=lambda item: (-item[1], item[0]),
        )[: profile.dense_top_k]
        return tuple(
            _ChannelHit(chunk_id=chunk_id, rank=rank, score=score)
            for rank, (chunk_id, score) in enumerate(scored, start=1)
        )

    async def _lexical_channel(
        self,
        *,
        normalized_query: str,
        query_terms: str,
        profile: RetrievalProfile,
        source_ids: Sequence[str],
        release: NutritionRelease,
    ) -> tuple[_ChannelHit, ...]:
        if not query_terms:
            return ()
        if self.database.engine.dialect.name != "postgresql":
            return await self._sqlite_lexical(
                query_terms=query_terms,
                profile=profile,
                source_ids=source_ids,
                release=release,
            )
        query_text = " OR ".join(query_terms.split()[:32])
        tsquery = func.websearch_to_tsquery("simple", query_text)
        vector = func.to_tsvector("simple", NutritionRagChunkRecord.lexical_terms)
        score = func.ts_rank_cd(vector, tsquery)
        statement = (
            select(NutritionRagChunkRecord.id, score.label("score"))
            .where(
                NutritionRagChunkRecord.source_id.in_(tuple(source_ids)),
                NutritionRagChunkRecord.chunker_profile_key == release.chunker_profile_key,
                NutritionRagChunkRecord.chunk_kind == "retrieval_child",
                vector.op("@@")(tsquery),
            )
            .order_by(score.desc(), NutritionRagChunkRecord.id)
            .limit(profile.lexical_top_k)
        )
        async with self.database.session() as session:
            rows = tuple((await session.execute(statement)).tuples())
        return tuple(
            _ChannelHit(chunk_id=chunk_id, rank=rank, score=max(0.0, float(score_value)))
            for rank, (chunk_id, score_value) in enumerate(rows, start=1)
        )

    async def _sqlite_lexical(
        self,
        *,
        query_terms: str,
        profile: RetrievalProfile,
        source_ids: Sequence[str],
        release: NutritionRelease,
    ) -> tuple[_ChannelHit, ...]:
        rows = await self._lexical_rows(source_ids=source_ids, release=release)
        wanted = set(query_terms.split())
        scored = sorted(
            (
                (chunk_id, len(wanted.intersection(set(terms.split()))) / len(wanted))
                for chunk_id, _, terms in rows
                if wanted.intersection(set(terms.split()))
            ),
            key=lambda item: (-item[1], item[0]),
        )[: profile.lexical_top_k]
        return tuple(
            _ChannelHit(chunk_id=chunk_id, rank=rank, score=score)
            for rank, (chunk_id, score) in enumerate(scored, start=1)
        )

    async def _phrase_channel(
        self,
        *,
        normalized_query: str,
        profile: RetrievalProfile,
        source_ids: Sequence[str],
        release: NutritionRelease,
    ) -> tuple[_ChannelHit, ...]:
        if self.database.engine.dialect.name != "postgresql":
            rows = await self._lexical_rows(source_ids=source_ids, release=release)
            scored = sorted(
                (
                    (
                        chunk_id,
                        SequenceMatcher(None, normalized_query, lexical_text).ratio(),
                    )
                    for chunk_id, lexical_text, _ in rows
                    if normalized_query in lexical_text
                    or SequenceMatcher(None, normalized_query, lexical_text).quick_ratio() >= 0.25
                ),
                key=lambda item: (-item[1], item[0]),
            )[: profile.phrase_top_k]
        else:
            score = func.similarity(NutritionRagChunkRecord.lexical_text, normalized_query)
            statement = (
                select(NutritionRagChunkRecord.id, score.label("score"))
                .where(
                    NutritionRagChunkRecord.source_id.in_(tuple(source_ids)),
                    NutritionRagChunkRecord.chunker_profile_key == release.chunker_profile_key,
                    NutritionRagChunkRecord.chunk_kind == "retrieval_child",
                    or_(
                        NutritionRagChunkRecord.lexical_text.contains(normalized_query),
                        score >= 0.08,
                    ),
                )
                .order_by(score.desc(), NutritionRagChunkRecord.id)
                .limit(profile.phrase_top_k)
            )
            async with self.database.session() as session:
                scored = list((await session.execute(statement)).tuples())
        return tuple(
            _ChannelHit(chunk_id=chunk_id, rank=rank, score=max(0.0, float(score)))
            for rank, (chunk_id, score) in enumerate(scored, start=1)
        )

    async def _lexical_rows(
        self, *, source_ids: Sequence[str], release: NutritionRelease
    ) -> tuple[tuple[str, str, str], ...]:
        async with self.database.session() as session:
            return tuple(
                (
                    await session.execute(
                        select(
                            NutritionRagChunkRecord.id,
                            NutritionRagChunkRecord.lexical_text,
                            NutritionRagChunkRecord.lexical_terms,
                        ).where(
                            NutritionRagChunkRecord.source_id.in_(tuple(source_ids)),
                            NutritionRagChunkRecord.chunker_profile_key
                            == release.chunker_profile_key,
                            NutritionRagChunkRecord.chunk_kind == "retrieval_child",
                        )
                    )
                ).tuples()
            )

    @staticmethod
    def _fuse(
        *,
        dense: Sequence[_ChannelHit],
        lexical: Sequence[_ChannelHit],
        phrase: Sequence[_ChannelHit],
        rrf_k: int,
    ) -> list[_FusedHit]:
        values: dict[str, _FusedHit] = {}
        for name, hits in (("dense", dense), ("lexical", lexical), ("phrase", phrase)):
            for hit in hits:
                fused = values.setdefault(hit.chunk_id, _FusedHit(chunk_id=hit.chunk_id))
                setattr(fused, f"{name}_rank", hit.rank)
                setattr(fused, f"{name}_score", hit.score)
                fused.rrf_score += 1.0 / (rrf_k + hit.rank)
                fused.reasons.append(f"{name}_candidate")
        ordered = sorted(values.values(), key=lambda item: (-item.rrf_score, item.chunk_id))
        for rank, fused_hit in enumerate(ordered, start=1):
            fused_hit.rrf_rank = rank
        return ordered

    async def _load_chunks(self, chunk_ids: Sequence[str]) -> dict[str, _LoadedChunk]:
        if not chunk_ids:
            return {}
        async with self.database.session() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(
                            NutritionRagChunkRecord,
                            NutritionKnowledgeSectionRecord,
                            NutritionKnowledgeSourceRecord,
                        )
                        .join(
                            NutritionKnowledgeSectionRecord,
                            NutritionKnowledgeSectionRecord.id
                            == NutritionRagChunkRecord.section_id,
                        )
                        .join(
                            NutritionKnowledgeSourceRecord,
                            NutritionKnowledgeSourceRecord.id == NutritionRagChunkRecord.source_id,
                        )
                        .where(NutritionRagChunkRecord.id.in_(tuple(chunk_ids)))
                    )
                ).tuples()
            )
            parent_ids = tuple(
                chunk.parent_chunk_id for chunk, _, _ in rows if chunk.parent_chunk_id
            )
            parents = {
                item.id: item
                for item in await session.scalars(
                    select(NutritionRagChunkRecord).where(
                        NutritionRagChunkRecord.id.in_(parent_ids)
                    )
                )
            }
            labels = tuple(
                (
                    await session.execute(
                        select(
                            NutritionKnowledgeSourceLabelRecord.source_id,
                            NutritionKnowledgeSourceLabelRecord.value,
                        ).where(
                            NutritionKnowledgeSourceLabelRecord.source_id.in_(
                                tuple(source.id for _, _, source in rows)
                            ),
                            NutritionKnowledgeSourceLabelRecord.kind == "applicability",
                        )
                    )
                ).tuples()
            )
        applicability: dict[str, list[str]] = defaultdict(list)
        for source_id, value in labels:
            applicability[source_id].append(value)
        return {
            chunk.id: _LoadedChunk(
                chunk=chunk,
                parent=parents.get(chunk.parent_chunk_id or ""),
                section=section,
                source=source,
                applicability=tuple(sorted(applicability[source.id])),
            )
            for chunk, section, source in rows
        }

    async def _record_run(
        self,
        *,
        release: NutritionRelease,
        profile: RetrievalProfile,
        query: str,
        filters: KnowledgeMetadataFilter,
        status: str,
        started: float,
        usage: Mapping[str, Any],
        hits: Sequence[_FusedHit],
        invocation_id: str | None,
    ) -> str:
        run_id = new_uuid()
        query_plan = {
            "version": profile.query_plan_version,
            "query": query,
            "filters": filters.model_dump(mode="json"),
        }
        async with self.database.session() as session, session.begin():
            session.add(
                NutritionRetrievalRunRecord(
                    id=run_id,
                    invocation_id=invocation_id,
                    release_id=release.id,
                    retrieval_profile_id=profile.id,
                    query_plan_json=_json(query_plan),
                    query_hash=hashlib.sha256(_json(query_plan).encode()).hexdigest(),
                    safe_query_summary=query[:1000],
                    status=status,
                    provider_usage_json=_json(dict(usage)),
                    total_latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                )
            )
            # Without an ORM relationship, candidates may otherwise flush first.
            # Keep this flush inside the transaction so failures roll back both.
            await session.flush()
            session.add_all(
                [
                    NutritionRetrievalCandidateRecord(
                        id=new_uuid(),
                        retrieval_run_id=run_id,
                        chunk_id=hit.chunk_id,
                        query_variant_ids_json='["primary"]',
                        dense_rank=hit.dense_rank,
                        dense_score=hit.dense_score,
                        lexical_rank=hit.lexical_rank,
                        lexical_score=hit.lexical_score,
                        phrase_rank=hit.phrase_rank,
                        phrase_score=hit.phrase_score,
                        rrf_rank=hit.rrf_rank,
                        rrf_score=hit.rrf_score,
                        rerank_rank=hit.rerank_rank,
                        rerank_score=hit.rerank_score,
                        selection_status="adopted" if hit.selected else "candidate_only",
                        rejection_reason=hit.rejection_reason,
                    )
                    for hit in hits
                ]
            )
        return run_id

    @staticmethod
    def _rerank_document(item: _LoadedChunk) -> str:
        heading = " / ".join(_string_list(item.section.heading_path_json))
        return f"资料：{item.source.title}\n章节：{heading}\n{item.context}"[:4096]

    @staticmethod
    def _candidate(
        *,
        release: NutritionRelease,
        run_id: str,
        loaded: _LoadedChunk,
        hit: _FusedHit,
    ) -> dict[str, Any]:
        heading = " / ".join(_string_list(loaded.section.heading_path_json))
        page = f"第 {loaded.section.page_from} 页" if loaded.section.page_from is not None else None
        section = " · ".join(value for value in (heading, page) if value) or None
        stable = f"{release.manifest_sha256}:{loaded.chunk.id}"
        content = loaded.context[:16_000]
        return {
            "candidate_id": "candidate-"
            + hashlib.sha256(f"{run_id}:{stable}".encode()).hexdigest()[:48],
            "citation_id": "citation-" + hashlib.sha256(stable.encode()).hexdigest()[:48],
            "source_id": loaded.source.id,
            "chunk_id": loaded.chunk.id,
            "title": loaded.source.title,
            "publisher": loaded.source.publisher,
            "published_at": _iso_date(loaded.source.published_at),
            "version": loaded.source.version,
            "section_or_page": section,
            "source_url": loaded.source.source_url,
            "applicability": list(loaded.applicability),
            "review_status": "approved",
            "active": True,
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "source_content_sha256": loaded.source.content_sha256,
            "corpus_release_id": release.id,
            "corpus_release_sha256": release.manifest_sha256,
            "retrieval_run_id": run_id,
            "rank": hit.rerank_rank or hit.rrf_rank,
            "keyword_score": max(hit.lexical_score or 0.0, hit.phrase_score or 0.0),
            "vector_score": hit.dense_score or 0.0,
            "rerank_score": hit.rerank_score or 0.0,
            "match_reasons": list(dict.fromkeys(hit.reasons)),
            "adoption_status": "adopted" if hit.selected else "candidate_only",
        }

    @staticmethod
    def _unavailable_source(
        source_id: str, chunk_id: str | None, *, status: str
    ) -> Mapping[str, Any]:
        return {
            "corpus_status": status,
            "source_id": source_id,
            "chunk_id": chunk_id,
            "source": None,
            "chunk": None,
            "eligibility": {
                "active": False,
                "publication_status": "not_active",
                "reason": "source_not_in_active_release",
            },
        }


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def _string_list(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return ()
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        return ()
    return tuple(decoded)


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ["HybridNutritionRagService"]
