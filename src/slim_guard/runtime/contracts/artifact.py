"""Immutable artifact envelopes exchanged between runtime invocations."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from slim_guard.runtime.contracts.base import ContractModel


class ArtifactProducerRole(StrEnum):
    MEMORY_INGESTION = "memory_ingestion"
    MEMORY_RECALL = "memory_recall"
    CORE = "core"
    ORCHESTRATOR = "orchestrator"
    DISH_RECOGNITION = "dish_recognition"
    USER_DISH_CONFIRMATION = "user_dish_confirmation"
    ADMIN_REVIEWER = "admin_reviewer"
    NUTRITION_RETRIEVAL = "nutrition_retrieval"
    BUSINESS_TOOL = "business_tool"
    EVIDENCE_BUILDER = "evidence_builder"
    NUTRITION_EXPERT = "nutrition_expert"
    NUTRITION_TOOL = "nutrition_tool"
    STYLE_RESOLVER = "style_resolver"
    RESPONSE_STYLE = "response_style"
    RESPONSE_REVIEWER = "response_reviewer"
    COORDINATOR = "coordinator"


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Return the stable JSON representation used for artifact hashes."""

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Artifact payload must contain finite JSON values") from error
    return serialized.encode("utf-8")


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


class AgentArtifact(ContractModel):
    """Content-addressed result exchanged between bounded invocations."""

    artifact_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    producer_role: ArtifactProducerRole
    artifact_type: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=32)
    parent_artifact_ids: tuple[str, ...] = Field(default=(), max_length=128)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Artifact creation time must be timezone-aware")
        return value

    @field_validator("parent_artifact_ids")
    @classmethod
    def validate_parent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Artifact parent IDs cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Artifact parent IDs must be unique")
        return value

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.artifact_id in self.parent_artifact_ids:
            raise ValueError("An artifact cannot be its own parent")
        expected = payload_sha256(self.payload)
        if not hmac.compare_digest(expected, self.payload_sha256):
            raise ValueError("Artifact payload_sha256 does not match its canonical payload")
        return self

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        turn_id: str,
        producer_role: ArtifactProducerRole,
        artifact_type: str,
        schema_version: str,
        payload: dict[str, Any],
        created_at: datetime,
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> AgentArtifact:
        return cls(
            artifact_id=artifact_id,
            turn_id=turn_id,
            producer_role=producer_role,
            artifact_type=artifact_type,
            schema_version=schema_version,
            parent_artifact_ids=parent_artifact_ids,
            payload_sha256=payload_sha256(payload),
            payload=payload,
            created_at=created_at,
        )

    def verify_payload(self) -> bool:
        return hmac.compare_digest(payload_sha256(self.payload), self.payload_sha256)


Artifact = AgentArtifact


__all__ = [
    "AgentArtifact",
    "Artifact",
    "ArtifactProducerRole",
    "canonical_payload_bytes",
    "payload_sha256",
]
