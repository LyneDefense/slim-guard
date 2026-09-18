"""Structured output of the participant routing model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from slim_guard.runtime.contracts import ContractModel


class ParticipantRoutingDecision(ContractModel):
    schema_version: Literal["1"] = "1"
    coach_text: str | None = Field(default=None, max_length=2000)
    coach_source_refs: tuple[str, ...] = Field(default=(), max_length=64)


__all__ = ["ParticipantRoutingDecision"]
