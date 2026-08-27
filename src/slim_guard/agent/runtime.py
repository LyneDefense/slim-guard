from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.runner import (
    HarnessTurnGrants,
    HarnessTurnRunner,
    HarnessTurnRunResult,
)
from slim_guard.tools.contracts import ToolExecutionMode


class AgentRuntimeRequest(BaseModel):
    """Trusted application command for one inbound user message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20_000)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=256)
    channel_id: str | None = Field(default=None, min_length=1, max_length=128)
    occurred_at: datetime | None = None
    deadline_at: datetime | None = None
    execution_mode: ToolExecutionMode = ToolExecutionMode.LIVE
    isolated_write_environment: bool = False

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent user message cannot be empty")
        return normalized


class AgentRuntime:
    """Stable application entry point over the internal Agent Harness graph."""

    def __init__(
        self,
        *,
        manifest: AgentManifest,
        versions: AgentVersionRepository,
        runner: HarnessTurnRunner,
    ) -> None:
        self.manifest = manifest
        self._versions = versions
        self._runner = runner

    async def run_user_message(
        self,
        request: AgentRuntimeRequest,
    ) -> HarnessTurnRunResult:
        await self._versions.register(self.manifest)
        return await self._runner.run(
            request=TurnInitializationRequest(
                user_id=request.user_id,
                agent_version_id=self.manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=request.execution_mode,
                deadline_at=request.deadline_at,
                inputs=(
                    TurnInput.user_message(
                        text=request.text,
                        source_message_id=request.source_message_id,
                        channel_id=request.channel_id,
                        occurred_at=request.occurred_at,
                    ),
                ),
            ),
            grants=HarnessTurnGrants(
                isolated_write_environment=request.isolated_write_environment,
            ),
        )
