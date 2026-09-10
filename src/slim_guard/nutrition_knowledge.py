"""Versioned nutrition knowledge ingestion, governance, and retrieval.

Only published sources are eligible for new retrieval. Draft, rejected, and retired
assets remain addressable by ID so historical citations can always be audited.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import (
    NutritionKnowledgeChunkRecord,
    NutritionKnowledgeImportBatchRecord,
    NutritionKnowledgeReviewRecord,
    NutritionKnowledgeSourceRecord,
    new_uuid,
    utc_now,
)

if TYPE_CHECKING:
    from slim_guard.db.session import Database


class KnowledgeSourceStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    RETIRED = "retired"


class KnowledgeReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    PUBLISH = "publish"
    RETIRE = "retire"


class KnowledgeCorpusStatus(StrEnum):
    EMPTY = "empty"
    AVAILABLE = "available"


class NutritionKnowledgeError(RuntimeError):
    pass


class KnowledgeSourceNotFound(NutritionKnowledgeError):
    pass


class KnowledgeSourceConflict(NutritionKnowledgeError):
    pass


class KnowledgeGovernanceError(NutritionKnowledgeError):
    pass


class KnowledgeIntegrityError(NutritionKnowledgeError):
    pass


class KnowledgeDocument(BaseModel):
    """One complete, versioned source supplied by the offline import process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=2_000_000)
    published_at: date | None = None
    source_url: HttpUrl | None = None
    language: str = Field(default="zh-CN", min_length=1, max_length=32)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_key", "version", "title", "publisher", "language")
    @classmethod
    def strip_bounded_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Knowledge document fields cannot be blank")
        return normalized

    @field_validator("tags", "applicability")
    @classmethod
    def validate_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(label.strip() for label in value)
        if any(not label or len(label) > 128 for label in normalized):
            raise ValueError("Knowledge labels must contain 1 to 128 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Knowledge labels must be unique")
        return normalized

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = _canonical_json(value)
        except (TypeError, ValueError) as error:
            raise ValueError("Knowledge metadata must be canonical JSON") from error
        if len(encoded) > 32_000:
            raise ValueError("Knowledge metadata exceeds 32000 characters")
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):
            raise ValueError("Knowledge metadata must be a JSON object")
        return decoded


class KnowledgeMetadataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ids: tuple[str, ...] = Field(default=(), max_length=128)
    publishers: tuple[str, ...] = Field(default=(), max_length=64)
    languages: tuple[str, ...] = Field(default=(), max_length=32)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)
    published_from: date | None = None
    published_to: date | None = None

    @field_validator("source_ids", "publishers", "languages", "tags", "applicability")
    @classmethod
    def reject_duplicate_or_blank_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Knowledge metadata filters cannot contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("Knowledge metadata filters must be unique")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError("published_from cannot be after published_to")
        return self


@dataclass(frozen=True, slots=True)
class KnowledgeChunkDraft:
    ordinal: int
    start_char: int
    end_char: int
    content: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class KnowledgeImportBatch:
    id: str
    manifest_sha256: str
    imported_by: str
    status: str
    source_count: int
    duplicate_count: int
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    id: str
    import_batch_id: str
    source_key: str
    version: str
    title: str
    publisher: str
    published_at: date | None
    source_url: str | None
    language: str
    content: str
    content_sha256: str
    char_count: int
    tags: tuple[str, ...]
    applicability: tuple[str, ...]
    metadata: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
    published_for_retrieval_at: datetime | None
    retired_at: datetime | None


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: str
    source_id: str
    ordinal: int
    start_char: int
    end_char: int
    char_count: int
    content: str
    content_sha256: str
    metadata: dict[str, Any]
    embedding_model: str | None
    embedding: tuple[float, ...] | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeReview:
    id: str
    source_id: str
    reviewer: str
    decision: str
    status_from: str
    status_to: str
    reason: str | None
    source_content_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportedKnowledgeDocument:
    source: KnowledgeSource
    created: bool
    duplicate_of_source_id: str | None


@dataclass(frozen=True, slots=True)
class KnowledgeImportResult:
    batch: KnowledgeImportBatch
    documents: tuple[ImportedKnowledgeDocument, ...]


@dataclass(frozen=True, slots=True)
class SearchableKnowledgeChunk:
    source: KnowledgeSource
    chunk: KnowledgeChunk


@dataclass(frozen=True, slots=True)
class VectorCandidateInput:
    chunk_id: str
    source_id: str
    content: str
    content_sha256: str
    metadata: dict[str, Any]


class KnowledgeVectorScorer(Protocol):
    """Optional vector backend; scores are treated only as untrusted candidates."""

    async def candidate_scores(
        self,
        query: str,
        *,
        candidates: Sequence[VectorCandidateInput],
        limit: int,
    ) -> Mapping[str, float]: ...


class DeterministicKnowledgeChunker:
    """Boundary-aware character chunking with stable offsets and digests."""

    _BOUNDARY_PATTERN = re.compile(r"[。！？.!?；;\n]")

    def __init__(self, *, max_chars: int = 1_200, min_chars: int = 240) -> None:
        if not 64 <= max_chars <= 10_000:
            raise ValueError("max_chars must be between 64 and 10000")
        if not 1 <= min_chars <= max_chars:
            raise ValueError("min_chars must be between 1 and max_chars")
        self.max_chars = max_chars
        self.min_chars = min_chars

    def split(self, content: str) -> tuple[KnowledgeChunkDraft, ...]:
        normalized = normalize_knowledge_content(content)
        chunks: list[KnowledgeChunkDraft] = []
        cursor = 0
        ordinal = 0
        while cursor < len(normalized):
            while cursor < len(normalized) and normalized[cursor].isspace():
                cursor += 1
            if cursor >= len(normalized):
                break
            hard_end = min(cursor + self.max_chars, len(normalized))
            boundary = hard_end
            if hard_end < len(normalized):
                window = normalized[cursor + self.min_chars : hard_end]
                matches = tuple(self._BOUNDARY_PATTERN.finditer(window))
                if matches:
                    boundary = cursor + self.min_chars + matches[-1].end()
                else:
                    whitespace = normalized.rfind(" ", cursor + self.min_chars, hard_end)
                    if whitespace > cursor:
                        boundary = whitespace
            content_end = boundary
            while content_end > cursor and normalized[content_end - 1].isspace():
                content_end -= 1
            chunk_content = normalized[cursor:content_end]
            if chunk_content:
                chunks.append(
                    KnowledgeChunkDraft(
                        ordinal=ordinal,
                        start_char=cursor,
                        end_char=content_end,
                        content=chunk_content,
                        content_sha256=_sha256(chunk_content),
                    )
                )
                ordinal += 1
            cursor = max(boundary, cursor + 1)
        if not chunks:
            raise ValueError("Knowledge content does not contain non-whitespace text")
        return tuple(chunks)


def normalize_knowledge_content(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Knowledge content must be a string")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
    if not normalized:
        raise ValueError("Knowledge content cannot be blank")
    if len(normalized) > 2_000_000:
        raise ValueError("Knowledge content exceeds 2000000 characters")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("Knowledge timestamps must be timezone-aware")


class NutritionKnowledgeRepository:
    """Database boundary for immutable content and append-only review decisions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def start_import_batch(
        self,
        *,
        manifest_sha256: str,
        imported_by: str,
        created_at: datetime | None = None,
    ) -> KnowledgeImportBatch:
        manifest_sha256 = self._digest(manifest_sha256, field="manifest_sha256")
        imported_by = self._text(imported_by, field="imported_by", maximum=128)
        created_at = created_at or utc_now()
        _require_aware(created_at)
        async with self.database.session() as session, session.begin():
            row = NutritionKnowledgeImportBatchRecord(
                id=new_uuid(),
                manifest_sha256=manifest_sha256,
                imported_by=imported_by,
                status="running",
                source_count=0,
                duplicate_count=0,
                created_at=created_at,
            )
            session.add(row)
            await session.flush()
            return self._batch(row)

    async def complete_import_batch(
        self,
        batch_id: str,
        *,
        source_count: int,
        duplicate_count: int,
        completed_at: datetime | None = None,
    ) -> KnowledgeImportBatch:
        if source_count < 0 or duplicate_count < 0:
            raise ValueError("Import counters cannot be negative")
        completed_at = completed_at or utc_now()
        _require_aware(completed_at)
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionKnowledgeImportBatchRecord, batch_id)
            if row is None:
                raise KnowledgeSourceNotFound(f"Knowledge import batch {batch_id} does not exist")
            if row.status != "running":
                raise KnowledgeGovernanceError(
                    f"Knowledge import batch {batch_id} is already {row.status}"
                )
            row.status = "completed"
            row.source_count = source_count
            row.duplicate_count = duplicate_count
            row.completed_at = completed_at
            await session.flush()
            return self._batch(row)

    async def fail_import_batch(
        self,
        batch_id: str,
        *,
        failure_code: str,
        source_count: int = 0,
        duplicate_count: int = 0,
        completed_at: datetime | None = None,
    ) -> KnowledgeImportBatch:
        failure_code = self._text(failure_code, field="failure_code", maximum=128)
        if source_count < 0 or duplicate_count < 0:
            raise ValueError("Import counters cannot be negative")
        completed_at = completed_at or utc_now()
        _require_aware(completed_at)
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionKnowledgeImportBatchRecord, batch_id)
            if row is None:
                raise KnowledgeSourceNotFound(f"Knowledge import batch {batch_id} does not exist")
            if row.status != "running":
                raise KnowledgeGovernanceError(
                    f"Knowledge import batch {batch_id} is already {row.status}"
                )
            row.status = "failed"
            row.source_count = source_count
            row.duplicate_count = duplicate_count
            row.failure_code = failure_code
            row.completed_at = completed_at
            await session.flush()
            return self._batch(row)

    async def get_import_batch(self, batch_id: str) -> KnowledgeImportBatch | None:
        async with self.database.session() as session:
            row = await session.get(NutritionKnowledgeImportBatchRecord, batch_id)
            return self._batch(row) if row is not None else None

    async def append_source(
        self,
        *,
        batch_id: str,
        document: KnowledgeDocument,
        chunks: Sequence[KnowledgeChunkDraft],
        content_sha256: str,
        created_at: datetime | None = None,
    ) -> ImportedKnowledgeDocument:
        content_sha256 = self._digest(content_sha256, field="content_sha256")
        normalized_content = normalize_knowledge_content(document.content)
        if _sha256(normalized_content) != content_sha256:
            raise ValueError("content_sha256 does not match normalized source content")
        self._validate_chunks(normalized_content, chunks)
        created_at = created_at or utc_now()
        _require_aware(created_at)
        try:
            async with self.database.session() as session, session.begin():
                batch = await session.get(NutritionKnowledgeImportBatchRecord, batch_id)
                if batch is None:
                    raise KnowledgeSourceNotFound(
                        f"Knowledge import batch {batch_id} does not exist"
                    )
                if batch.status != "running":
                    raise KnowledgeGovernanceError(
                        f"Knowledge import batch {batch_id} is {batch.status}"
                    )
                duplicate = await session.scalar(
                    select(NutritionKnowledgeSourceRecord).where(
                        NutritionKnowledgeSourceRecord.content_sha256 == content_sha256
                    )
                )
                if duplicate is not None:
                    source = self._source(duplicate)
                    return ImportedKnowledgeDocument(
                        source=source,
                        created=False,
                        duplicate_of_source_id=source.id,
                    )
                version_conflict = await session.scalar(
                    select(NutritionKnowledgeSourceRecord.id).where(
                        NutritionKnowledgeSourceRecord.source_key == document.source_key,
                        NutritionKnowledgeSourceRecord.version == document.version,
                    )
                )
                if version_conflict is not None:
                    raise KnowledgeSourceConflict(
                        f"Knowledge source {document.source_key}@{document.version} "
                        "already exists with different content"
                    )
                source_id = new_uuid()
                row = NutritionKnowledgeSourceRecord(
                    id=source_id,
                    import_batch_id=batch_id,
                    source_key=document.source_key,
                    version=document.version,
                    title=document.title,
                    publisher=document.publisher,
                    published_at=document.published_at,
                    source_url=str(document.source_url)
                    if document.source_url is not None
                    else None,
                    language=document.language,
                    content_text=normalized_content,
                    content_sha256=content_sha256,
                    char_count=len(normalized_content),
                    metadata_json=_canonical_json(
                        {
                            "tags": list(document.tags),
                            "applicability": list(document.applicability),
                            "custom": document.metadata,
                        }
                    ),
                    status=KnowledgeSourceStatus.DRAFT.value,
                    created_at=created_at,
                    updated_at=created_at,
                )
                session.add(row)
                # There are intentionally no ORM relationships between immutable
                # source and chunk records. Flush the parent explicitly so
                # PostgreSQL never attempts a child INSERT before its FK target.
                await session.flush()
                session.add_all(
                    [
                        NutritionKnowledgeChunkRecord(
                            id=self._chunk_id(
                                source_content_sha256=content_sha256,
                                draft=draft,
                            ),
                            source_id=source_id,
                            ordinal=draft.ordinal,
                            start_char=draft.start_char,
                            end_char=draft.end_char,
                            char_count=len(draft.content),
                            content_text=draft.content,
                            content_sha256=draft.content_sha256,
                            metadata_json=_canonical_json(
                                {
                                    "start_char": draft.start_char,
                                    "end_char": draft.end_char,
                                }
                            ),
                            created_at=created_at,
                        )
                        for draft in chunks
                    ]
                )
                await session.flush()
                return ImportedKnowledgeDocument(
                    source=self._source(row),
                    created=True,
                    duplicate_of_source_id=None,
                )
        except IntegrityError as error:
            existing = await self.get_source_by_content_sha256(content_sha256)
            if existing is not None:
                return ImportedKnowledgeDocument(
                    source=existing,
                    created=False,
                    duplicate_of_source_id=existing.id,
                )
            raise KnowledgeSourceConflict(
                f"Knowledge source {document.source_key}@{document.version} already exists"
            ) from error

    async def get_source(self, source_id: str) -> KnowledgeSource | None:
        async with self.database.session() as session:
            row = await session.get(NutritionKnowledgeSourceRecord, source_id)
            return self._source(row) if row is not None else None

    async def get_source_by_content_sha256(
        self,
        content_sha256: str,
    ) -> KnowledgeSource | None:
        content_sha256 = self._digest(content_sha256, field="content_sha256")
        async with self.database.session() as session:
            row = await session.scalar(
                select(NutritionKnowledgeSourceRecord).where(
                    NutritionKnowledgeSourceRecord.content_sha256 == content_sha256
                )
            )
            return self._source(row) if row is not None else None

    async def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        async with self.database.session() as session:
            row = await session.get(NutritionKnowledgeChunkRecord, chunk_id)
            return self._chunk(row) if row is not None else None

    async def list_chunks(self, source_id: str) -> tuple[KnowledgeChunk, ...]:
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(NutritionKnowledgeChunkRecord)
                    .where(NutritionKnowledgeChunkRecord.source_id == source_id)
                    .order_by(NutritionKnowledgeChunkRecord.ordinal)
                )
            )
            return tuple(self._chunk(row) for row in rows)

    async def list_sources(
        self,
        *,
        statuses: Sequence[KnowledgeSourceStatus | str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        if not 1 <= limit <= 1_000 or offset < 0:
            raise ValueError("Invalid source page")
        statement = select(NutritionKnowledgeSourceRecord)
        if statuses is not None:
            normalized = tuple(KnowledgeSourceStatus(item).value for item in statuses)
            statement = statement.where(NutritionKnowledgeSourceRecord.status.in_(normalized))
        statement = (
            statement.order_by(
                NutritionKnowledgeSourceRecord.created_at.desc(),
                NutritionKnowledgeSourceRecord.id,
            )
            .limit(limit)
            .offset(offset)
        )
        async with self.database.session() as session:
            return tuple(self._source(row) for row in await session.scalars(statement))

    async def list_reviews(self, source_id: str) -> tuple[KnowledgeReview, ...]:
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(NutritionKnowledgeReviewRecord)
                    .where(NutritionKnowledgeReviewRecord.source_id == source_id)
                    .order_by(
                        NutritionKnowledgeReviewRecord.created_at,
                        NutritionKnowledgeReviewRecord.id,
                    )
                )
            )
            return tuple(self._review(row) for row in rows)

    async def transition_source(
        self,
        source_id: str,
        *,
        decision: KnowledgeReviewDecision | str,
        reviewer: str,
        reason: str | None = None,
        decided_at: datetime | None = None,
    ) -> KnowledgeSource:
        decision = KnowledgeReviewDecision(decision)
        reviewer = self._text(reviewer, field="reviewer", maximum=128)
        reason = self._text(reason, field="reason", maximum=2_000) if reason is not None else None
        if decision in {KnowledgeReviewDecision.REJECT, KnowledgeReviewDecision.RETIRE}:
            if reason is None:
                raise ValueError(f"{decision.value} requires a reason")
        decided_at = decided_at or utc_now()
        _require_aware(decided_at)
        target, allowed = self._transition(decision)
        async with self.database.session() as session, session.begin():
            row = await session.get(NutritionKnowledgeSourceRecord, source_id)
            if row is None:
                raise KnowledgeSourceNotFound(f"Knowledge source {source_id} does not exist")
            if row.status not in allowed:
                raise KnowledgeGovernanceError(
                    f"Cannot {decision.value} source in {row.status} status"
                )
            previous = row.status
            row.status = target.value
            row.updated_at = decided_at
            if target is KnowledgeSourceStatus.PUBLISHED:
                row.published_for_retrieval_at = decided_at
            if target is KnowledgeSourceStatus.RETIRED:
                row.retired_at = decided_at
            session.add(
                NutritionKnowledgeReviewRecord(
                    id=new_uuid(),
                    source_id=row.id,
                    reviewer=reviewer,
                    decision=decision.value,
                    status_from=previous,
                    status_to=target.value,
                    reason=reason,
                    source_content_sha256=row.content_sha256,
                    created_at=decided_at,
                )
            )
            await session.flush()
            return self._source(row)

    async def has_published_sources(self) -> bool:
        async with self.database.session() as session:
            count = await session.scalar(
                select(func.count(NutritionKnowledgeSourceRecord.id)).where(
                    NutritionKnowledgeSourceRecord.status == KnowledgeSourceStatus.PUBLISHED.value
                )
            )
            return bool(count)

    async def list_searchable_chunks(
        self,
        *,
        metadata_filter: KnowledgeMetadataFilter | None = None,
        scan_limit: int = 5_000,
    ) -> tuple[SearchableKnowledgeChunk, ...]:
        if not 1 <= scan_limit <= 20_000:
            raise ValueError("scan_limit must be between 1 and 20000")
        filters = metadata_filter or KnowledgeMetadataFilter()
        statement = (
            select(NutritionKnowledgeSourceRecord, NutritionKnowledgeChunkRecord)
            .join(
                NutritionKnowledgeChunkRecord,
                NutritionKnowledgeChunkRecord.source_id == NutritionKnowledgeSourceRecord.id,
            )
            .where(NutritionKnowledgeSourceRecord.status == KnowledgeSourceStatus.PUBLISHED.value)
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
        statement = statement.order_by(
            NutritionKnowledgeSourceRecord.id,
            NutritionKnowledgeChunkRecord.ordinal,
        ).limit(scan_limit)
        async with self.database.session() as session:
            rows = tuple((await session.execute(statement)).tuples())
            candidates = [
                SearchableKnowledgeChunk(
                    source=self._source(source),
                    chunk=self._chunk(chunk),
                )
                for source, chunk in rows
            ]
        return tuple(
            candidate
            for candidate in candidates
            if self._metadata_matches(candidate.source, filters)
        )

    @staticmethod
    def _metadata_matches(
        source: KnowledgeSource,
        filters: KnowledgeMetadataFilter,
    ) -> bool:
        if filters.tags and not set(filters.tags).issubset(source.tags):
            return False
        if filters.applicability and not set(filters.applicability).issubset(source.applicability):
            return False
        return True

    @staticmethod
    def _transition(
        decision: KnowledgeReviewDecision,
    ) -> tuple[KnowledgeSourceStatus, frozenset[str]]:
        if decision is KnowledgeReviewDecision.APPROVE:
            return KnowledgeSourceStatus.APPROVED, frozenset({"draft"})
        if decision is KnowledgeReviewDecision.REJECT:
            return KnowledgeSourceStatus.REJECTED, frozenset({"draft"})
        if decision is KnowledgeReviewDecision.PUBLISH:
            return KnowledgeSourceStatus.PUBLISHED, frozenset({"approved"})
        return KnowledgeSourceStatus.RETIRED, frozenset(
            {"draft", "approved", "rejected", "published"}
        )

    @staticmethod
    def _validate_chunks(
        content: str,
        chunks: Sequence[KnowledgeChunkDraft],
    ) -> None:
        if not chunks:
            raise ValueError("A knowledge source requires at least one chunk")
        expected_ordinals = tuple(range(len(chunks)))
        if tuple(chunk.ordinal for chunk in chunks) != expected_ordinals:
            raise ValueError("Knowledge chunk ordinals must be contiguous from zero")
        for chunk in chunks:
            if not 0 <= chunk.start_char < chunk.end_char <= len(content):
                raise ValueError("Knowledge chunk offsets are outside the source")
            if content[chunk.start_char : chunk.end_char] != chunk.content:
                raise ValueError("Knowledge chunk content does not match source offsets")
            if _sha256(chunk.content) != chunk.content_sha256:
                raise ValueError("Knowledge chunk digest does not match its content")

    @staticmethod
    def _chunk_id(
        *,
        source_content_sha256: str,
        draft: KnowledgeChunkDraft,
    ) -> str:
        identity = f"{source_content_sha256}:{draft.ordinal}:{draft.content_sha256}"
        return "knowledge-chunk-" + _sha256(identity)[:48]

    @classmethod
    def _source(cls, row: NutritionKnowledgeSourceRecord) -> KnowledgeSource:
        if _sha256(row.content_text) != row.content_sha256:
            raise KnowledgeIntegrityError(
                f"Stored knowledge source {row.id} failed content verification"
            )
        if len(row.content_text) != row.char_count:
            raise KnowledgeIntegrityError(
                f"Stored knowledge source {row.id} has an invalid character count"
            )
        metadata = cls._load_json_object(row.metadata_json, subject=f"source {row.id}")
        tags = cls._string_tuple(metadata.get("tags"), field="tags")
        applicability = cls._string_tuple(
            metadata.get("applicability"),
            field="applicability",
        )
        custom = metadata.get("custom", {})
        if not isinstance(custom, dict):
            raise KnowledgeIntegrityError(
                f"Stored knowledge source {row.id} has invalid custom metadata"
            )
        return KnowledgeSource(
            id=row.id,
            import_batch_id=row.import_batch_id,
            source_key=row.source_key,
            version=row.version,
            title=row.title,
            publisher=row.publisher,
            published_at=row.published_at,
            source_url=row.source_url,
            language=row.language,
            content=row.content_text,
            content_sha256=row.content_sha256,
            char_count=row.char_count,
            tags=tags,
            applicability=applicability,
            metadata=dict(custom),
            status=row.status,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
            published_for_retrieval_at=(
                _aware(row.published_for_retrieval_at)
                if row.published_for_retrieval_at is not None
                else None
            ),
            retired_at=_aware(row.retired_at) if row.retired_at is not None else None,
        )

    @classmethod
    def _chunk(cls, row: NutritionKnowledgeChunkRecord) -> KnowledgeChunk:
        if _sha256(row.content_text) != row.content_sha256:
            raise KnowledgeIntegrityError(
                f"Stored knowledge chunk {row.id} failed content verification"
            )
        if len(row.content_text) != row.char_count:
            raise KnowledgeIntegrityError(
                f"Stored knowledge chunk {row.id} has an invalid character count"
            )
        metadata = cls._load_json_object(row.metadata_json, subject=f"chunk {row.id}")
        embedding = None
        if row.embedding_json is not None:
            try:
                raw_embedding = json.loads(row.embedding_json)
            except (TypeError, ValueError) as error:
                raise KnowledgeIntegrityError(
                    f"Stored knowledge chunk {row.id} has invalid embedding JSON"
                ) from error
            if (
                not isinstance(raw_embedding, list)
                or not raw_embedding
                or any(
                    not isinstance(item, (int, float))
                    or isinstance(item, bool)
                    or not math.isfinite(float(item))
                    for item in raw_embedding
                )
            ):
                raise KnowledgeIntegrityError(
                    f"Stored knowledge chunk {row.id} has an invalid embedding"
                )
            embedding = tuple(float(item) for item in raw_embedding)
        return KnowledgeChunk(
            id=row.id,
            source_id=row.source_id,
            ordinal=row.ordinal,
            start_char=row.start_char,
            end_char=row.end_char,
            char_count=row.char_count,
            content=row.content_text,
            content_sha256=row.content_sha256,
            metadata=metadata,
            embedding_model=row.embedding_model,
            embedding=embedding,
            created_at=_aware(row.created_at),
        )

    @staticmethod
    def _review(row: NutritionKnowledgeReviewRecord) -> KnowledgeReview:
        return KnowledgeReview(
            id=row.id,
            source_id=row.source_id,
            reviewer=row.reviewer,
            decision=row.decision,
            status_from=row.status_from,
            status_to=row.status_to,
            reason=row.reason,
            source_content_sha256=row.source_content_sha256,
            created_at=_aware(row.created_at),
        )

    @staticmethod
    def _batch(row: NutritionKnowledgeImportBatchRecord) -> KnowledgeImportBatch:
        return KnowledgeImportBatch(
            id=row.id,
            manifest_sha256=row.manifest_sha256,
            imported_by=row.imported_by,
            status=row.status,
            source_count=row.source_count,
            duplicate_count=row.duplicate_count,
            failure_code=row.failure_code,
            created_at=_aware(row.created_at),
            completed_at=_aware(row.completed_at) if row.completed_at is not None else None,
        )

    @staticmethod
    def _load_json_object(value: str, *, subject: str) -> dict[str, Any]:
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as error:
            raise KnowledgeIntegrityError(f"Stored knowledge {subject} has invalid JSON") from error
        if not isinstance(decoded, dict):
            raise KnowledgeIntegrityError(f"Stored knowledge {subject} must be a JSON object")
        return decoded

    @staticmethod
    def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise KnowledgeIntegrityError(f"Stored knowledge {field} must be a string list")
        return tuple(value)

    @staticmethod
    def _digest(value: str, *, field: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"{field} must be lowercase SHA-256 hex")
        return value

    @staticmethod
    def _text(value: str, *, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"{field} must contain 1 to {maximum} characters")
        return normalized


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    searchable: SearchableKnowledgeChunk
    lexical_score: float
    vector_score: float
    rerank_score: float
    match_reasons: tuple[str, ...]


class NutritionKnowledgeService:
    """Stable offline import API and production read-only retrieval adapter."""

    def __init__(
        self,
        repository: NutritionKnowledgeRepository,
        *,
        chunker: DeterministicKnowledgeChunker | None = None,
        vector_scorer: KnowledgeVectorScorer | None = None,
        scan_limit: int = 5_000,
    ) -> None:
        if not 1 <= scan_limit <= 20_000:
            raise ValueError("scan_limit must be between 1 and 20000")
        self.repository = repository
        self.chunker = chunker or DeterministicKnowledgeChunker()
        self.vector_scorer = vector_scorer
        self.scan_limit = scan_limit

    async def import_documents(
        self,
        documents: Sequence[KnowledgeDocument | Mapping[str, Any]],
        *,
        imported_by: str,
        created_at: datetime | None = None,
    ) -> KnowledgeImportResult:
        if not documents:
            raise ValueError("At least one knowledge document is required")
        if len(documents) > 1_000:
            raise ValueError("A knowledge import is limited to 1000 documents")
        created_at = created_at or utc_now()
        _require_aware(created_at)
        normalized_documents = tuple(self._normalized_document(document) for document in documents)
        manifest_sha256 = _sha256(
            _canonical_json(
                [
                    {
                        **document.model_dump(mode="json", exclude={"content"}),
                        "content_sha256": _sha256(document.content),
                    }
                    for document in normalized_documents
                ]
            )
        )
        batch = await self.repository.start_import_batch(
            manifest_sha256=manifest_sha256,
            imported_by=imported_by,
            created_at=created_at,
        )
        results: list[ImportedKnowledgeDocument] = []
        source_count = 0
        duplicate_count = 0
        try:
            for document in normalized_documents:
                imported = await self.repository.append_source(
                    batch_id=batch.id,
                    document=document,
                    chunks=self.chunker.split(document.content),
                    content_sha256=_sha256(document.content),
                    created_at=created_at,
                )
                results.append(imported)
                if imported.created:
                    source_count += 1
                else:
                    duplicate_count += 1
            completed = await self.repository.complete_import_batch(
                batch.id,
                source_count=source_count,
                duplicate_count=duplicate_count,
                completed_at=created_at,
            )
        except Exception as error:
            await self.repository.fail_import_batch(
                batch.id,
                failure_code=type(error).__name__[:128],
                source_count=source_count,
                duplicate_count=duplicate_count,
                completed_at=created_at,
            )
            raise
        return KnowledgeImportResult(batch=completed, documents=tuple(results))

    async def approve_source(
        self,
        source_id: str,
        *,
        reviewer: str,
        reason: str | None = None,
        decided_at: datetime | None = None,
    ) -> KnowledgeSource:
        return await self.repository.transition_source(
            source_id,
            decision=KnowledgeReviewDecision.APPROVE,
            reviewer=reviewer,
            reason=reason,
            decided_at=decided_at,
        )

    async def reject_source(
        self,
        source_id: str,
        *,
        reviewer: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> KnowledgeSource:
        return await self.repository.transition_source(
            source_id,
            decision=KnowledgeReviewDecision.REJECT,
            reviewer=reviewer,
            reason=reason,
            decided_at=decided_at,
        )

    async def publish_source(
        self,
        source_id: str,
        *,
        reviewer: str,
        reason: str | None = None,
        decided_at: datetime | None = None,
    ) -> KnowledgeSource:
        return await self.repository.transition_source(
            source_id,
            decision=KnowledgeReviewDecision.PUBLISH,
            reviewer=reviewer,
            reason=reason,
            decided_at=decided_at,
        )

    async def retire_source(
        self,
        source_id: str,
        *,
        reviewer: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> KnowledgeSource:
        return await self.repository.transition_source(
            source_id,
            decision=KnowledgeReviewDecision.RETIRE,
            reviewer=reviewer,
            reason=reason,
            decided_at=decided_at,
        )

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        metadata_filter: KnowledgeMetadataFilter | Mapping[str, Any] | None = None,
        retrieved_in_invocation_id: str | None = None,
    ) -> Mapping[str, Any]:
        query = " ".join(query.split())
        if not query or len(query) > 1_000:
            raise ValueError("query must contain 1 to 1000 characters")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        filters = (
            metadata_filter
            if isinstance(metadata_filter, KnowledgeMetadataFilter)
            else KnowledgeMetadataFilter.model_validate(metadata_filter or {})
        )
        searchable = await self.repository.list_searchable_chunks(
            metadata_filter=filters,
            scan_limit=self.scan_limit,
        )
        has_corpus = await self.repository.has_published_sources()
        vector_scores = await self._vector_scores(
            query=query,
            searchable=searchable,
            limit=min(self.scan_limit, max(max_results * 8, 32)),
        )
        scored: list[_ScoredCandidate] = []
        for item in searchable:
            lexical_score, reasons = self._lexical_score(query, item)
            vector_score = vector_scores.get(item.chunk.id, 0.0)
            if lexical_score <= 0 and vector_score <= 0:
                continue
            rerank_score = round(lexical_score + (vector_score * 0.35), 8)
            scored.append(
                _ScoredCandidate(
                    searchable=item,
                    lexical_score=lexical_score,
                    vector_score=vector_score,
                    rerank_score=rerank_score,
                    match_reasons=reasons + (("vector_candidate",) if vector_score > 0 else ()),
                )
            )
        scored.sort(
            key=lambda item: (
                -item.rerank_score,
                -item.lexical_score,
                -item.vector_score,
                item.searchable.source.source_key,
                item.searchable.source.version,
                item.searchable.chunk.ordinal,
                item.searchable.chunk.id,
            )
        )
        candidate_limit = min(len(scored), max(max_results * 4, max_results))
        candidates = [
            self._candidate(
                item,
                rank=index + 1,
                adopted=index < max_results,
            )
            for index, item in enumerate(scored[:candidate_limit])
        ]
        adopted = [
            candidate for candidate in candidates if candidate["adoption_status"] == "adopted"
        ]
        citations = (
            [
                self._citation(
                    candidate,
                    retrieved_in_invocation_id=retrieved_in_invocation_id,
                )
                for candidate in adopted
            ]
            if retrieved_in_invocation_id is not None
            else []
        )
        return {
            "corpus_status": (
                KnowledgeCorpusStatus.AVAILABLE.value
                if has_corpus
                else KnowledgeCorpusStatus.EMPTY.value
            ),
            "candidates": candidates,
            "adopted_citations": adopted,
            # Existing NutritionKnowledgeRepository consumers can keep reading
            # citations. A coordinator binds the invocation ID before model use.
            "citations": citations,
            "query_summary": (
                f"lexical_vector_candidates={len(candidates)};adopted={len(adopted)}"
            ),
        }

    async def get_source(
        self,
        *,
        source_id: str,
        chunk_id: str | None = None,
    ) -> Mapping[str, Any]:
        source = await self.repository.get_source(source_id)
        if source is None:
            return {
                "corpus_status": (
                    KnowledgeCorpusStatus.AVAILABLE.value
                    if await self.repository.has_published_sources()
                    else KnowledgeCorpusStatus.EMPTY.value
                ),
                "source_id": source_id,
                "chunk_id": chunk_id,
                "source": None,
                "eligibility": {
                    "active": False,
                    "publication_status": "not_found",
                    "reason": "source_not_found",
                },
            }
        if source.status != KnowledgeSourceStatus.PUBLISHED.value:
            return {
                "corpus_status": (
                    KnowledgeCorpusStatus.AVAILABLE.value
                    if await self.repository.has_published_sources()
                    else KnowledgeCorpusStatus.EMPTY.value
                ),
                "source_id": source.id,
                "chunk_id": chunk_id,
                "source": None,
                "chunk": None,
                "eligibility": {
                    "active": False,
                    "publication_status": source.status,
                    "reason": "source_not_published",
                },
            }
        chunk = await self.repository.get_chunk(chunk_id) if chunk_id is not None else None
        if chunk is not None and chunk.source_id != source.id:
            chunk = None
        return {
            "corpus_status": (
                KnowledgeCorpusStatus.AVAILABLE.value
                if source.status == KnowledgeSourceStatus.PUBLISHED.value
                else KnowledgeCorpusStatus.EMPTY.value
            ),
            "source_id": source.id,
            "chunk_id": chunk_id,
            "source": {
                "source_key": source.source_key,
                "version": source.version,
                "title": source.title,
                "publisher": source.publisher,
                "published_at": (
                    source.published_at.isoformat() if source.published_at is not None else None
                ),
                "source_url": source.source_url,
                "language": source.language,
                "content_sha256": source.content_sha256,
                "applicability": list(source.applicability),
                "tags": list(source.tags),
                "review_status": (
                    "approved"
                    if source.status
                    in {
                        KnowledgeSourceStatus.APPROVED.value,
                        KnowledgeSourceStatus.PUBLISHED.value,
                    }
                    else source.status
                ),
                "publication_status": source.status,
                "active": source.status == KnowledgeSourceStatus.PUBLISHED.value,
            },
            "chunk": (
                {
                    "chunk_id": chunk.id,
                    "content": chunk.content,
                    "content_sha256": chunk.content_sha256,
                    "ordinal": chunk.ordinal,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                }
                if chunk is not None
                else None
            ),
            "eligibility": {
                "active": True,
                "publication_status": KnowledgeSourceStatus.PUBLISHED.value,
                "reason": None,
            },
        }

    async def _vector_scores(
        self,
        *,
        query: str,
        searchable: Sequence[SearchableKnowledgeChunk],
        limit: int,
    ) -> dict[str, float]:
        if self.vector_scorer is None or not searchable:
            return {}
        trusted_ids = {item.chunk.id for item in searchable}
        raw = await self.vector_scorer.candidate_scores(
            query,
            candidates=tuple(
                VectorCandidateInput(
                    chunk_id=item.chunk.id,
                    source_id=item.source.id,
                    content=item.chunk.content,
                    content_sha256=item.chunk.content_sha256,
                    metadata={
                        "publisher": item.source.publisher,
                        "language": item.source.language,
                        "tags": list(item.source.tags),
                        "applicability": list(item.source.applicability),
                    },
                )
                for item in searchable
            ),
            limit=limit,
        )
        result: dict[str, float] = {}
        for chunk_id, score in raw.items():
            if (
                chunk_id not in trusted_ids
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                continue
            result[chunk_id] = round(max(0.0, min(1.0, float(score))), 8)
        return result

    @classmethod
    def _lexical_score(
        cls,
        query: str,
        item: SearchableKnowledgeChunk,
    ) -> tuple[float, tuple[str, ...]]:
        query_tokens = Counter(cls._tokens(query))
        if not query_tokens:
            return 0.0, ()
        body_tokens = Counter(cls._tokens(item.chunk.content))
        title_tokens = Counter(cls._tokens(item.source.title))
        denominator = sum(query_tokens.values())
        body_matches = sum(
            min(count, body_tokens.get(token, 0)) for token, count in query_tokens.items()
        )
        title_matches = sum(
            min(count, title_tokens.get(token, 0)) for token, count in query_tokens.items()
        )
        normalized_query = unicodedata.normalize("NFC", query).casefold()
        exact_phrase = normalized_query in item.chunk.content.casefold()
        score = min(
            1.0,
            (body_matches / denominator * 0.8)
            + (title_matches / denominator * 0.2)
            + (0.2 if exact_phrase else 0.0),
        )
        reasons: list[str] = []
        if body_matches:
            reasons.append("content_token_match")
        if title_matches:
            reasons.append("title_token_match")
        if exact_phrase:
            reasons.append("exact_phrase_match")
        return round(score, 8), tuple(reasons)

    @staticmethod
    def _tokens(value: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        base = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", normalized)
        cjk_runs = re.findall(r"[\u3400-\u9fff]{2,}", normalized)
        bigrams = [run[index : index + 2] for run in cjk_runs for index in range(len(run) - 1)]
        return tuple(base + bigrams)

    @staticmethod
    def _candidate(
        scored: _ScoredCandidate,
        *,
        rank: int,
        adopted: bool,
    ) -> dict[str, Any]:
        source = scored.searchable.source
        chunk = scored.searchable.chunk
        stable_identity = f"{source.source_key}:{source.version}:{chunk.id}"
        citation_id = "citation-" + _sha256(stable_identity)[:48]
        return {
            "candidate_id": "candidate-" + _sha256(stable_identity)[:48],
            "rank": rank,
            "citation_id": citation_id,
            "source_id": source.id,
            "chunk_id": chunk.id,
            "title": source.title,
            "publisher": source.publisher,
            "published_at": (
                source.published_at.isoformat() if source.published_at is not None else None
            ),
            "version": source.version,
            "section_or_page": f"chunk {chunk.ordinal + 1}",
            "source_url": source.source_url,
            "applicability": list(source.applicability[:32]),
            "review_status": "approved",
            "active": True,
            "content": chunk.content,
            # The digest is always over the exact candidate content above.
            "content_sha256": chunk.content_sha256,
            "keyword_score": scored.lexical_score,
            "vector_score": scored.vector_score,
            "rerank_score": scored.rerank_score,
            "match_reasons": list(scored.match_reasons),
            "adoption_status": "adopted" if adopted else "candidate_only",
        }

    @staticmethod
    def _citation(
        candidate: Mapping[str, Any],
        *,
        retrieved_in_invocation_id: str,
    ) -> dict[str, Any]:
        return {
            "citation_id": candidate["citation_id"],
            "source_id": candidate["source_id"],
            "chunk_id": candidate["chunk_id"],
            "title": candidate["title"],
            "publisher": candidate["publisher"],
            "published_at": candidate["published_at"],
            "version": candidate["version"],
            "section_or_page": candidate["section_or_page"],
            "source_url": candidate["source_url"],
            "applicability": candidate["applicability"],
            "review_status": candidate["review_status"],
            "retrieved_in_invocation_id": retrieved_in_invocation_id,
        }

    @staticmethod
    def _normalized_document(
        document: KnowledgeDocument | Mapping[str, Any],
    ) -> KnowledgeDocument:
        parsed = (
            document
            if isinstance(document, KnowledgeDocument)
            else KnowledgeDocument.model_validate(document)
        )
        return parsed.model_copy(update={"content": normalize_knowledge_content(parsed.content)})


__all__ = [
    "DeterministicKnowledgeChunker",
    "ImportedKnowledgeDocument",
    "KnowledgeChunk",
    "KnowledgeChunkDraft",
    "KnowledgeCorpusStatus",
    "KnowledgeDocument",
    "KnowledgeGovernanceError",
    "KnowledgeImportBatch",
    "KnowledgeImportResult",
    "KnowledgeIntegrityError",
    "KnowledgeMetadataFilter",
    "KnowledgeReview",
    "KnowledgeReviewDecision",
    "KnowledgeSource",
    "KnowledgeSourceConflict",
    "KnowledgeSourceNotFound",
    "KnowledgeSourceStatus",
    "KnowledgeVectorScorer",
    "NutritionKnowledgeError",
    "NutritionKnowledgeRepository",
    "NutritionKnowledgeService",
    "SearchableKnowledgeChunk",
    "VectorCandidateInput",
    "normalize_knowledge_content",
]
