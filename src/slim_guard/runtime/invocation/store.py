"""Persistence port owned by the Invocation Runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from slim_guard.runtime.contracts import AgentArtifact, AgentInvocation, AgentResult


class InvocationStore(Protocol):
    """Durable store required by the Turn and specialist runtimes."""

    async def start_invocation(
        self,
        invocation: AgentInvocation,
        *,
        reason_summary: str | None = None,
        started_at: datetime | None = None,
    ) -> object: ...

    async def append_artifact(
        self,
        artifact: AgentArtifact,
        *,
        invocation_id: str | None = None,
    ) -> AgentArtifact: ...

    async def complete_invocation(
        self,
        result: AgentResult,
        *,
        completed_at: datetime | None = None,
    ) -> object: ...
