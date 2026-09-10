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
    NutritionKnowledgeAssetRecord,
    NutritionKnowledgeJobEventRecord,
    NutritionKnowledgeJobRecord,
    NutritionKnowledgeReviewEventRecord,
    NutritionKnowledgeSectionRecord,
    NutritionKnowledgeSourceLabelRecord,
    NutritionKnowledgeSourceRecord,
    NutritionRagChunkRecord,
    NutritionRetrievalProfileRecord,
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
                status="review_ready",
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
                await session.execute(
                    select(
                        NutritionKnowledgeSourceRecord.status,
                        func.count(NutritionKnowledgeSourceRecord.id),
                    ).group_by(NutritionKnowledgeSourceRecord.status)
                )
            ).tuples()
            job_rows = (
                await session.execute(
                    select(
                        NutritionKnowledgeJobRecord.status,
                        func.count(NutritionKnowledgeJobRecord.id),
                    ).group_by(NutritionKnowledgeJobRecord.status)
                )
            ).tuples()
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


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None


__all__ = [
    "NutritionAsset",
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
