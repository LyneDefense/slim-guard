"""Governed, versioned dish catalog used by nutrition retrieval.

The catalog deliberately stores qualitative dish traits and reviewed suitability rules.
It is not a calorie or nutrient-composition database.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    DishAliasRecord,
    DishCatalogImportBatchRecord,
    DishCatalogReviewRecord,
    DishEntityRecord,
    DishRuleRecord,
    DishTraitRecord,
    new_uuid,
    utc_now,
)
from slim_guard.db.session import Database


class DishCatalogStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    RETIRED = "retired"


class DishCatalogDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    PUBLISH = "publish"
    RETIRE = "retire"


class DishCatalogMatchStatus(StrEnum):
    EXACT = "exact"
    ALIAS = "alias"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class DishCatalogError(RuntimeError):
    pass


class DishCatalogConflict(DishCatalogError):
    pass


class DishCatalogNotFound(DishCatalogError):
    pass


class DishCatalogGovernanceError(DishCatalogError):
    pass


class DishAliasDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=256)


class DishTraitDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trait_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=128)
    statement: str = Field(min_length=1, max_length=1000)
    certainty: str = Field(pattern=r"^(defined|typical|possible)$")
    preparation_scope: str | None = Field(default=None, min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=256)


class DishRuleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=128)
    condition_type: str = Field(
        pattern=r"^(general|goal|constraint|medical_boundary)$"
    )
    condition_value: str | None = Field(default=None, min_length=1, max_length=256)
    effect: str = Field(pattern=r"^(allow|adjust|limit|avoid|require_confirmation)$")
    statement: str = Field(min_length=1, max_length=1000)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)
    source_ref: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_condition(self) -> Self:
        if self.condition_type == "general" and self.condition_value is not None:
            raise ValueError("General rules cannot have a condition value")
        if self.condition_type != "general" and self.condition_value is None:
            raise ValueError("Conditional rules require a condition value")
        if self.effect == "avoid" and self.condition_type == "general":
            raise ValueError("Avoid rules cannot be unconditional")
        return self


class DishCatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$", max_length=128)
    version: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    cuisine: str | None = Field(default=None, min_length=1, max_length=128)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    aliases: tuple[DishAliasDocument, ...] = Field(default=(), max_length=64)
    traits: tuple[DishTraitDocument, ...] = Field(default=(), max_length=64)
    rules: tuple[DishRuleDocument, ...] = Field(default=(), max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("canonical_name", "cuisine")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Dish catalog text cannot be blank")
        return normalized

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(item.split()) for item in value)
        if any(not item for item in normalized):
            raise ValueError("Dish source references cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Dish source references must be unique")
        return normalized

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = _canonical_json(value)
        if len(encoded) > 32_000:
            raise ValueError("Dish metadata exceeds 32000 characters")
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):
            raise ValueError("Dish metadata must be an object")
        return decoded

    @model_validator(mode="after")
    def validate_children(self) -> Self:
        aliases = tuple(normalize_dish_name(item.name) for item in self.aliases)
        if len(aliases) != len(set(aliases)):
            raise ValueError("Dish aliases must be unique after normalization")
        trait_keys = tuple(item.trait_key for item in self.traits)
        if len(trait_keys) != len(set(trait_keys)):
            raise ValueError("Dish trait keys must be unique")
        rule_keys = tuple(item.rule_key for item in self.rules)
        if len(rule_keys) != len(set(rule_keys)):
            raise ValueError("Dish rule keys must be unique")
        return self


@dataclass(frozen=True, slots=True)
class DishCatalogImportBatch:
    id: str
    manifest_sha256: str
    imported_by: str
    status: str
    entity_count: int
    duplicate_count: int
    failure_code: str | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class DishAlias:
    id: str
    dish_entity_id: str
    name: str
    normalized_name: str
    region: str | None
    source_ref: str


@dataclass(frozen=True, slots=True)
class DishTrait:
    id: str
    dish_entity_id: str
    trait_key: str
    statement: str
    certainty: str
    preparation_scope: str | None
    source_ref: str


@dataclass(frozen=True, slots=True)
class DishRule:
    id: str
    dish_entity_id: str
    rule_key: str
    condition_type: str
    condition_value: str | None
    effect: str
    statement: str
    applicability: tuple[str, ...]
    source_ref: str
    version: str


@dataclass(frozen=True, slots=True)
class DishEntity:
    id: str
    import_batch_id: str
    entity_key: str
    version: str
    canonical_name: str
    normalized_name: str
    cuisine: str | None
    source_refs: tuple[str, ...]
    metadata: dict[str, Any]
    document_sha256: str
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    retired_at: datetime | None


@dataclass(frozen=True, slots=True)
class DishCatalogReview:
    id: str
    dish_entity_id: str
    reviewer: str
    decision: str
    status_from: str
    status_to: str
    reason: str | None
    document_sha256: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DishCatalogEntry:
    entity: DishEntity
    aliases: tuple[DishAlias, ...]
    traits: tuple[DishTrait, ...]
    rules: tuple[DishRule, ...]


@dataclass(frozen=True, slots=True)
class ImportedDishCatalogEntry:
    entry: DishCatalogEntry
    created: bool


@dataclass(frozen=True, slots=True)
class DishCatalogImportResult:
    batch: DishCatalogImportBatch
    entries: tuple[ImportedDishCatalogEntry, ...]


@dataclass(frozen=True, slots=True)
class DishSearchCandidate:
    entity: DishEntity
    matched_by: str
    matched_text: str


@dataclass(frozen=True, slots=True)
class DishSearchResult:
    query: str
    normalized_query: str
    status: DishCatalogMatchStatus
    candidates: tuple[DishSearchCandidate, ...]


def normalize_dish_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Dish name must be text")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", "", normalized).casefold().strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("Dish name must contain 1 to 128 normalized characters")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DishCatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start_import_batch(
        self,
        *,
        manifest_sha256: str,
        imported_by: str,
        created_at: datetime,
    ) -> DishCatalogImportBatch:
        row = DishCatalogImportBatchRecord(
            id=new_uuid(),
            manifest_sha256=manifest_sha256,
            imported_by=imported_by,
            status="running",
            entity_count=0,
            duplicate_count=0,
            created_at=created_at,
        )
        async with self.database.session() as session, session.begin():
            session.add(row)
            await session.flush()
        return self._batch(row)

    async def finish_import_batch(
        self,
        batch_id: str,
        *,
        status: str,
        entity_count: int,
        duplicate_count: int,
        completed_at: datetime,
        failure_code: str | None = None,
    ) -> DishCatalogImportBatch:
        async with self.database.session() as session, session.begin():
            row = await session.get(DishCatalogImportBatchRecord, batch_id)
            if row is None:
                raise DishCatalogNotFound(f"Dish import batch {batch_id} does not exist")
            if row.status != "running":
                raise DishCatalogGovernanceError("Dish import batch is no longer running")
            row.status = status
            row.entity_count = entity_count
            row.duplicate_count = duplicate_count
            row.failure_code = failure_code
            row.completed_at = completed_at
            await session.flush()
            return self._batch(row)

    async def append_document(
        self,
        *,
        batch_id: str,
        document: DishCatalogDocument,
        document_sha256: str,
        created_at: datetime,
    ) -> ImportedDishCatalogEntry:
        async with self.database.session() as session, session.begin():
            existing = await session.scalar(
                select(DishEntityRecord).where(
                    DishEntityRecord.entity_key == document.entity_key,
                    DishEntityRecord.version == document.version,
                )
            )
            if existing is not None:
                if existing.document_sha256 != document_sha256:
                    raise DishCatalogConflict(
                        "Dish entity key/version already exists with different content"
                    )
                return ImportedDishCatalogEntry(
                    entry=await self._entry_in_session(session, existing),
                    created=False,
                )
            row = DishEntityRecord(
                id=new_uuid(),
                import_batch_id=batch_id,
                entity_key=document.entity_key,
                version=document.version,
                canonical_name=document.canonical_name,
                canonical_name_normalized=normalize_dish_name(document.canonical_name),
                cuisine=document.cuisine,
                source_refs_json=_canonical_json(list(document.source_refs)),
                metadata_json=_canonical_json(document.metadata),
                document_sha256=document_sha256,
                status=DishCatalogStatus.DRAFT.value,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(row)
            await session.flush()
            session.add_all(
                DishAliasRecord(
                    id=new_uuid(),
                    dish_entity_id=row.id,
                    alias=item.name,
                    alias_normalized=normalize_dish_name(item.name),
                    region=item.region,
                    source_ref=item.source_ref,
                )
                for item in document.aliases
            )
            session.add_all(
                DishTraitRecord(
                    id=new_uuid(),
                    dish_entity_id=row.id,
                    trait_key=item.trait_key,
                    statement=item.statement,
                    certainty=item.certainty,
                    preparation_scope=item.preparation_scope,
                    source_ref=item.source_ref,
                )
                for item in document.traits
            )
            session.add_all(
                DishRuleRecord(
                    id=new_uuid(),
                    dish_entity_id=row.id,
                    rule_key=item.rule_key,
                    condition_type=item.condition_type,
                    condition_value=item.condition_value,
                    effect=item.effect,
                    statement=item.statement,
                    applicability_json=_canonical_json(list(item.applicability)),
                    source_ref=item.source_ref,
                    version=item.version,
                )
                for item in document.rules
            )
            try:
                await session.flush()
            except IntegrityError as error:
                raise DishCatalogConflict("Dish catalog child records conflict") from error
            return ImportedDishCatalogEntry(
                entry=await self._entry_in_session(session, row),
                created=True,
            )

    async def get_entry(self, entity_id: str) -> DishCatalogEntry | None:
        async with self.database.session() as session:
            row = await session.get(DishEntityRecord, entity_id)
            if row is None:
                return None
            return await self._entry_in_session(session, row)

    async def get_published_entry(self, entity_id: str) -> DishCatalogEntry | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(DishEntityRecord).where(
                    DishEntityRecord.id == entity_id,
                    DishEntityRecord.status == DishCatalogStatus.PUBLISHED.value,
                )
            )
            if row is None:
                return None
            return await self._entry_in_session(session, row)

    async def list_reviews(self, entity_id: str) -> tuple[DishCatalogReview, ...]:
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(DishCatalogReviewRecord)
                    .where(DishCatalogReviewRecord.dish_entity_id == entity_id)
                    .order_by(DishCatalogReviewRecord.created_at, DishCatalogReviewRecord.id)
                )
            )
            return tuple(self._review(row) for row in rows)

    async def transition(
        self,
        entity_id: str,
        *,
        decision: DishCatalogDecision,
        reviewer: str,
        reason: str | None,
        decided_at: datetime,
    ) -> DishCatalogEntry:
        target, allowed = self._transition(decision)
        if decision in {DishCatalogDecision.REJECT, DishCatalogDecision.RETIRE} and not reason:
            raise ValueError(f"{decision.value} requires a reason")
        async with self.database.session() as session, session.begin():
            row = await session.get(DishEntityRecord, entity_id)
            if row is None:
                raise DishCatalogNotFound(f"Dish entity {entity_id} does not exist")
            if row.status not in allowed:
                raise DishCatalogGovernanceError(
                    f"Cannot {decision.value} dish entity in {row.status} status"
                )
            if target is DishCatalogStatus.PUBLISHED:
                existing = await session.scalar(
                    select(DishEntityRecord).where(
                        DishEntityRecord.entity_key == row.entity_key,
                        DishEntityRecord.status == DishCatalogStatus.PUBLISHED.value,
                        DishEntityRecord.id != row.id,
                    )
                )
                if existing is not None:
                    raise DishCatalogGovernanceError(
                        "Retire the currently published dish version before publishing another"
                    )
            previous = row.status
            row.status = target.value
            row.updated_at = decided_at
            if target is DishCatalogStatus.PUBLISHED:
                row.published_at = decided_at
            if target is DishCatalogStatus.RETIRED:
                row.retired_at = decided_at
            session.add(
                DishCatalogReviewRecord(
                    id=new_uuid(),
                    dish_entity_id=row.id,
                    reviewer=reviewer,
                    decision=decision.value,
                    status_from=previous,
                    status_to=target.value,
                    reason=reason,
                    document_sha256=row.document_sha256,
                    created_at=decided_at,
                )
            )
            await session.flush()
            return await self._entry_in_session(session, row)

    async def search_published(self, name: str, *, limit: int = 10) -> DishSearchResult:
        if not 1 <= limit <= 50:
            raise ValueError("Dish search limit must be between 1 and 50")
        normalized = normalize_dish_name(name)
        async with self.database.session() as session:
            canonical_rows = tuple(
                await session.scalars(
                    select(DishEntityRecord)
                    .where(
                        DishEntityRecord.status == DishCatalogStatus.PUBLISHED.value,
                        DishEntityRecord.canonical_name_normalized == normalized,
                    )
                    .order_by(DishEntityRecord.canonical_name, DishEntityRecord.id)
                    .limit(limit)
                )
            )
            candidates: dict[str, DishSearchCandidate] = {
                row.id: DishSearchCandidate(
                    entity=self._entity(row),
                    matched_by="canonical_name",
                    matched_text=row.canonical_name,
                )
                for row in canonical_rows
            }
            if len(candidates) < limit:
                alias_rows = tuple(
                    (
                        await session.execute(
                            select(DishEntityRecord, DishAliasRecord)
                            .join(
                                DishAliasRecord,
                                DishAliasRecord.dish_entity_id == DishEntityRecord.id,
                            )
                            .where(
                                DishEntityRecord.status
                                == DishCatalogStatus.PUBLISHED.value,
                                DishAliasRecord.alias_normalized == normalized,
                            )
                            .order_by(DishEntityRecord.canonical_name, DishEntityRecord.id)
                            .limit(limit)
                        )
                    ).tuples()
                )
                for entity, alias in alias_rows:
                    candidates.setdefault(
                        entity.id,
                        DishSearchCandidate(
                            entity=self._entity(entity),
                            matched_by="alias",
                            matched_text=alias.alias,
                        ),
                    )
        ordered = tuple(candidates.values())[:limit]
        if not ordered:
            status = DishCatalogMatchStatus.NOT_FOUND
        elif len(ordered) > 1:
            status = DishCatalogMatchStatus.AMBIGUOUS
        elif ordered[0].matched_by == "canonical_name":
            status = DishCatalogMatchStatus.EXACT
        else:
            status = DishCatalogMatchStatus.ALIAS
        return DishSearchResult(
            query=name,
            normalized_query=normalized,
            status=status,
            candidates=ordered,
        )

    async def _entry_in_session(
        self, session: AsyncSession, row: DishEntityRecord
    ) -> DishCatalogEntry:
        aliases = tuple(
            await session.scalars(
                select(DishAliasRecord)
                .where(DishAliasRecord.dish_entity_id == row.id)
                .order_by(DishAliasRecord.alias_normalized, DishAliasRecord.id)
            )
        )
        traits = tuple(
            await session.scalars(
                select(DishTraitRecord)
                .where(DishTraitRecord.dish_entity_id == row.id)
                .order_by(DishTraitRecord.trait_key, DishTraitRecord.id)
            )
        )
        rules = tuple(
            await session.scalars(
                select(DishRuleRecord)
                .where(DishRuleRecord.dish_entity_id == row.id)
                .order_by(DishRuleRecord.rule_key, DishRuleRecord.id)
            )
        )
        return DishCatalogEntry(
            entity=self._entity(row),
            aliases=tuple(self._alias(item) for item in aliases),
            traits=tuple(self._trait(item) for item in traits),
            rules=tuple(self._rule(item) for item in rules),
        )

    @staticmethod
    def _transition(
        decision: DishCatalogDecision,
    ) -> tuple[DishCatalogStatus, frozenset[str]]:
        if decision is DishCatalogDecision.APPROVE:
            return DishCatalogStatus.APPROVED, frozenset({"draft"})
        if decision is DishCatalogDecision.REJECT:
            return DishCatalogStatus.REJECTED, frozenset({"draft"})
        if decision is DishCatalogDecision.PUBLISH:
            return DishCatalogStatus.PUBLISHED, frozenset({"approved"})
        return DishCatalogStatus.RETIRED, frozenset(
            {"draft", "approved", "rejected", "published"}
        )

    @staticmethod
    def _batch(row: DishCatalogImportBatchRecord) -> DishCatalogImportBatch:
        return DishCatalogImportBatch(
            id=row.id,
            manifest_sha256=row.manifest_sha256,
            imported_by=row.imported_by,
            status=row.status,
            entity_count=row.entity_count,
            duplicate_count=row.duplicate_count,
            failure_code=row.failure_code,
            created_at=_aware(row.created_at) or row.created_at,
            completed_at=_aware(row.completed_at),
        )

    @staticmethod
    def _entity(row: DishEntityRecord) -> DishEntity:
        source_refs = json.loads(row.source_refs_json)
        metadata = json.loads(row.metadata_json)
        if not isinstance(source_refs, list) or any(
            not isinstance(item, str) for item in source_refs
        ):
            raise DishCatalogConflict("Stored dish source references are invalid")
        if not isinstance(metadata, dict):
            raise DishCatalogConflict("Stored dish metadata is invalid")
        return DishEntity(
            id=row.id,
            import_batch_id=row.import_batch_id,
            entity_key=row.entity_key,
            version=row.version,
            canonical_name=row.canonical_name,
            normalized_name=row.canonical_name_normalized,
            cuisine=row.cuisine,
            source_refs=tuple(source_refs),
            metadata=metadata,
            document_sha256=row.document_sha256,
            status=row.status,
            created_at=_aware(row.created_at) or row.created_at,
            updated_at=_aware(row.updated_at) or row.updated_at,
            published_at=_aware(row.published_at),
            retired_at=_aware(row.retired_at),
        )

    @staticmethod
    def _alias(row: DishAliasRecord) -> DishAlias:
        return DishAlias(
            id=row.id,
            dish_entity_id=row.dish_entity_id,
            name=row.alias,
            normalized_name=row.alias_normalized,
            region=row.region,
            source_ref=row.source_ref,
        )

    @staticmethod
    def _trait(row: DishTraitRecord) -> DishTrait:
        return DishTrait(
            id=row.id,
            dish_entity_id=row.dish_entity_id,
            trait_key=row.trait_key,
            statement=row.statement,
            certainty=row.certainty,
            preparation_scope=row.preparation_scope,
            source_ref=row.source_ref,
        )

    @staticmethod
    def _rule(row: DishRuleRecord) -> DishRule:
        applicability = json.loads(row.applicability_json)
        if not isinstance(applicability, list) or any(
            not isinstance(item, str) for item in applicability
        ):
            raise DishCatalogConflict("Stored dish rule applicability is invalid")
        return DishRule(
            id=row.id,
            dish_entity_id=row.dish_entity_id,
            rule_key=row.rule_key,
            condition_type=row.condition_type,
            condition_value=row.condition_value,
            effect=row.effect,
            statement=row.statement,
            applicability=tuple(applicability),
            source_ref=row.source_ref,
            version=row.version,
        )

    @staticmethod
    def _review(row: DishCatalogReviewRecord) -> DishCatalogReview:
        return DishCatalogReview(
            id=row.id,
            dish_entity_id=row.dish_entity_id,
            reviewer=row.reviewer,
            decision=row.decision,
            status_from=row.status_from,
            status_to=row.status_to,
            reason=row.reason,
            document_sha256=row.document_sha256,
            created_at=_aware(row.created_at) or row.created_at,
        )


class DishCatalogService:
    def __init__(self, repository: DishCatalogRepository) -> None:
        self.repository = repository

    async def import_documents(
        self,
        documents: tuple[DishCatalogDocument, ...],
        *,
        imported_by: str,
        created_at: datetime | None = None,
    ) -> DishCatalogImportResult:
        if not documents:
            raise ValueError("At least one dish document is required")
        if len(documents) > 2_000:
            raise ValueError("A dish import is limited to 2000 documents")
        now = created_at or utc_now()
        actor = " ".join(imported_by.split())
        if not actor or len(actor) > 128:
            raise ValueError("Dish import actor must contain 1 to 128 characters")
        canonical_documents = tuple(self._normalized_document(item) for item in documents)
        serialized = tuple(
            _canonical_json(item.model_dump(mode="json")) for item in canonical_documents
        )
        manifest_sha256 = _sha256(_canonical_json(list(serialized)))
        batch = await self.repository.start_import_batch(
            manifest_sha256=manifest_sha256,
            imported_by=actor,
            created_at=now,
        )
        entries: list[ImportedDishCatalogEntry] = []
        created_count = 0
        duplicate_count = 0
        try:
            for document, encoded in zip(canonical_documents, serialized, strict=True):
                imported = await self.repository.append_document(
                    batch_id=batch.id,
                    document=document,
                    document_sha256=_sha256(encoded),
                    created_at=now,
                )
                entries.append(imported)
                if imported.created:
                    created_count += 1
                else:
                    duplicate_count += 1
            completed = await self.repository.finish_import_batch(
                batch.id,
                status="completed",
                entity_count=created_count,
                duplicate_count=duplicate_count,
                completed_at=now,
            )
        except Exception as error:
            await self.repository.finish_import_batch(
                batch.id,
                status="failed",
                entity_count=created_count,
                duplicate_count=duplicate_count,
                completed_at=now,
                failure_code=type(error).__name__[:128],
            )
            raise
        return DishCatalogImportResult(batch=completed, entries=tuple(entries))

    async def approve(
        self, entity_id: str, *, reviewer: str, reason: str | None = None
    ) -> DishCatalogEntry:
        return await self._transition(entity_id, DishCatalogDecision.APPROVE, reviewer, reason)

    async def reject(
        self, entity_id: str, *, reviewer: str, reason: str
    ) -> DishCatalogEntry:
        return await self._transition(entity_id, DishCatalogDecision.REJECT, reviewer, reason)

    async def publish(
        self, entity_id: str, *, reviewer: str, reason: str | None = None
    ) -> DishCatalogEntry:
        return await self._transition(entity_id, DishCatalogDecision.PUBLISH, reviewer, reason)

    async def retire(
        self, entity_id: str, *, reviewer: str, reason: str
    ) -> DishCatalogEntry:
        return await self._transition(entity_id, DishCatalogDecision.RETIRE, reviewer, reason)

    async def _transition(
        self,
        entity_id: str,
        decision: DishCatalogDecision,
        reviewer: str,
        reason: str | None,
    ) -> DishCatalogEntry:
        actor = " ".join(reviewer.split())
        if not actor or len(actor) > 128:
            raise ValueError("Reviewer must contain 1 to 128 characters")
        normalized_reason = " ".join(reason.split()) if reason is not None else None
        return await self.repository.transition(
            entity_id,
            decision=decision,
            reviewer=actor,
            reason=normalized_reason,
            decided_at=utc_now(),
        )

    @staticmethod
    def _normalized_document(document: DishCatalogDocument) -> DishCatalogDocument:
        # Round-trip through canonical JSON to reject non-finite or non-JSON metadata.
        return DishCatalogDocument.model_validate_json(
            _canonical_json(document.model_dump(mode="json"))
        )


__all__ = [
    "DishAlias",
    "DishAliasDocument",
    "DishCatalogConflict",
    "DishCatalogDecision",
    "DishCatalogDocument",
    "DishCatalogEntry",
    "DishCatalogError",
    "DishCatalogGovernanceError",
    "DishCatalogImportBatch",
    "DishCatalogImportResult",
    "DishCatalogMatchStatus",
    "DishCatalogNotFound",
    "DishCatalogRepository",
    "DishCatalogReview",
    "DishCatalogService",
    "DishCatalogStatus",
    "DishEntity",
    "DishRule",
    "DishRuleDocument",
    "DishSearchCandidate",
    "DishSearchResult",
    "DishTrait",
    "DishTraitDocument",
    "ImportedDishCatalogEntry",
    "normalize_dish_name",
]
