"""Append-only style corrections captured from authenticated development testing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select

from slim_guard.agents.contracts import CommunicationAct, ContractModel
from slim_guard.db.models import (
    StyleABEvaluationCaseRecord,
    StyleCorrectionFeedbackRecord,
    new_uuid,
    utc_now,
)
from slim_guard.db.session import Database


class StyleCorrectionFeedbackInput(ContractModel):
    """A reviewed expression correction, not an instruction to mutate the live model."""

    profile_version: str = Field(min_length=1, max_length=128)
    communication_act: CommunicationAct | None = None
    scenario: str = Field(min_length=1, max_length=2000)
    user_message: str = Field(min_length=1, max_length=4000)
    agent_response: str = Field(min_length=1, max_length=4000)
    desired_response: str = Field(min_length=1, max_length=4000)
    guidance_note: str | None = Field(default=None, max_length=2000)
    deidentified_confirmed: Literal[True]
    expression_only_confirmed: Literal[True]

    @field_validator(
        "profile_version",
        "scenario",
        "user_message",
        "agent_response",
        "desired_response",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Style correction fields cannot be blank")
        return normalized

    @field_validator("guidance_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_an_actual_correction(self) -> Self:
        if self.agent_response == self.desired_response:
            raise ValueError("Desired response must differ from the current agent response")
        return self


class StyleCorrectionFeedbackRepository:
    """Stores immutable feedback and exposes it only to the version-building workflow."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def append(
        self,
        payload: StyleCorrectionFeedbackInput,
        *,
        actor: str,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        reviewer = actor.strip()
        if not reviewer or len(reviewer) > 128:
            raise ValueError("Authenticated feedback actor is invalid")
        captured_at = created_at or utc_now()
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("Feedback timestamp must be timezone-aware")
        content = payload.model_dump(mode="json")
        content_sha256 = self._sha256(content)
        row = StyleCorrectionFeedbackRecord(
            id=new_uuid(),
            profile_version=payload.profile_version,
            communication_act=(
                payload.communication_act.value
                if payload.communication_act is not None
                else None
            ),
            scenario=payload.scenario,
            user_message=payload.user_message,
            agent_response=payload.agent_response,
            desired_response=payload.desired_response,
            guidance_note=payload.guidance_note,
            actor=reviewer,
            deidentified_confirmed=True,
            expression_only_confirmed=True,
            content_sha256=content_sha256,
            created_at=captured_at,
        )
        async with self.database.session() as session, session.begin():
            session.add(row)
            await session.flush()
        return self._view(row)

    async def list(
        self,
        *,
        limit: int = 30,
        offset: int = 0,
        profile_version: str | None = None,
        communication_act: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid feedback pagination")
        filters = []
        if profile_version is not None:
            filters.append(
                StyleCorrectionFeedbackRecord.profile_version == profile_version.strip()
            )
        if communication_act is not None:
            filters.append(
                StyleCorrectionFeedbackRecord.communication_act == communication_act
            )
        async with self.database.session() as session:
            total = int(
                await session.scalar(
                    select(func.count(StyleCorrectionFeedbackRecord.id)).where(*filters)
                )
                or 0
            )
            rows = tuple(
                await session.scalars(
                    select(StyleCorrectionFeedbackRecord)
                    .where(*filters)
                    .order_by(
                        StyleCorrectionFeedbackRecord.created_at.desc(),
                        StyleCorrectionFeedbackRecord.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
        return {
            "items": [self._view(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def profile_options(self) -> tuple[str, ...]:
        """Prefer the newest A/B candidate while retaining prior feedback versions."""

        async with self.database.session() as session:
            candidate_versions = tuple(
                await session.scalars(
                    select(StyleABEvaluationCaseRecord.candidate_profile_version)
                    .order_by(StyleABEvaluationCaseRecord.created_at.desc())
                )
            )
            feedback_versions = tuple(
                await session.scalars(
                    select(StyleCorrectionFeedbackRecord.profile_version)
                    .order_by(StyleCorrectionFeedbackRecord.created_at.desc())
                )
            )
        return tuple(dict.fromkeys((*candidate_versions, *feedback_versions)))

    @staticmethod
    def _view(row: StyleCorrectionFeedbackRecord) -> dict[str, Any]:
        created_at = row.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            created_at = created_at.replace(tzinfo=UTC)
        return {
            "feedback_id": row.id,
            "profile_version": row.profile_version,
            "communication_act": row.communication_act,
            "scenario": row.scenario,
            "user_message": row.user_message,
            "agent_response": row.agent_response,
            "desired_response": row.desired_response,
            "guidance_note": row.guidance_note,
            "actor": row.actor,
            "content_sha256": row.content_sha256,
            "created_at": created_at.isoformat(),
        }

    @staticmethod
    def _sha256(value: Any) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()


__all__ = [
    "StyleCorrectionFeedbackInput",
    "StyleCorrectionFeedbackRepository",
]
