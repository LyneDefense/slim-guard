"""Bounded, provenance-preserving evidence for professional agent calls.

The builder accepts only current-turn items plus a deliberately small allowlist of
trusted context sections.  It does not search old dialogue and it never promotes a
visual observation to authoritative evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceTurnItem(Protocol):
    """Structural current-item input; avoids importing the Harness runtime graph."""

    id: str
    item_type: object
    payload: Mapping[str, Any]


class EvidenceAuthority(StrEnum):
    AUTHORITATIVE = "authoritative"
    USER_REPORTED = "user_reported"
    OBSERVATION = "observation"


class EvidenceSourceType(StrEnum):
    USER_MESSAGE = "user_message"
    VISION_OBSERVATION = "vision_observation"
    WEIGHT_RECORD = "weight_record"
    BODY_FAT_RECORD = "body_fat_record"
    MEAL_RECORD = "meal_record"
    EXERCISE_RECORD = "exercise_record"
    PROFILE_MEMORY = "profile_memory"
    GOAL_MEMORY = "goal_memory"
    CONSTRAINT_MEMORY = "constraint_memory"
    TOOL_RECEIPT = "tool_receipt"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    KNOWLEDGE_EVIDENCE = "knowledge_evidence"


class EvidenceConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceItem(BaseModel):
    """One immutable fact or observation with explicit origin and confidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=128)
    source_type: EvidenceSourceType
    authority: EvidenceAuthority
    occurred_at: datetime | None = None
    content: dict[str, Any]
    confidence: EvidenceConfidence | None = None
    uncertainty: str | None = Field(default=None, min_length=1, max_length=512)
    source_ref: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Evidence timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_provenance_strength(self) -> Self:
        if self.source_type is EvidenceSourceType.VISION_OBSERVATION:
            if self.authority is not EvidenceAuthority.OBSERVATION:
                raise ValueError("Visual evidence must have observation authority")
            if self.confidence is None or self.uncertainty is None:
                raise ValueError("Visual evidence requires confidence and explicit uncertainty")
        if self.authority is EvidenceAuthority.AUTHORITATIVE and self.source_type in {
            EvidenceSourceType.USER_MESSAGE,
            EvidenceSourceType.PROFILE_MEMORY,
            EvidenceSourceType.GOAL_MEMORY,
            EvidenceSourceType.CONSTRAINT_MEMORY,
            EvidenceSourceType.VISION_OBSERVATION,
        }:
            raise ValueError("User reports and observations cannot be authoritative")
        return self

    # Compatibility names make the contract easy to consume from generic agents
    # without duplicating the canonical fields in serialized artifacts.
    @property
    def id(self) -> str:
        return self.evidence_id

    @property
    def kind(self) -> EvidenceSourceType:
        return self.source_type

    @property
    def type(self) -> EvidenceSourceType:
        return self.source_type


class EvidencePacket(BaseModel):
    """All evidence an agent may use for one professional question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1", pattern=r"^1$")
    turn_id: str = Field(min_length=1, max_length=128)
    user_request: str = Field(min_length=1, max_length=4000)
    professional_question: str = Field(min_length=1, max_length=2000)
    items: tuple[EvidenceItem, ...] = Field(default=(), max_length=64)
    missing_information: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("missing_information")
    @classmethod
    def validate_missing_information(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Missing-information entries cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Missing-information entries must be unique")
        return value

    @model_validator(mode="after")
    def validate_item_ids(self) -> Self:
        ids = self.evidence_ids
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence IDs must be unique within a packet")
        return self

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.items)

    def require_refs(self, refs: Sequence[str]) -> tuple[EvidenceItem, ...]:
        by_id = {item.evidence_id: item for item in self.items}
        unknown = sorted(set(refs).difference(by_id))
        if unknown:
            raise ValueError("Unknown evidence references: " + ", ".join(unknown))
        return tuple(by_id[ref] for ref in refs)


class EvidenceBuilder:
    """Compile current-turn and allowlisted context into a small evidence packet."""

    _CONTEXT_SOURCES: tuple[
        tuple[str, EvidenceSourceType, EvidenceAuthority, tuple[str, ...]], ...
    ] = (
        (
            "recent_weights",
            EvidenceSourceType.WEIGHT_RECORD,
            EvidenceAuthority.AUTHORITATIVE,
            ("id", "record_id", "weight_kg", "measured_at", "condition", "status"),
        ),
        (
            "recent_body_fat",
            EvidenceSourceType.BODY_FAT_RECORD,
            EvidenceAuthority.AUTHORITATIVE,
            ("id", "record_id", "body_fat_percent", "measured_at", "status"),
        ),
        (
            "recent_meals",
            EvidenceSourceType.MEAL_RECORD,
            EvidenceAuthority.AUTHORITATIVE,
            ("id", "record_id", "meal_type", "foods", "note", "occurred_at", "status"),
        ),
        (
            "recent_exercise",
            EvidenceSourceType.EXERCISE_RECORD,
            EvidenceAuthority.AUTHORITATIVE,
            (
                "id",
                "record_id",
                "activity_name",
                "duration_minutes",
                "steps",
                "distance_meters",
                "reported_energy_kcal",
                "note",
                "occurred_at",
                "status",
            ),
        ),
    )

    def __init__(
        self,
        *,
        max_items: int = 32,
        max_item_chars: int = 2_000,
        max_total_chars: int = 12_000,
    ) -> None:
        if max_items < 1 or max_item_chars < 64 or max_total_chars < max_item_chars:
            raise ValueError("Evidence bounds must be positive and internally consistent")
        self._max_items = min(max_items, 64)
        self._max_item_chars = max_item_chars
        self._max_total_chars = max_total_chars

    async def build(
        self,
        *,
        turn_id: str,
        user_request: str,
        professional_question: str,
        current_items: Sequence[EvidenceTurnItem | Mapping[str, Any]],
        authoritative_context: Mapping[str, Any],
        allowed_evidence_refs: Sequence[str] | None = None,
        missing_information: Sequence[str] = (),
    ) -> EvidencePacket:
        candidates: list[EvidenceItem] = []
        missing = list(dict.fromkeys(item.strip() for item in missing_information if item.strip()))

        for item in current_items:
            evidence = self._from_turn_item(turn_id=turn_id, item=item, missing=missing)
            if evidence is not None:
                candidates.append(evidence)

        for key, source_type, authority, fields in self._CONTEXT_SOURCES:
            raw_values = authoritative_context.get(key, ())
            if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
                continue
            for index, raw in enumerate(raw_values):
                if not isinstance(raw, Mapping):
                    continue
                content = {field: raw[field] for field in fields if field in raw}
                candidates.append(
                    self._make_item(
                        turn_id=turn_id,
                        source_type=source_type,
                        authority=authority,
                        content=content,
                        source_ref=self._source_ref(raw, fallback=f"{key}:{index}"),
                        occurred_at=self._timestamp(raw),
                        confidence=EvidenceConfidence.HIGH,
                    )
                )

        self._append_memories(
            turn_id=turn_id,
            context=authoritative_context,
            candidates=candidates,
        )
        self._append_checkin_status(
            turn_id=turn_id,
            context=authoritative_context,
            candidates=candidates,
        )
        self._append_visual_observations(
            turn_id=turn_id,
            context=authoritative_context,
            candidates=candidates,
            missing=missing,
        )

        if allowed_evidence_refs is not None:
            allowed = {ref for ref in allowed_evidence_refs if ref}
            matched: set[str] = set()
            filtered: list[EvidenceItem] = []
            for candidate in candidates:
                refs = {candidate.evidence_id}
                if candidate.source_ref is not None:
                    refs.add(candidate.source_ref)
                overlap = refs.intersection(allowed)
                if overlap:
                    filtered.append(candidate)
                    matched.update(overlap)
            for unknown in sorted(allowed.difference(matched)):
                missing.append(f"unresolved_evidence_ref:{unknown}")
            candidates = filtered

        bounded: list[EvidenceItem] = []
        seen: set[str] = set()
        used_chars = 0
        for candidate in candidates:
            if candidate.evidence_id in seen:
                continue
            content, was_truncated = self._bounded_content(candidate.content)
            if was_truncated:
                missing.append(f"truncated_evidence:{candidate.evidence_id}")
                candidate = candidate.model_copy(update={"content": content})
            item_chars = len(self._canonical(candidate.model_dump(mode="json")))
            if len(bounded) >= self._max_items or used_chars + item_chars > self._max_total_chars:
                missing.append("evidence_packet_capacity_reached")
                break
            bounded.append(candidate)
            seen.add(candidate.evidence_id)
            used_chars += item_chars

        return EvidencePacket(
            turn_id=turn_id,
            user_request=user_request,
            professional_question=professional_question,
            items=tuple(bounded),
            missing_information=tuple(dict.fromkeys(missing)),
        )

    def _from_turn_item(
        self,
        *,
        turn_id: str,
        item: EvidenceTurnItem | Mapping[str, Any],
        missing: list[str],
    ) -> EvidenceItem | None:
        if isinstance(item, Mapping):
            item_id = item.get("id")
            raw_type = item.get("item_type")
            payload = item.get("payload", {})
        else:
            item_id = item.id
            raw_type = item.item_type
            payload = item.payload
        item_type = str(getattr(raw_type, "value", raw_type) or "")
        if not isinstance(payload, Mapping):
            return None
        source_ref = item_id if isinstance(item_id, str) and item_id else None
        if item_type == "user_message":
            content = {
                key: payload[key] for key in ("text", "content", "occurred_at") if key in payload
            }
            if not content:
                return None
            return self._make_item(
                turn_id=turn_id,
                source_type=EvidenceSourceType.USER_MESSAGE,
                authority=EvidenceAuthority.USER_REPORTED,
                content=content,
                source_ref=source_ref,
                occurred_at=self._timestamp(payload),
                confidence=EvidenceConfidence.HIGH,
            )
        if item_type == "image_attachment":
            observation = payload.get("observation")
            if not isinstance(observation, (str, Mapping)) or not observation:
                missing.append(f"visual_observation_missing:{source_ref or 'image'}")
                return None
            requires_confirmation = payload.get("requires_user_confirmation", True) is not False
            uncertainty = payload.get("uncertainty")
            if not isinstance(uncertainty, str) or not uncertainty.strip():
                uncertainty = (
                    "Visual interpretation requires user confirmation"
                    if requires_confirmation
                    else "Visual interpretation may omit or misclassify details"
                )
            return self._make_item(
                turn_id=turn_id,
                source_type=EvidenceSourceType.VISION_OBSERVATION,
                authority=EvidenceAuthority.OBSERVATION,
                content={
                    "asset_id": payload.get("asset_id") or source_ref,
                    "observation": observation,
                    "requires_user_confirmation": requires_confirmation,
                },
                source_ref=str(payload.get("asset_id") or source_ref or "image"),
                occurred_at=self._timestamp(payload),
                confidence=self._confidence(payload.get("confidence")),
                uncertainty=uncertainty,
            )
        if item_type == "tool_result":
            safe = {
                key: payload[key]
                for key in ("tool_name", "tool_version", "status", "output", "source_ids")
                if key in payload
            }
            return self._make_item(
                turn_id=turn_id,
                source_type=EvidenceSourceType.TOOL_RECEIPT,
                authority=EvidenceAuthority.AUTHORITATIVE,
                content=safe,
                source_ref=source_ref,
                occurred_at=self._timestamp(payload),
                confidence=EvidenceConfidence.HIGH,
            )
        return None

    def _append_memories(
        self,
        *,
        turn_id: str,
        context: Mapping[str, Any],
        candidates: list[EvidenceItem],
    ) -> None:
        raw_memories = context.get("profile_memory", ())
        if not isinstance(raw_memories, Sequence) or isinstance(raw_memories, (str, bytes)):
            return
        for index, raw in enumerate(raw_memories):
            if not isinstance(raw, Mapping):
                continue
            memory_kind = str(raw.get("kind", "")).lower()
            memory_key = str(raw.get("key", "")).lower()
            if "goal" in memory_kind or "goal" in memory_key:
                source_type = EvidenceSourceType.GOAL_MEMORY
            elif (
                "constraint" in memory_kind or "constraint" in memory_key or "allergy" in memory_key
            ):
                source_type = EvidenceSourceType.CONSTRAINT_MEMORY
            else:
                source_type = EvidenceSourceType.PROFILE_MEMORY
            content = {
                key: raw[key]
                for key in ("memory_id", "kind", "key", "value", "assertion", "stale")
                if key in raw
            }
            candidates.append(
                self._make_item(
                    turn_id=turn_id,
                    source_type=source_type,
                    authority=EvidenceAuthority.USER_REPORTED,
                    content=content,
                    source_ref=self._source_ref(raw, fallback=f"profile_memory:{index}"),
                    occurred_at=self._timestamp(raw),
                    confidence=(
                        EvidenceConfidence.LOW
                        if raw.get("stale") is True
                        else EvidenceConfidence.MEDIUM
                    ),
                    uncertainty=("Memory is marked stale" if raw.get("stale") is True else None),
                )
            )

    def _append_checkin_status(
        self,
        *,
        turn_id: str,
        context: Mapping[str, Any],
        candidates: list[EvidenceItem],
    ) -> None:
        raw = context.get("today_checkin_status")
        if not isinstance(raw, Mapping):
            return
        fields = (
            "local_date",
            "timezone",
            "weight_count",
            "meal_count",
            "exercise_count",
        )
        candidates.append(
            self._make_item(
                turn_id=turn_id,
                source_type=EvidenceSourceType.TOOL_RECEIPT,
                authority=EvidenceAuthority.AUTHORITATIVE,
                content={field: raw[field] for field in fields if field in raw},
                source_ref=f"checkin_status:{raw.get('local_date', 'current')}",
                confidence=EvidenceConfidence.HIGH,
            )
        )

    def _append_visual_observations(
        self,
        *,
        turn_id: str,
        context: Mapping[str, Any],
        candidates: list[EvidenceItem],
        missing: list[str],
    ) -> None:
        working = context.get("working_memory")
        if not isinstance(working, Mapping):
            return
        images = working.get("recent_images", ())
        if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
            return
        known_refs = {item.source_ref for item in candidates if item.source_ref}
        for index, raw in enumerate(images):
            if not isinstance(raw, Mapping):
                continue
            source_ref = self._source_ref(raw, fallback=f"recent_image:{index}")
            if source_ref in known_refs:
                continue
            observation = raw.get("observation")
            if not isinstance(observation, (str, Mapping)) or not observation:
                missing.append(f"visual_observation_missing:{source_ref}")
                continue
            requires_confirmation = raw.get("requires_user_confirmation", True) is not False
            uncertainty = raw.get("uncertainty")
            if not isinstance(uncertainty, str) or not uncertainty.strip():
                uncertainty = (
                    "Visual interpretation requires user confirmation"
                    if requires_confirmation
                    else "Visual interpretation may omit or misclassify details"
                )
            candidates.append(
                self._make_item(
                    turn_id=turn_id,
                    source_type=EvidenceSourceType.VISION_OBSERVATION,
                    authority=EvidenceAuthority.OBSERVATION,
                    content={
                        "asset_id": raw.get("asset_id") or source_ref,
                        "observation": observation,
                        "requires_user_confirmation": requires_confirmation,
                    },
                    source_ref=source_ref,
                    occurred_at=self._timestamp(raw),
                    confidence=self._confidence(raw.get("confidence")),
                    uncertainty=uncertainty,
                )
            )

    def _make_item(
        self,
        *,
        turn_id: str,
        source_type: EvidenceSourceType,
        authority: EvidenceAuthority,
        content: Mapping[str, Any],
        source_ref: str | None,
        occurred_at: datetime | None = None,
        confidence: EvidenceConfidence | None = None,
        uncertainty: str | None = None,
    ) -> EvidenceItem:
        normalized_content = dict(content)
        digest = hashlib.sha256(
            self._canonical(
                {
                    "turn_id": turn_id,
                    "source_type": source_type.value,
                    "source_ref": source_ref,
                    "content": normalized_content,
                }
            ).encode("utf-8")
        ).hexdigest()[:24]
        return EvidenceItem(
            evidence_id=f"evidence-{digest}",
            source_type=source_type,
            authority=authority,
            occurred_at=occurred_at,
            content=normalized_content,
            confidence=confidence,
            uncertainty=uncertainty,
            source_ref=source_ref,
        )

    def _bounded_content(self, content: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if len(self._canonical(content)) <= self._max_item_chars:
            return content, False
        return {
            "fields": sorted(str(key) for key in content),
            "truncated": True,
        }, True

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _source_ref(raw: Mapping[str, Any], *, fallback: str) -> str:
        for key in ("evidence_id", "record_id", "memory_id", "asset_id", "id"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
        return fallback

    @staticmethod
    def _timestamp(raw: Mapping[str, Any]) -> datetime | None:
        for key in ("occurred_at", "measured_at", "created_at"):
            value = raw.get(key)
            if isinstance(value, datetime):
                return value if value.utcoffset() is not None else None
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.utcoffset() is not None:
                    return parsed
        return None

    @staticmethod
    def _confidence(raw: Any) -> EvidenceConfidence:
        try:
            return EvidenceConfidence(str(raw))
        except ValueError:
            return EvidenceConfidence.LOW


__all__ = [
    "EvidenceAuthority",
    "EvidenceBuilder",
    "EvidenceConfidence",
    "EvidenceItem",
    "EvidencePacket",
    "EvidenceSourceType",
    "EvidenceTurnItem",
]
