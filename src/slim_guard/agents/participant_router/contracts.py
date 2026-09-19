"""Structured output of the participant routing model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from slim_guard.runtime.contracts import ContractModel


class ParticipantRoutingDecision(ContractModel):
    schema_version: Literal["1"] = "1"
    # The router owns the final partition between the system assistant and the
    # coach.  Core's draft is only a semantic candidate and may accidentally
    # contain relational language; keeping this field in the same structured
    # decision lets the model remove that leakage instead of leaving duplicate
    # or misattributed text in the chat.
    system_text: str = Field(min_length=1, max_length=4000)
    coach_text: str | None = Field(default=None, max_length=2000)
    coach_source_refs: tuple[str, ...] = Field(default=(), max_length=64)


__all__ = ["ParticipantRoutingDecision"]
