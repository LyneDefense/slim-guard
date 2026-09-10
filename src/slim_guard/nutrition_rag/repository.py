from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import (
    NutritionChunkEmbeddingRecord,
    NutritionCorpusActivationEventRecord,
    NutritionCorpusReleaseRecord,
    NutritionCorpusReleaseSourceRecord,
    NutritionCorpusRuntimeRecord,
    NutritionEmbeddingProfileRecord,
    NutritionEvaluationCaseRecord,
    NutritionEvaluationDatasetRecord,
    NutritionEvaluationResultRecord,
    NutritionEvaluationRunRecord,
    NutritionKnowledgeAssetRecord,
    NutritionKnowledgeJobEventRecord,
    NutritionKnowledgeJobRecord,
    NutritionKnowledgeReviewEventRecord,
    NutritionKnowledgeSectionRecord,
    NutritionKnowledgeSourceLabelRecord,
    NutritionKnowledgeSourceRecord,
    NutritionRagChunkRecord,
    NutritionRetrievalCandidateRecord,
    NutritionRetrievalProfileRecord,
    NutritionRetrievalRunRecord,
    new_uuid,
    utc_now,
)
from slim_guard.nutrition_rag.processing import (
    NutritionChunkDraftV2,
    ParsedNutritionDocument,
)
from slim_guard.nutrition_rag.profiles import (
    CHUNKER_PROFILE_KEY,
    DEFAULT_EMBEDDING_PROFILE_ID,
    DEFAULT_LEXICAL_PROFILE_ID,
    DEFAULT_RETRIEVAL_PROFILE_ID,
)
from slim_guard.nutrition_rag.storage import StoredNutritionObject

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from slim_guard.db.session import Database


class NutritionRagControlError(RuntimeError):
    pass


class NutritionRagNotFound(NutritionRagControlError):
    pass


class NutritionRagConflict(NutritionRagControlError):
    pass


class NutritionRagGovernanceError(NutritionRagControlError):
    pass


@dataclass(frozen=True, slots=True)
class NutritionAsset:
    id: str
    storage_key: str
    bucket: str
    region: str
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    etag: str | None
    source_method: str
    source_url: str | None
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NutritionJob:
    id: str
    job_type: str
    subject_type: str
    subject_id: str | None
    status: str
    stage: str
    completed_items: int
    total_items: int
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    available_at: datetime
    idempotency_key: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    error_code: str | None
    safe_error_message: str | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class NutritionJobEvent:
    id: str
    job_id: str
    sequence: int
    stage: str
    level: str
    message: str
    completed_items: int
    total_items: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PendingNutritionEmbedding:
    chunk_id: str
    content_sha256: str
    embedding_text: str


@dataclass(frozen=True, slots=True)
class NutritionReviewState:
    content: str | None
    applicability: str | None
    rights: str | None

    @property
    def approved(self) -> bool:
        return all(value == "approve" for value in (self.content, self.applicability, self.rights))


@dataclass(frozen=True, slots=True)
class NutritionRelease:
    id: str
    version: str
    status: str
    manifest_sha256: str
    source_ids: tuple[str, ...]
    embedding_profile_id: str
    lexical_profile_id: str
    retrieval_profile_id: str
    chunker_profile_key: str
    evaluation_run_id: str | None
    created_by: str
    created_at: datetime
    approved_at: datetime | None
    activated_at: datetime | None
    retired_at: datetime | None


@dataclass(frozen=True, slots=True)
class NutritionRuntime:
    active_release_id: str | None
    active_release_version: str | None
    runtime_revision: int
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NutritionEvaluationCase:
    id: str
    case_key: str
    query_plan_input: dict[str, Any]
    expected_source_keys: tuple[str, ...]
    expected_chunk_concepts: tuple[str, ...]
    forbidden_source_keys: tuple[str, ...]
    expected_outcome: str


@dataclass(frozen=True, slots=True)
class NutritionEvaluationDataset:
    id: str
    version: str
    manifest_sha256: str
    status: str
    cases: tuple[NutritionEvaluationCase, ...]
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    id: str
    profile_key: str
    embedding_profile_id: str
    lexical_profile_id: str
    dimensions: int
    dense_top_k: int
    lexical_top_k: int
    phrase_top_k: int
    rrf_k: int
    rerank_top_n: int
    final_top_k: int
    min_rerank_score: float
    max_context_chars: int
    query_plan_version: str


class NutritionRagRepository:
    """Transactional control plane for ingestion, review, release, and retrieval."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_asset(
        self,
        *,
        stored: StoredNutritionObject,
        original_filename: str,
        source_method: str,
        source_url: str | None,
        created_by: str,
    ) -> NutritionAsset:
        if source_method not in {"upload", "url", "pasted_text", "legacy_manifest"}:
            raise ValueError("Unsupported nutrition asset source method")
        original_filename = _text(original_filename, "original_filename", 512)
        created_by = _text(created_by, "created_by", 128)
        if source_url is not None:
            source_url = _text(source_url, "source_url", 4000)
        async with self.database.session() as session, session.begin():
            existing = await session.scalar(
                select(NutritionKnowledgeAssetRecord).where(
                    NutritionKnowledgeAssetRecord.sha256 == stored.sha256
                )
            )
            if existing is not None:
                return self._asset(existing)
            row = NutritionKnowledgeAssetRecord(
                id=new_uuid(),
                storage_backend="cos",
                storage_key=stored.key,
                bucket=stored.bucket,
                region=stored.region,
                original_filename=original_filename,
                media_type=stored.media_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                etag=stored.etag,
                source_method=source_method,
                source_url=source_url,
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            return self._asset(row)

    async def get_asset(self, asset_id: str) -> NutritionAsset | None:
        async with self.database.session() as session:
            row = await session.get(NutritionKnowledgeAssetRecord, asset_id)
            return self._asset(row) if row is not None else None

    async def enqueue_job(
        self,
        *,
        job_type: str,
        subject_type: str,
        subject_id: str | None,
        input: Mapping[str, Any],
        idempotency_key: str,
        created_by: str,
        max_attempts: int = 3,
    ) -> NutritionJob:
        if job_type not in {"ingest", "reparse", "embed", "build_release", "evaluate"}:
            raise ValueError("Unsupported nutrition job type")
        subject_type = _text(subject_type, "subject_type", 32)
        idempotency_key = _text(idempotency_key, "idempotency_key", 256)
        created_by = _text(created_by, "created_by", 128)
        if not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        encoded = _canonical_json(dict(input), maximum=64_000)
        try:
            async with self.database.session() as session, session.begin():
                existing = await session.scalar(
                    select(NutritionKnowledgeJobRecord).where(
                        NutritionKnowledgeJobRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return self._job(existing)
                row = NutritionKnowledgeJobRecord(
                    id=new_uuid(),
                    job_type=job_type,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    status="queued",
                    stage="queued",
                    completed_items=0,
                    total_items=0,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    available_at=utc_now(),
                    idempotency_key=idempotency_key,
                    input_json=encoded,
                    created_by=created_by,
                )
                session.add(row)
                await session.flush()
                await self._append_job_event(
                    session,
                    row=row,
                    stage="queued",
                    level="info",
                    message="任务已进入队列",
                )
                return self._job(row)
        except IntegrityError as error:
            existing_job = await self.get_job_by_idempotency_key(idempotency_key)
            if existing_job is not None:
                return existing_job
            raise NutritionRagConflict("Nutrition job idempotency conflict") from error

    async def get_job(self, job_id: str) -> NutritionJob | None:
        async with self.database.session() as session:
            row = await session.get(NutritionKnowledgeJobRecord, job_id)
            return self._job(row) if row is not None else None

    async def get_job_by_idempotency_key(self, key: str) -> NutritionJob | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(NutritionKnowledgeJobRecord).where(
                    NutritionKnowledgeJobRecord.idempotency_key == key
                )
            )
            return self._job(row) if row is not None else None

    async def list_jobs(
        self,
        *,
        statuses: Sequence[str] = (),
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[NutritionJob, ...]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid job page")
        statement = select(NutritionKnowledgeJobRecord)
        if statuses:
            statement = statement.where(NutritionKnowledgeJobRecord.status.in_(tuple(statuses)))
        statement = (
            statement.order_by(
                NutritionKnowledgeJobRecord.created_at.desc(), NutritionKnowledgeJobRecord.id
            )
            .limit(limit)
            .offset(offset)
        )
        async with self.database.session() as session:
            return tuple(self._job(row) for row in await session.scalars(statement))

    async def list_job_events(self, job_id: str) -> tuple[NutritionJobEvent, ...]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(NutritionKnowledgeJobEventRecord)
                .where(NutritionKnowledgeJobEventRecord.job_id == job_id)
                .order_by(NutritionKnowledgeJobEventRecord.sequence)
            )
            return tuple(self._job_event(row) for row in rows)

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> NutritionJob | None:
        worker_id = _text(worker_id, "worker_id", 128)
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        now = _aware(now or utc_now())
        async with self.database.session() as session, session.begin():
            statement = (
                select(NutritionKnowledgeJobRecord)
                .where(
                    or_(
                        and_(
                            NutritionKnowledgeJobRecord.status.in_(("queued", "retry_wait")),
                            NutritionKnowledgeJobRecord.available_at <= now,
                        ),
                        and_(
                            NutritionKnowledgeJobRecord.status == "running",
                            NutritionKnowledgeJobRecord.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(
                    NutritionKnowledgeJobRecord.available_at,
                    NutritionKnowledgeJobRecord.created_at,
                )
                .limit(1)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = await session.scalar(statement)
            if row is None:
                return None
            row.status = "running"
            row.stage = "claimed"
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.attempt_count += 1
            row.started_at = row.started_at or now
            row.finished_at = None
            await self._append_job_event(
                session,
                row=row,
                stage="claimed",
                level="info",
                message=f"Worker 已领取任务（第 {row.attempt_count} 次尝试）",
            )
            await session.flush()
            return self._job(row)

    async def update_job_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        stage: str,
        message: str,
        completed_items: int,
        total_items: int,
        lease_seconds: int,
    ) -> NutritionJob:
        if completed_items < 0 or total_items < completed_items:
            raise ValueError("Invalid nutrition job progress")
        async with self.database.session() as session, session.begin():
            row = await self._locked_owned_job(session, job_id=job_id, worker_id=worker_id)
            row.stage = _text(stage, "stage", 64)
            row.completed_items = completed_items
            row.total_items = total_items
            row.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
            await self._append_job_event(
                session,
                row=row,
                stage=row.stage,
                level="info",
                message=message,
            )
            await session.flush()
            return self._job(row)

    async def complete_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        output: Mapping[str, Any],
        message: str = "任务已完成",
    ) -> NutritionJob:
        async with self.database.session() as session, session.begin():
            row = await self._locked_owned_job(session, job_id=job_id, worker_id=worker_id)
            row.status = "succeeded"
            row.stage = "completed"
            row.output_json = _canonical_json(dict(output), maximum=64_000)
            row.completed_items = max(row.completed_items, row.total_items)
            row.lease_owner = None
            row.lease_expires_at = None
            row.error_code = None
            row.safe_error_message = None
            row.finished_at = utc_now()
            await self._append_job_event(
                session, row=row, stage="completed", level="info", message=message
            )
            await session.flush()
            return self._job(row)

    async def fail_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_code: str,
        safe_message: str,
        retryable: bool,
    ) -> NutritionJob:
        error_code = _text(error_code, "error_code", 128)
        safe_message = _text(safe_message, "safe_message", 1000)
        now = utc_now()
        async with self.database.session() as session, session.begin():
            row = await self._locked_owned_job(session, job_id=job_id, worker_id=worker_id)
            can_retry = retryable and row.attempt_count < row.max_attempts
            row.status = "retry_wait" if can_retry else "failed"
            row.stage = "retry_wait" if can_retry else "failed"
            row.error_code = error_code
            row.safe_error_message = safe_message
            row.lease_owner = None
            row.lease_expires_at = None
            row.available_at = now + timedelta(seconds=min(900, 15 * (2**row.attempt_count)))
            row.finished_at = None if can_retry else now
            await self._append_job_event(
                session,
                row=row,
                stage=row.stage,
                level="warning" if can_retry else "error",
                message=safe_message,
            )
            await session.flush()
            return self._job(row)

    async def retry_job(self, job_id: str, *, actor: str) -> NutritionJob:
        actor = _text(actor, "actor", 128)
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionKnowledgeJobRecord, job_id, with_for_update=True)
            if row is None:
                raise NutritionRagNotFound("Nutrition job does not exist")
            if row.status not in {"failed", "cancelled"}:
                raise NutritionRagGovernanceError("Only failed or cancelled jobs can be retried")
            row.status = "queued"
            row.stage = "queued"
            row.available_at = utc_now()
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = None
            await self._append_job_event(
                session,
                row=row,
                stage="queued",
                level="info",
                message=f"{actor} 已请求重试",
            )
            await session.flush()
            return self._job(row)

    async def cancel_job(self, job_id: str, *, actor: str) -> NutritionJob:
        actor = _text(actor, "actor", 128)
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionKnowledgeJobRecord, job_id, with_for_update=True)
            if row is None:
                raise NutritionRagNotFound("Nutrition job does not exist")
            if row.status not in {"queued", "retry_wait"}:
                raise NutritionRagGovernanceError("Only queued jobs can be cancelled")
            row.status = "cancelled"
            row.stage = "cancelled"
            row.finished_at = utc_now()
            await self._append_job_event(
                session,
                row=row,
                stage="cancelled",
                level="warning",
                message=f"{actor} 已取消任务",
            )
            await session.flush()
            return self._job(row)

    async def persist_processed_source(
        self,
        *,
        source_id: str,
        asset_id: str,
        document: ParsedNutritionDocument,
        chunks: Sequence[NutritionChunkDraftV2],
        tags: Sequence[str],
        applicability: Sequence[str],
        embedding_profile_id: str = DEFAULT_EMBEDDING_PROFILE_ID,
    ) -> int:
        section_ids = {
            section.ordinal: _stable_id(
                "nutrition-section",
                source_id,
                CHUNKER_PROFILE_KEY,
                str(section.ordinal),
                section.content_sha256,
            )
            for section in document.sections
        }
        async with self.database.session() as session, session.begin():
            source = await session.get(
                NutritionKnowledgeSourceRecord, source_id, with_for_update=True
            )
            if source is None:
                raise NutritionRagNotFound("Nutrition source does not exist")
            if source.content_sha256 != document.content_sha256:
                raise NutritionRagConflict("Parsed content does not match imported source")
            source.asset_id = asset_id
            source.parser_profile_key = "nutrition-document-parser-v1"
            await session.execute(
                delete(NutritionKnowledgeSourceLabelRecord).where(
                    NutritionKnowledgeSourceLabelRecord.source_id == source_id
                )
            )
            session.add_all(
                [
                    NutritionKnowledgeSourceLabelRecord(
                        source_id=source_id,
                        kind=kind,
                        value=value,
                    )
                    for kind, values in (("tag", tags), ("applicability", applicability))
                    for value in _labels(values)
                ]
            )
            for section in document.sections:
                section_id = section_ids[section.ordinal]
                section_row = await session.get(NutritionKnowledgeSectionRecord, section_id)
                if section_row is None:
                    session.add(
                        NutritionKnowledgeSectionRecord(
                            id=section_id,
                            source_id=source_id,
                            parent_section_id=None,
                            parser_profile_key="nutrition-document-parser-v1",
                            ordinal=section.ordinal,
                            heading_path_json=_canonical_json(list(section.heading_path)),
                            page_from=section.page_from,
                            page_to=section.page_to,
                            content_text=section.content,
                            content_sha256=section.content_sha256,
                        )
                    )
            await session.flush()
            ordered_chunks = sorted(chunks, key=lambda item: item.parent_chunk_id is not None)
            for child_phase in (False, True):
                for draft in ordered_chunks:
                    if (draft.parent_chunk_id is not None) != child_phase:
                        continue
                    chunk_row = await session.get(NutritionRagChunkRecord, draft.id)
                    if chunk_row is not None:
                        continue
                    session.add(
                        NutritionRagChunkRecord(
                            id=draft.id,
                            source_id=source_id,
                            section_id=section_ids[draft.section_ordinal],
                            parent_chunk_id=draft.parent_chunk_id,
                            chunker_profile_key=CHUNKER_PROFILE_KEY,
                            chunk_kind=draft.chunk_kind,
                            ordinal=draft.ordinal,
                            page_from=draft.page_from,
                            page_to=draft.page_to,
                            content_text=draft.content,
                            content_sha256=draft.content_sha256,
                            lexical_text=draft.lexical_text,
                            lexical_terms=draft.lexical_terms,
                            token_count=draft.token_count,
                            char_count=draft.char_count,
                            metadata_json=_canonical_json({"embedding_text": draft.embedding_text}),
                        )
                    )
                await session.flush()
            for draft in chunks:
                if draft.chunk_kind != "retrieval_child":
                    continue
                embedding = await session.get(
                    NutritionChunkEmbeddingRecord,
                    (draft.id, embedding_profile_id),
                )
                if embedding is None:
                    session.add(
                        NutritionChunkEmbeddingRecord(
                            chunk_id=draft.id,
                            embedding_profile_id=embedding_profile_id,
                            embedding=None,
                            content_sha256=draft.content_sha256,
                            status="pending",
                        )
                    )
            await session.flush()
            return sum(draft.chunk_kind == "retrieval_child" for draft in chunks)

    async def list_pending_embeddings(
        self,
        *,
        source_id: str,
        embedding_profile_id: str = DEFAULT_EMBEDDING_PROFILE_ID,
        limit: int = 64,
    ) -> tuple[PendingNutritionEmbedding, ...]:
        if not 1 <= limit <= 64:
            raise ValueError("Embedding batch must contain 1 to 64 chunks")
        statement = (
            select(NutritionChunkEmbeddingRecord, NutritionRagChunkRecord)
            .join(
                NutritionRagChunkRecord,
                NutritionRagChunkRecord.id == NutritionChunkEmbeddingRecord.chunk_id,
            )
            .where(
                NutritionRagChunkRecord.source_id == source_id,
                NutritionChunkEmbeddingRecord.embedding_profile_id == embedding_profile_id,
                NutritionChunkEmbeddingRecord.status == "pending",
            )
            .order_by(NutritionRagChunkRecord.ordinal)
            .limit(limit)
        )
        async with self.database.session() as session:
            rows = (await session.execute(statement)).tuples()
            result: list[PendingNutritionEmbedding] = []
            for embedding, chunk in rows:
                metadata = _load_json(chunk.metadata_json)
                embedding_text = metadata.get("embedding_text")
                result.append(
                    PendingNutritionEmbedding(
                        chunk_id=chunk.id,
                        content_sha256=embedding.content_sha256,
                        embedding_text=(
                            embedding_text
                            if isinstance(embedding_text, str)
                            else chunk.content_text
                        ),
                    )
                )
            return tuple(result)

    async def save_embeddings(
        self,
        *,
        items: Sequence[PendingNutritionEmbedding],
        vectors: Sequence[Sequence[float]],
        embedding_profile_id: str,
        dimensions: int,
        provider_request_id: str | None,
        prompt_tokens: int,
        latency_ms: int,
    ) -> None:
        if len(items) != len(vectors) or not items:
            raise ValueError("Embedding inputs and outputs must have equal nonzero length")
        if any(len(vector) != dimensions for vector in vectors):
            raise ValueError("Embedding vector dimensions do not match the profile")
        async with self.database.session() as session, session.begin():
            for item, vector in zip(items, vectors, strict=True):
                row = await session.get(
                    NutritionChunkEmbeddingRecord,
                    (item.chunk_id, embedding_profile_id),
                    with_for_update=True,
                )
                if row is None or row.content_sha256 != item.content_sha256:
                    raise NutritionRagConflict("Embedding target changed during indexing")
                row.embedding = [float(value) for value in vector]
                row.status = "ready"
                row.provider_request_id = provider_request_id
                row.prompt_tokens = prompt_tokens
                row.latency_ms = latency_ms
                row.error_code = None
                row.embedded_at = utc_now()

    async def append_source_review(
        self,
        *,
        source_id: str,
        review_type: str,
        decision: str,
        actor: str,
        attestations: Mapping[str, Any],
        reason: str | None = None,
    ) -> NutritionReviewState:
        if review_type not in {"content", "applicability", "rights"}:
            raise ValueError("Unsupported source review type")
        if decision not in {"approve", "reject", "revoke"}:
            raise ValueError("Unsupported source review decision")
        if decision != "approve" and not (reason and reason.strip()):
            raise ValueError("Rejecting or revoking a review requires a reason")
        actor = _text(actor, "actor", 128)
        reason = _text(reason, "reason", 2000) if reason is not None else None
        async with self.database.session() as session, session.begin():
            source = await session.get(NutritionKnowledgeSourceRecord, source_id)
            if source is None:
                raise NutritionRagNotFound("Nutrition source does not exist")
            if source.status in {"retired", "published"}:
                raise NutritionRagGovernanceError(
                    "Published or retired sources cannot receive new intake reviews"
                )
            session.add(
                NutritionKnowledgeReviewEventRecord(
                    id=new_uuid(),
                    subject_type="source",
                    subject_id=source_id,
                    review_type=review_type,
                    decision=decision,
                    attestations_json=_canonical_json(dict(attestations), maximum=16_000),
                    reason=reason,
                    actor=actor,
                    subject_sha256=source.content_sha256,
                )
            )
            await session.flush()
        return await self.get_source_review_state(source_id)

    async def get_source_review_state(self, source_id: str) -> NutritionReviewState:
        async with self.database.session() as session:
            source_exists = await session.get(NutritionKnowledgeSourceRecord, source_id)
            if source_exists is None:
                raise NutritionRagNotFound("Nutrition source does not exist")
            rows = (
                await session.scalars(
                    select(NutritionKnowledgeReviewEventRecord)
                    .where(
                        NutritionKnowledgeReviewEventRecord.subject_type == "source",
                        NutritionKnowledgeReviewEventRecord.subject_id == source_id,
                    )
                    .order_by(
                        NutritionKnowledgeReviewEventRecord.created_at,
                        NutritionKnowledgeReviewEventRecord.id,
                    )
                )
            ).all()
        latest: dict[str, str | None] = {
            "content": None,
            "applicability": None,
            "rights": None,
        }
        for row in rows:
            latest[row.review_type] = row.decision
        return NutritionReviewState(**latest)

    async def mark_source_approved(self, source_id: str) -> None:
        review = await self.get_source_review_state(source_id)
        if not review.approved:
            raise NutritionRagGovernanceError("All three source review domains must be approved")
        async with self.database.session() as session, session.begin():
            source = await session.get(
                NutritionKnowledgeSourceRecord, source_id, with_for_update=True
            )
            if source is None:
                raise NutritionRagNotFound("Nutrition source does not exist")
            pending = await session.scalar(
                select(func.count(NutritionChunkEmbeddingRecord.chunk_id))
                .join(
                    NutritionRagChunkRecord,
                    NutritionRagChunkRecord.id == NutritionChunkEmbeddingRecord.chunk_id,
                )
                .where(
                    NutritionRagChunkRecord.source_id == source_id,
                    NutritionChunkEmbeddingRecord.embedding_profile_id
                    == DEFAULT_EMBEDDING_PROFILE_ID,
                    NutritionChunkEmbeddingRecord.status != "ready",
                )
            )
            if pending:
                raise NutritionRagGovernanceError("Source indexing has not completed")
            if source.status not in {"draft", "rejected", "approved"}:
                raise NutritionRagGovernanceError(
                    "Source cannot be approved from its current state"
                )
            source.status = "approved"
            source.updated_at = utc_now()

    async def list_sources(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid source page")
        statement = select(NutritionKnowledgeSourceRecord)
        count_statement = select(func.count(NutritionKnowledgeSourceRecord.id))
        predicates = []
        if status is not None:
            if status not in {"draft", "approved", "rejected", "published", "retired"}:
                raise ValueError("Invalid source status")
            predicates.append(NutritionKnowledgeSourceRecord.status == status)
        if search and search.strip():
            pattern = f"%{search.strip()[:256]}%"
            predicates.append(
                or_(
                    NutritionKnowledgeSourceRecord.title.ilike(pattern),
                    NutritionKnowledgeSourceRecord.source_key.ilike(pattern),
                    NutritionKnowledgeSourceRecord.publisher.ilike(pattern),
                )
            )
        if predicates:
            statement = statement.where(*predicates)
            count_statement = count_statement.where(*predicates)
        statement = (
            statement.order_by(
                NutritionKnowledgeSourceRecord.created_at.desc(),
                NutritionKnowledgeSourceRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self.database.session() as session:
            rows = tuple(await session.scalars(statement))
            total = int(await session.scalar(count_statement) or 0)
            chunk_counts = dict(
                (
                    await session.execute(
                        select(
                            NutritionRagChunkRecord.source_id,
                            func.count(NutritionRagChunkRecord.id),
                        )
                        .where(
                            NutritionRagChunkRecord.source_id.in_(tuple(row.id for row in rows)),
                            NutritionRagChunkRecord.chunk_kind == "retrieval_child",
                        )
                        .group_by(NutritionRagChunkRecord.source_id)
                    )
                )
                .tuples()
                .all()
            )
            ready_counts = dict(
                (
                    await session.execute(
                        select(
                            NutritionRagChunkRecord.source_id,
                            func.count(NutritionChunkEmbeddingRecord.chunk_id),
                        )
                        .join(
                            NutritionChunkEmbeddingRecord,
                            NutritionChunkEmbeddingRecord.chunk_id == NutritionRagChunkRecord.id,
                        )
                        .where(
                            NutritionRagChunkRecord.source_id.in_(tuple(row.id for row in rows)),
                            NutritionChunkEmbeddingRecord.status == "ready",
                        )
                        .group_by(NutritionRagChunkRecord.source_id)
                    )
                )
                .tuples()
                .all()
            )
        items = []
        for row in rows:
            review = await self.get_source_review_state(row.id)
            metadata = _load_json(row.metadata_json)
            items.append(
                {
                    "id": row.id,
                    "source_key": row.source_key,
                    "version": row.version,
                    "title": row.title,
                    "publisher": row.publisher,
                    "published_at": row.published_at,
                    "source_url": row.source_url,
                    "language": row.language,
                    "content_sha256": row.content_sha256,
                    "char_count": row.char_count,
                    "status": row.status,
                    "asset_id": row.asset_id,
                    "tags": metadata.get("tags", []),
                    "applicability": metadata.get("applicability", []),
                    "review": asdict(review),
                    "review_approved": review.approved,
                    "retrieval_chunk_count": chunk_counts.get(row.id, 0),
                    "ready_embedding_count": ready_counts.get(row.id, 0),
                    "created_at": _aware(row.created_at),
                    "updated_at": _aware(row.updated_at),
                }
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def get_source_detail(self, source_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            source = await session.get(NutritionKnowledgeSourceRecord, source_id)
            if source is None:
                return None
            asset = (
                await session.get(NutritionKnowledgeAssetRecord, source.asset_id)
                if source.asset_id is not None
                else None
            )
            labels = tuple(
                (
                    await session.execute(
                        select(
                            NutritionKnowledgeSourceLabelRecord.kind,
                            NutritionKnowledgeSourceLabelRecord.value,
                        )
                        .where(NutritionKnowledgeSourceLabelRecord.source_id == source_id)
                        .order_by(
                            NutritionKnowledgeSourceLabelRecord.kind,
                            NutritionKnowledgeSourceLabelRecord.value,
                        )
                    )
                ).tuples()
            )
            section_count = int(
                await session.scalar(
                    select(func.count(NutritionKnowledgeSectionRecord.id)).where(
                        NutritionKnowledgeSectionRecord.source_id == source_id
                    )
                )
                or 0
            )
            chunk_count = int(
                await session.scalar(
                    select(func.count(NutritionRagChunkRecord.id)).where(
                        NutritionRagChunkRecord.source_id == source_id
                    )
                )
                or 0
            )
            reviews = tuple(
                await session.scalars(
                    select(NutritionKnowledgeReviewEventRecord)
                    .where(
                        NutritionKnowledgeReviewEventRecord.subject_type == "source",
                        NutritionKnowledgeReviewEventRecord.subject_id == source_id,
                    )
                    .order_by(
                        NutritionKnowledgeReviewEventRecord.created_at,
                        NutritionKnowledgeReviewEventRecord.id,
                    )
                )
            )
        review_state = await self.get_source_review_state(source_id)
        return {
            "source": {
                "id": source.id,
                "source_key": source.source_key,
                "version": source.version,
                "title": source.title,
                "publisher": source.publisher,
                "published_at": source.published_at,
                "source_url": source.source_url,
                "language": source.language,
                "content_sha256": source.content_sha256,
                "char_count": source.char_count,
                "status": source.status,
                "parser_profile_key": source.parser_profile_key,
                "created_at": _aware(source.created_at),
                "updated_at": _aware(source.updated_at),
            },
            "asset": asdict(self._asset(asset)) if asset is not None else None,
            "labels": [{"kind": kind, "value": value} for kind, value in labels],
            "review": asdict(review_state),
            "review_approved": review_state.approved,
            "section_count": section_count,
            "chunk_count": chunk_count,
            "reviews": [
                {
                    "id": review.id,
                    "review_type": review.review_type,
                    "decision": review.decision,
                    "attestations": _load_json(review.attestations_json),
                    "reason": review.reason,
                    "actor": review.actor,
                    "subject_sha256": review.subject_sha256,
                    "created_at": _aware(review.created_at),
                }
                for review in reviews
            ],
        }

    async def list_source_sections(
        self, source_id: str, *, limit: int, offset: int
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid section page")
        async with self.database.session() as session:
            total = int(
                await session.scalar(
                    select(func.count(NutritionKnowledgeSectionRecord.id)).where(
                        NutritionKnowledgeSectionRecord.source_id == source_id
                    )
                )
                or 0
            )
            rows = tuple(
                await session.scalars(
                    select(NutritionKnowledgeSectionRecord)
                    .where(NutritionKnowledgeSectionRecord.source_id == source_id)
                    .order_by(NutritionKnowledgeSectionRecord.ordinal)
                    .limit(limit)
                    .offset(offset)
                )
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "ordinal": row.ordinal,
                    "heading_path": json.loads(row.heading_path_json),
                    "page_from": row.page_from,
                    "page_to": row.page_to,
                    "content": row.content_text,
                    "content_sha256": row.content_sha256,
                }
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def list_source_chunks(
        self,
        source_id: str,
        *,
        kind: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid chunk page")
        predicates = [NutritionRagChunkRecord.source_id == source_id]
        if kind is not None:
            predicates.append(NutritionRagChunkRecord.chunk_kind == kind)
        async with self.database.session() as session:
            total = int(
                await session.scalar(
                    select(func.count(NutritionRagChunkRecord.id)).where(*predicates)
                )
                or 0
            )
            rows = tuple(
                await session.scalars(
                    select(NutritionRagChunkRecord)
                    .where(*predicates)
                    .order_by(NutritionRagChunkRecord.ordinal)
                    .limit(limit)
                    .offset(offset)
                )
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "section_id": row.section_id,
                    "parent_chunk_id": row.parent_chunk_id,
                    "kind": row.chunk_kind,
                    "ordinal": row.ordinal,
                    "page_from": row.page_from,
                    "page_to": row.page_to,
                    "content": row.content_text,
                    "content_sha256": row.content_sha256,
                    "lexical_terms": row.lexical_terms,
                    "token_count": row.token_count,
                    "char_count": row.char_count,
                }
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def retire_source(self, source_id: str, *, actor: str, reason: str) -> None:
        actor = _text(actor, "actor", 128)
        reason = _text(reason, "reason", 2000)
        async with self.database.session() as session, session.begin():
            source = await session.get(
                NutritionKnowledgeSourceRecord, source_id, with_for_update=True
            )
            if source is None:
                raise NutritionRagNotFound("Nutrition source does not exist")
            active_membership = await session.scalar(
                select(NutritionCorpusReleaseSourceRecord.source_id)
                .join(
                    NutritionCorpusRuntimeRecord,
                    NutritionCorpusRuntimeRecord.active_release_id
                    == NutritionCorpusReleaseSourceRecord.release_id,
                )
                .where(
                    NutritionCorpusRuntimeRecord.singleton_key == "default",
                    NutritionCorpusReleaseSourceRecord.source_id == source_id,
                )
            )
            if active_membership is not None:
                raise NutritionRagGovernanceError(
                    "An active release source must be replaced by a new release before retirement"
                )
            if source.status == "retired":
                return
            source.status = "retired"
            source.retired_at = utc_now()
            source.updated_at = source.retired_at
            session.add(
                NutritionKnowledgeReviewEventRecord(
                    id=new_uuid(),
                    subject_type="source",
                    subject_id=source_id,
                    review_type="content",
                    decision="revoke",
                    attestations_json="{}",
                    reason=reason,
                    actor=actor,
                    subject_sha256=source.content_sha256,
                )
            )

    async def create_release(
        self,
        *,
        version: str,
        source_ids: Sequence[str],
        created_by: str,
    ) -> NutritionRelease:
        version = _text(version, "version", 128)
        created_by = _text(created_by, "created_by", 128)
        unique_ids = tuple(dict.fromkeys(source_ids))
        if not unique_ids:
            raise ValueError("A nutrition release requires at least one source")
        async with self.database.session() as session, session.begin():
            sources = tuple(
                await session.scalars(
                    select(NutritionKnowledgeSourceRecord)
                    .where(NutritionKnowledgeSourceRecord.id.in_(unique_ids))
                    .order_by(NutritionKnowledgeSourceRecord.id)
                )
            )
            if len(sources) != len(unique_ids):
                raise NutritionRagNotFound("One or more release sources do not exist")
            if any(source.status != "approved" for source in sources):
                raise NutritionRagGovernanceError("Every release source must be approved")
            manifest = {
                "version": version,
                "sources": [
                    {"id": source.id, "sha256": source.content_sha256} for source in sources
                ],
                "embedding_profile_id": DEFAULT_EMBEDDING_PROFILE_ID,
                "lexical_profile_id": DEFAULT_LEXICAL_PROFILE_ID,
                "retrieval_profile_id": DEFAULT_RETRIEVAL_PROFILE_ID,
                "chunker_profile_key": CHUNKER_PROFILE_KEY,
            }
            manifest_sha256 = hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
            existing = await session.scalar(
                select(NutritionCorpusReleaseRecord).where(
                    or_(
                        NutritionCorpusReleaseRecord.version == version,
                        NutritionCorpusReleaseRecord.manifest_sha256 == manifest_sha256,
                    )
                )
            )
            if existing is not None:
                return await self._release(session, existing)
            row = NutritionCorpusReleaseRecord(
                id=new_uuid(),
                version=version,
                status="indexing_check",
                embedding_profile_id=DEFAULT_EMBEDDING_PROFILE_ID,
                lexical_profile_id=DEFAULT_LEXICAL_PROFILE_ID,
                retrieval_profile_id=DEFAULT_RETRIEVAL_PROFILE_ID,
                chunker_profile_key=CHUNKER_PROFILE_KEY,
                manifest_sha256=manifest_sha256,
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            session.add_all(
                [
                    NutritionCorpusReleaseSourceRecord(
                        release_id=row.id,
                        source_id=source.id,
                        source_content_sha256=source.content_sha256,
                    )
                    for source in sources
                ]
            )
            await session.flush()
            return await self._release(session, row)

    async def complete_release_index_check(self, release_id: str) -> NutritionRelease:
        async with self.database.session() as session, session.begin():
            release = await session.get(
                NutritionCorpusReleaseRecord, release_id, with_for_update=True
            )
            if release is None:
                raise NutritionRagNotFound("Nutrition release does not exist")
            if release.status not in {"indexing_check", "evaluating"}:
                raise NutritionRagGovernanceError("Release is not awaiting index validation")
            counts = (
                await session.execute(
                    select(
                        func.count(NutritionRagChunkRecord.id),
                        func.count(NutritionChunkEmbeddingRecord.chunk_id),
                    )
                    .select_from(NutritionCorpusReleaseSourceRecord)
                    .join(
                        NutritionRagChunkRecord,
                        NutritionRagChunkRecord.source_id
                        == NutritionCorpusReleaseSourceRecord.source_id,
                    )
                    .outerjoin(
                        NutritionChunkEmbeddingRecord,
                        and_(
                            NutritionChunkEmbeddingRecord.chunk_id == NutritionRagChunkRecord.id,
                            NutritionChunkEmbeddingRecord.embedding_profile_id
                            == release.embedding_profile_id,
                            NutritionChunkEmbeddingRecord.status == "ready",
                        ),
                    )
                    .where(
                        NutritionCorpusReleaseSourceRecord.release_id == release_id,
                        NutritionRagChunkRecord.chunker_profile_key == release.chunker_profile_key,
                        NutritionRagChunkRecord.chunk_kind == "retrieval_child",
                    )
                )
            ).one()
            if counts[0] == 0 or counts[0] != counts[1]:
                raise NutritionRagGovernanceError("Release has missing retrieval embeddings")
            release.status = "evaluating"
            await session.flush()
            return await self._release(session, release)

    async def create_evaluation_dataset(
        self,
        *,
        version: str,
        cases: Sequence[Mapping[str, Any]],
        created_by: str,
    ) -> NutritionEvaluationDataset:
        version = _text(version, "version", 128)
        created_by = _text(created_by, "created_by", 128)
        if not 1 <= len(cases) <= 1000:
            raise ValueError("An evaluation dataset requires 1 to 1000 cases")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in cases:
            case_key = _text(str(raw.get("case_key", "")), "case_key", 128)
            if case_key in seen:
                raise ValueError("Evaluation case keys must be unique")
            seen.add(case_key)
            query_plan = raw.get("query_plan_input")
            if not isinstance(query_plan, Mapping):
                raise ValueError("Evaluation query_plan_input must be an object")
            query = _text(str(query_plan.get("query", "")), "query", 1000)
            filters = query_plan.get("metadata_filter", {})
            if not isinstance(filters, Mapping):
                raise ValueError("Evaluation metadata_filter must be an object")
            expected_outcome = str(raw.get("expected_outcome", "evidence"))
            if expected_outcome not in {"evidence", "insufficient"}:
                raise ValueError("Invalid expected evaluation outcome")
            normalized.append(
                {
                    "case_key": case_key,
                    "query_plan_input": {"query": query, "metadata_filter": dict(filters)},
                    "expected_source_keys": list(
                        _labels(_mapping_strings(raw, "expected_source_keys"))
                    ),
                    "expected_chunk_concepts": list(
                        _labels(_mapping_strings(raw, "expected_chunk_concepts"))
                    ),
                    "forbidden_source_keys": list(
                        _labels(_mapping_strings(raw, "forbidden_source_keys"))
                    ),
                    "expected_outcome": expected_outcome,
                }
            )
        manifest = {"version": version, "cases": normalized}
        manifest_sha256 = hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
        negative_count = sum(item["expected_outcome"] == "insufficient" for item in normalized)
        dataset_status = (
            "ready"
            if len(normalized) >= 100 and negative_count / len(normalized) >= 0.25
            else "draft"
        )
        async with self.database.session() as session, session.begin():
            existing = await session.scalar(
                select(NutritionEvaluationDatasetRecord).where(
                    or_(
                        NutritionEvaluationDatasetRecord.version == version,
                        NutritionEvaluationDatasetRecord.manifest_sha256 == manifest_sha256,
                    )
                )
            )
            if existing is not None:
                return await self._dataset(session, existing)
            dataset = NutritionEvaluationDatasetRecord(
                id=new_uuid(),
                version=version,
                manifest_sha256=manifest_sha256,
                status=dataset_status,
                created_by=created_by,
            )
            session.add(dataset)
            await session.flush()
            session.add_all(
                [
                    NutritionEvaluationCaseRecord(
                        id=new_uuid(),
                        dataset_id=dataset.id,
                        case_key=item["case_key"],
                        query_plan_input_json=_canonical_json(item["query_plan_input"]),
                        expected_source_keys_json=_canonical_json(item["expected_source_keys"]),
                        expected_chunk_concepts_json=_canonical_json(
                            item["expected_chunk_concepts"]
                        ),
                        forbidden_source_keys_json=_canonical_json(item["forbidden_source_keys"]),
                        expected_outcome=item["expected_outcome"],
                    )
                    for item in normalized
                ]
            )
            await session.flush()
            return await self._dataset(session, dataset)

    async def list_evaluation_datasets(self) -> tuple[NutritionEvaluationDataset, ...]:
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(NutritionEvaluationDatasetRecord).order_by(
                        NutritionEvaluationDatasetRecord.created_at.desc()
                    )
                )
            )
            return tuple([await self._dataset(session, row) for row in rows])

    async def create_evaluation_run(
        self,
        *,
        release_id: str,
        dataset_id: str,
        created_by: str,
        idempotency_key: str,
    ) -> tuple[str, NutritionJob]:
        created_by = _text(created_by, "created_by", 128)
        idempotency_key = _text(idempotency_key, "idempotency_key", 200)
        existing_job = await self.get_job_by_idempotency_key(f"evaluate:{idempotency_key}")
        if existing_job is not None and existing_job.subject_id is not None:
            return existing_job.subject_id, existing_job
        async with self.database.session() as session, session.begin():
            release = await session.get(
                NutritionCorpusReleaseRecord, release_id, with_for_update=True
            )
            dataset = await session.get(NutritionEvaluationDatasetRecord, dataset_id)
            if release is None or dataset is None:
                raise NutritionRagNotFound("Release or evaluation dataset does not exist")
            if release.status not in {"indexing_check", "evaluating", "review_ready"}:
                raise NutritionRagGovernanceError(
                    "Release cannot be evaluated in its current state"
                )
            if dataset.status != "ready":
                raise NutritionRagGovernanceError("Evaluation dataset is not ready")
            run = NutritionEvaluationRunRecord(
                id=new_uuid(),
                release_id=release_id,
                retrieval_profile_id=release.retrieval_profile_id,
                dataset_id=dataset_id,
                status="queued",
                created_by=created_by,
            )
            release.status = "evaluating"
            session.add(run)
            await session.flush()
            run_id = run.id
        job = await self.enqueue_job(
            job_type="evaluate",
            subject_type="evaluation_run",
            subject_id=run_id,
            input={"evaluation_run_id": run_id},
            idempotency_key=f"evaluate:{idempotency_key}",
            created_by=created_by,
            max_attempts=2,
        )
        return run_id, job

    async def list_evaluation_runs(self, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Invalid evaluation run limit")
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(NutritionEvaluationRunRecord)
                    .order_by(NutritionEvaluationRunRecord.created_at.desc())
                    .limit(limit)
                )
            )
        return tuple(self._evaluation_run(row) for row in rows)

    async def get_evaluation_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = await session.get(NutritionEvaluationRunRecord, run_id)
            if row is None:
                return None
            results = tuple(
                await session.scalars(
                    select(NutritionEvaluationResultRecord)
                    .where(NutritionEvaluationResultRecord.run_id == run_id)
                    .order_by(NutritionEvaluationResultRecord.id)
                )
            )
        value = self._evaluation_run(row)
        value["results"] = [
            {
                "id": item.id,
                "case_id": item.case_id,
                "retrieval_run_id": item.retrieval_run_id,
                "passed": item.passed,
                "rank": item.rank,
                "details": _load_json(item.details_json),
            }
            for item in results
        ]
        return value

    async def list_releases(self) -> tuple[NutritionRelease, ...]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(NutritionCorpusReleaseRecord).order_by(
                    NutritionCorpusReleaseRecord.created_at.desc()
                )
            )
            return tuple([await self._release(session, row) for row in rows])

    async def get_release(self, release_id: str) -> NutritionRelease | None:
        async with self.database.session() as session:
            row = await session.get(NutritionCorpusReleaseRecord, release_id)
            return await self._release(session, row) if row is not None else None

    async def review_release(
        self,
        release_id: str,
        *,
        decision: str,
        actor: str,
        reason: str | None,
    ) -> NutritionRelease:
        if decision not in {"approve", "reject"}:
            raise ValueError("Unsupported release decision")
        if decision == "reject" and not (reason and reason.strip()):
            raise ValueError("Rejecting a release requires a reason")
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionCorpusReleaseRecord, release_id, with_for_update=True)
            if row is None:
                raise NutritionRagNotFound("Nutrition release does not exist")
            if row.status not in {"review_ready", "rejected"}:
                raise NutritionRagGovernanceError("Release is not ready for review")
            if decision == "approve":
                evaluation = (
                    await session.get(NutritionEvaluationRunRecord, row.evaluation_run_id)
                    if row.evaluation_run_id is not None
                    else None
                )
                if evaluation is None or evaluation.status != "succeeded":
                    raise NutritionRagGovernanceError(
                        "A passing evaluation run is required before release approval"
                    )
                metrics = (
                    _load_json(evaluation.metrics_json)
                    if evaluation.metrics_json is not None
                    else {}
                )
                if metrics.get("gates_passed") is not True:
                    raise NutritionRagGovernanceError(
                        "Release evaluation did not pass the frozen quality gates"
                    )
            row.status = "approved" if decision == "approve" else "rejected"
            row.approved_at = utc_now() if decision == "approve" else None
            session.add(
                NutritionKnowledgeReviewEventRecord(
                    id=new_uuid(),
                    subject_type="release",
                    subject_id=row.id,
                    review_type="release_acceptance",
                    decision=decision,
                    attestations_json="{}",
                    reason=reason,
                    actor=_text(actor, "actor", 128),
                    subject_sha256=row.manifest_sha256,
                )
            )
            await session.flush()
            return await self._release(session, row)

    async def activate_release(
        self,
        release_id: str,
        *,
        actor: str,
        reason: str,
        rollback: bool = False,
    ) -> NutritionRuntime:
        actor = _text(actor, "actor", 128)
        reason = _text(reason, "reason", 1000)
        async with self.database.session() as session, session.begin():
            release = await session.get(
                NutritionCorpusReleaseRecord, release_id, with_for_update=True
            )
            if release is None:
                raise NutritionRagNotFound("Nutrition release does not exist")
            if release.status not in {"approved", "active", "retired"}:
                raise NutritionRagGovernanceError("Only approved releases can be activated")
            runtime = await session.get(
                NutritionCorpusRuntimeRecord, "default", with_for_update=True
            )
            if runtime is None:
                raise NutritionRagConflict("Nutrition runtime is not initialized")
            previous_id = runtime.active_release_id
            if previous_id == release_id:
                return await self._runtime(session, runtime)
            if previous_id is not None:
                previous = await session.get(NutritionCorpusReleaseRecord, previous_id)
                if previous is not None:
                    previous.status = "retired"
                    previous.retired_at = utc_now()
            runtime.active_release_id = release.id
            runtime.runtime_revision += 1
            runtime.updated_by = actor
            runtime.updated_at = utc_now()
            release.status = "active"
            release.activated_at = utc_now()
            release.retired_at = None
            session.add(
                NutritionCorpusActivationEventRecord(
                    id=new_uuid(),
                    previous_release_id=previous_id,
                    activated_release_id=release.id,
                    action="rollback" if rollback else "activate",
                    actor=actor,
                    reason=reason,
                    runtime_revision=runtime.runtime_revision,
                )
            )
            await session.flush()
            return await self._runtime(session, runtime)

    async def get_runtime(self) -> NutritionRuntime:
        async with self.database.session() as session:
            row = await session.get(NutritionCorpusRuntimeRecord, "default")
            if row is None:
                raise NutritionRagConflict("Nutrition runtime is not initialized")
            return await self._runtime(session, row)

    async def get_active_profile(self) -> tuple[NutritionRelease, RetrievalProfile] | None:
        async with self.database.session() as session:
            runtime = await session.get(NutritionCorpusRuntimeRecord, "default")
            if runtime is None or runtime.active_release_id is None:
                return None
            release_row = await session.get(NutritionCorpusReleaseRecord, runtime.active_release_id)
            if release_row is None or release_row.status != "active":
                return None
            return await self._profile_bundle(session, release_row)

    async def get_release_profile(
        self, release_id: str
    ) -> tuple[NutritionRelease, RetrievalProfile] | None:
        async with self.database.session() as session:
            release_row = await session.get(NutritionCorpusReleaseRecord, release_id)
            if release_row is None or release_row.status in {"draft", "rejected"}:
                return None
            return await self._profile_bundle(session, release_row)

    async def _profile_bundle(
        self,
        session: AsyncSession,
        release_row: NutritionCorpusReleaseRecord,
    ) -> tuple[NutritionRelease, RetrievalProfile] | None:
        profile_row = await session.get(
            NutritionRetrievalProfileRecord, release_row.retrieval_profile_id
        )
        if profile_row is None or profile_row.status != "ready":
            return None
        embedding = await session.get(
            NutritionEmbeddingProfileRecord, profile_row.embedding_profile_id
        )
        if embedding is None or embedding.status != "ready":
            return None
        return (
            await self._release(session, release_row),
            RetrievalProfile(
                id=profile_row.id,
                profile_key=profile_row.profile_key,
                embedding_profile_id=profile_row.embedding_profile_id,
                lexical_profile_id=profile_row.lexical_profile_id,
                dimensions=embedding.dimensions,
                dense_top_k=profile_row.dense_top_k,
                lexical_top_k=profile_row.lexical_top_k,
                phrase_top_k=profile_row.phrase_top_k,
                rrf_k=profile_row.rrf_k,
                rerank_top_n=profile_row.rerank_top_n,
                final_top_k=profile_row.final_top_k,
                min_rerank_score=profile_row.min_rerank_score,
                max_context_chars=profile_row.max_context_chars,
                query_plan_version=profile_row.query_plan_version,
            ),
        )

    async def dashboard(self) -> dict[str, Any]:
        async with self.database.session() as session:
            source_rows = (
                (
                    await session.execute(
                        select(
                            NutritionKnowledgeSourceRecord.status,
                            func.count(NutritionKnowledgeSourceRecord.id),
                        ).group_by(NutritionKnowledgeSourceRecord.status)
                    )
                )
                .tuples()
                .all()
            )
            job_rows = (
                (
                    await session.execute(
                        select(
                            NutritionKnowledgeJobRecord.status,
                            func.count(NutritionKnowledgeJobRecord.id),
                        ).group_by(NutritionKnowledgeJobRecord.status)
                    )
                )
                .tuples()
                .all()
            )
            ready_embeddings = await session.scalar(
                select(func.count(NutritionChunkEmbeddingRecord.chunk_id)).where(
                    NutritionChunkEmbeddingRecord.status == "ready"
                )
            )
            pending_embeddings = await session.scalar(
                select(func.count(NutritionChunkEmbeddingRecord.chunk_id)).where(
                    NutritionChunkEmbeddingRecord.status != "ready"
                )
            )
        runtime = await self.get_runtime()
        return {
            "sources": dict(source_rows),
            "jobs": dict(job_rows),
            "ready_embeddings": ready_embeddings or 0,
            "pending_embeddings": pending_embeddings or 0,
            "runtime": asdict(runtime),
        }

    async def get_retrieval_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            run = await session.get(NutritionRetrievalRunRecord, run_id)
            if run is None:
                return None
            rows = tuple(
                (
                    await session.execute(
                        select(
                            NutritionRetrievalCandidateRecord,
                            NutritionRagChunkRecord,
                            NutritionKnowledgeSourceRecord,
                        )
                        .join(
                            NutritionRagChunkRecord,
                            NutritionRagChunkRecord.id
                            == NutritionRetrievalCandidateRecord.chunk_id,
                        )
                        .join(
                            NutritionKnowledgeSourceRecord,
                            NutritionKnowledgeSourceRecord.id == NutritionRagChunkRecord.source_id,
                        )
                        .where(NutritionRetrievalCandidateRecord.retrieval_run_id == run_id)
                        .order_by(
                            NutritionRetrievalCandidateRecord.rerank_rank,
                            NutritionRetrievalCandidateRecord.rrf_rank,
                        )
                    )
                ).tuples()
            )
            parent_ids = tuple(
                chunk.parent_chunk_id for _, chunk, _ in rows if chunk.parent_chunk_id is not None
            )
            parents = {
                row.id: row
                for row in await session.scalars(
                    select(NutritionRagChunkRecord).where(
                        NutritionRagChunkRecord.id.in_(parent_ids)
                    )
                )
            }
        return {
            "id": run.id,
            "invocation_id": run.invocation_id,
            "release_id": run.release_id,
            "retrieval_profile_id": run.retrieval_profile_id,
            "query_plan": _load_json(run.query_plan_json),
            "query_hash": run.query_hash,
            "safe_query_summary": run.safe_query_summary,
            "status": run.status,
            "provider_usage": _load_json(run.provider_usage_json),
            "total_latency_ms": run.total_latency_ms,
            "created_at": _aware(run.created_at),
            "candidates": [
                {
                    "id": candidate.id,
                    "chunk_id": chunk.id,
                    "parent_chunk_id": chunk.parent_chunk_id,
                    "source_id": source.id,
                    "source_key": source.source_key,
                    "source_title": source.title,
                    "child_content": chunk.content_text,
                    "parent_context": (
                        parents[chunk.parent_chunk_id].content_text
                        if chunk.parent_chunk_id in parents
                        else chunk.content_text
                    ),
                    "dense_rank": candidate.dense_rank,
                    "dense_score": candidate.dense_score,
                    "lexical_rank": candidate.lexical_rank,
                    "lexical_score": candidate.lexical_score,
                    "phrase_rank": candidate.phrase_rank,
                    "phrase_score": candidate.phrase_score,
                    "rrf_rank": candidate.rrf_rank,
                    "rrf_score": candidate.rrf_score,
                    "rerank_rank": candidate.rerank_rank,
                    "rerank_score": candidate.rerank_score,
                    "selection_status": candidate.selection_status,
                    "rejection_reason": candidate.rejection_reason,
                }
                for candidate, chunk, source in rows
            ],
        }

    async def _locked_owned_job(
        self,
        session: AsyncSession,
        *,
        job_id: str,
        worker_id: str,
    ) -> NutritionKnowledgeJobRecord:
        row = await session.get(NutritionKnowledgeJobRecord, job_id, with_for_update=True)
        if row is None:
            raise NutritionRagNotFound("Nutrition job does not exist")
        if row.status != "running" or row.lease_owner != worker_id:
            raise NutritionRagConflict("Nutrition job lease is no longer owned by this worker")
        return row

    async def _append_job_event(
        self,
        session: AsyncSession,
        *,
        row: NutritionKnowledgeJobRecord,
        stage: str,
        level: str,
        message: str,
    ) -> None:
        last_sequence = await session.scalar(
            select(func.max(NutritionKnowledgeJobEventRecord.sequence)).where(
                NutritionKnowledgeJobEventRecord.job_id == row.id
            )
        )
        session.add(
            NutritionKnowledgeJobEventRecord(
                id=new_uuid(),
                job_id=row.id,
                sequence=(last_sequence or 0) + 1,
                stage=_text(stage, "stage", 64),
                level=_text(level, "level", 16),
                message=_text(message, "message", 1000),
                completed_items=row.completed_items,
                total_items=row.total_items,
            )
        )

    @staticmethod
    def _evaluation_run(row: NutritionEvaluationRunRecord) -> dict[str, Any]:
        return {
            "id": row.id,
            "release_id": row.release_id,
            "retrieval_profile_id": row.retrieval_profile_id,
            "dataset_id": row.dataset_id,
            "status": row.status,
            "metrics": _load_json(row.metrics_json) if row.metrics_json else None,
            "result_sha256": row.result_sha256,
            "created_by": row.created_by,
            "created_at": _aware(row.created_at),
            "completed_at": _optional_aware(row.completed_at),
        }

    @staticmethod
    async def _dataset(
        session: AsyncSession, row: NutritionEvaluationDatasetRecord
    ) -> NutritionEvaluationDataset:
        rows = tuple(
            await session.scalars(
                select(NutritionEvaluationCaseRecord)
                .where(NutritionEvaluationCaseRecord.dataset_id == row.id)
                .order_by(NutritionEvaluationCaseRecord.case_key)
            )
        )
        return NutritionEvaluationDataset(
            id=row.id,
            version=row.version,
            manifest_sha256=row.manifest_sha256,
            status=row.status,
            cases=tuple(
                NutritionEvaluationCase(
                    id=item.id,
                    case_key=item.case_key,
                    query_plan_input=_load_json(item.query_plan_input_json),
                    expected_source_keys=_load_string_tuple(item.expected_source_keys_json),
                    expected_chunk_concepts=_load_string_tuple(item.expected_chunk_concepts_json),
                    forbidden_source_keys=_load_string_tuple(item.forbidden_source_keys_json),
                    expected_outcome=item.expected_outcome,
                )
                for item in rows
            ),
            created_by=row.created_by,
            created_at=_aware(row.created_at),
        )

    @staticmethod
    async def _release(
        session: AsyncSession, row: NutritionCorpusReleaseRecord
    ) -> NutritionRelease:
        source_ids = tuple(
            await session.scalars(
                select(NutritionCorpusReleaseSourceRecord.source_id)
                .where(NutritionCorpusReleaseSourceRecord.release_id == row.id)
                .order_by(NutritionCorpusReleaseSourceRecord.source_id)
            )
        )
        return NutritionRelease(
            id=row.id,
            version=row.version,
            status=row.status,
            manifest_sha256=row.manifest_sha256,
            source_ids=source_ids,
            embedding_profile_id=row.embedding_profile_id,
            lexical_profile_id=row.lexical_profile_id,
            retrieval_profile_id=row.retrieval_profile_id,
            chunker_profile_key=row.chunker_profile_key,
            evaluation_run_id=row.evaluation_run_id,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            approved_at=_optional_aware(row.approved_at),
            activated_at=_optional_aware(row.activated_at),
            retired_at=_optional_aware(row.retired_at),
        )

    @staticmethod
    async def _runtime(
        session: AsyncSession, row: NutritionCorpusRuntimeRecord
    ) -> NutritionRuntime:
        version: str | None = None
        if row.active_release_id is not None:
            version = await session.scalar(
                select(NutritionCorpusReleaseRecord.version).where(
                    NutritionCorpusReleaseRecord.id == row.active_release_id
                )
            )
        return NutritionRuntime(
            active_release_id=row.active_release_id,
            active_release_version=version,
            runtime_revision=row.runtime_revision,
            updated_by=row.updated_by,
            updated_at=_aware(row.updated_at),
        )

    @staticmethod
    def _asset(row: NutritionKnowledgeAssetRecord) -> NutritionAsset:
        return NutritionAsset(
            id=row.id,
            storage_key=row.storage_key,
            bucket=row.bucket,
            region=row.region,
            original_filename=row.original_filename,
            media_type=row.media_type,
            byte_size=row.byte_size,
            sha256=row.sha256,
            etag=row.etag,
            source_method=row.source_method,
            source_url=row.source_url,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
        )

    @staticmethod
    def _job(row: NutritionKnowledgeJobRecord) -> NutritionJob:
        return NutritionJob(
            id=row.id,
            job_type=row.job_type,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            status=row.status,
            stage=row.stage,
            completed_items=row.completed_items,
            total_items=row.total_items,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=_optional_aware(row.lease_expires_at),
            available_at=_aware(row.available_at),
            idempotency_key=row.idempotency_key,
            input=_load_json(row.input_json),
            output=_load_json(row.output_json) if row.output_json is not None else None,
            error_code=row.error_code,
            safe_error_message=row.safe_error_message,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            started_at=_optional_aware(row.started_at),
            finished_at=_optional_aware(row.finished_at),
        )

    @staticmethod
    def _job_event(row: NutritionKnowledgeJobEventRecord) -> NutritionJobEvent:
        return NutritionJobEvent(
            id=row.id,
            job_id=row.job_id,
            sequence=row.sequence,
            stage=row.stage,
            level=row.level,
            message=row.message,
            completed_items=row.completed_items,
            total_items=row.total_items,
            created_at=_aware(row.created_at),
        )


def _text(value: str | None, field: str, maximum: int) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    return normalized


def _labels(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values))
    if any(not value or len(value) > 128 for value in normalized):
        raise ValueError("Nutrition labels must contain 1 to 128 characters")
    return normalized


def _mapping_strings(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw = value.get(key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{key} must be a string list")
    if any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must be a string list")
    return tuple(raw)


def _canonical_json(value: Any, maximum: int = 128_000) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded) > maximum:
        raise ValueError("Serialized nutrition data exceeds its size limit")
    return encoded


def _load_json(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise NutritionRagConflict("Stored nutrition JSON is not an object")
    return decoded


def _load_string_tuple(value: str) -> tuple[str, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise NutritionRagConflict("Stored nutrition string list is invalid")
    return tuple(decoded)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None


__all__ = [
    "NutritionAsset",
    "NutritionEvaluationCase",
    "NutritionEvaluationDataset",
    "NutritionJob",
    "NutritionJobEvent",
    "NutritionRagConflict",
    "NutritionRagControlError",
    "NutritionRagGovernanceError",
    "NutritionRagNotFound",
    "NutritionRagRepository",
    "NutritionRelease",
    "NutritionReviewState",
    "NutritionRuntime",
    "PendingNutritionEmbedding",
    "RetrievalProfile",
]
