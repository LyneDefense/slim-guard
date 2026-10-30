from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

INSIGHT_POLICY_VERSION = "daily-insights-v1"


class InsightEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    kind: str
    label: str
    data: dict[str, Any]
    today: bool = False

    @field_validator("data", mode="before")
    @classmethod
    def canonical_json(cls, value: object) -> dict[str, Any]:
        return dict(json.loads(json.dumps(value, default=str)))


class PersonalContext(BaseModel):
    timezone: str
    evidence: tuple[InsightEvidence, ...] = ()
    style: dict[str, Any] = {}


class PersonalContextReader(Protocol):
    async def read(self, user_id: str) -> PersonalContext: ...


class InsightSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str
    day: date
    timezone: str
    as_of: datetime
    context_key: str
    evidence: tuple[InsightEvidence, ...]
    style: dict[str, Any]
    today_record_count: int
    fingerprint: str
    generation_version: str = INSIGHT_POLICY_VERSION


class InsightClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=500)
    kind: Literal["observation", "assessment", "suggestion"]
    source_refs: tuple[str, ...] = Field(max_length=64)


class InsightText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=600)
    claims: tuple[InsightClaim, ...] = Field(max_length=12)


class InsightDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    headline: InsightText
    summary: InsightText | None


class InsightReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    passed: bool
    issues: tuple[str, ...] = Field(max_length=12)


class DailyInsightsView(BaseModel):
    date: date
    timezone: str
    status: Literal["pending", "running", "ready", "failed", "unavailable"]
    headline: str | None = None
    summary: str | None = None
    summary_state: Literal["empty", "pending", "ready", "failed", "unavailable"]
    as_of: datetime | None = None
    generated_at: datetime | None = None
    evidence: tuple[InsightEvidence, ...] = ()
    failure_code: str | None = None
