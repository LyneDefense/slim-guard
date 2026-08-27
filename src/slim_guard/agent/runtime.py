from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
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
)
from slim_guard.harness.termination import HarnessTermination
from slim_guard.tools.contracts import ToolExecutionMode


class AgentRuntimeRequest(BaseModel):
    """Trusted application command for one inbound user message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=20_000)
    image_bytes: bytes | None = Field(default=None, min_length=1, max_length=20_971_520)
    image_mime_type: str | None = Field(default=None, max_length=128)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=256)
    channel_id: str | None = Field(default=None, min_length=1, max_length=128)
    occurred_at: datetime | None = None
    deadline_at: datetime | None = None
    execution_mode: ToolExecutionMode = ToolExecutionMode.LIVE
    isolated_write_environment: bool = False

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_content(self) -> AgentRuntimeRequest:
        if self.text is None and self.image_bytes is None:
            raise ValueError("Agent request requires text or an image")
        if self.image_mime_type is not None and self.image_bytes is None:
            raise ValueError("Image MIME type requires image bytes")
        return self


@dataclass(frozen=True, slots=True)
class AgentRuntimeResult:
    thread_id: str
    turn_id: str
    agent_version_id: str
    termination: HarnessTermination
    final_text: str | None
    failure_code: str | None


class AgentScheduledRequest(BaseModel):
    """Trusted command for a timer-triggered, input-free Agent Turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    trigger: TurnTrigger
    deadline_at: datetime | None = None
    execution_mode: ToolExecutionMode = ToolExecutionMode.LIVE

    @model_validator(mode="after")
    def validate_trigger(self) -> AgentScheduledRequest:
        if self.trigger not in {
            TurnTrigger.WEIGHT_REMINDER,
            TurnTrigger.MEAL_REMINDER,
            TurnTrigger.DAILY_REVIEW,
        }:
            raise ValueError("Scheduled Agent request has an unsupported trigger")
        if self.deadline_at is not None and self.deadline_at.utcoffset() is None:
            raise ValueError("Scheduled Agent deadline must be timezone-aware")
        return self


class AgentRuntimeProtocol(Protocol):
    async def run_user_message(
        self,
        request: AgentRuntimeRequest,
    ) -> AgentRuntimeResult: ...

    async def run_scheduled(
        self,
        request: AgentScheduledRequest,
    ) -> AgentRuntimeResult: ...


class AgentRuntime:
    """Stable application entry point over the internal Agent Harness graph."""

    def __init__(
        self,
        *,
        manifest: AgentManifest,
        versions: AgentVersionRepository,
        runner: HarnessTurnRunner,
        assets: ImageAssetRepository,
        image_retention: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if image_retention <= timedelta(0):
            raise ValueError("Image retention must be positive")
        self.manifest = manifest
        self._versions = versions
        self._runner = runner
        self._assets = assets
        self._image_retention = image_retention
        self._clock = clock or self._utc_now

    async def run_user_message(
        self,
        request: AgentRuntimeRequest,
    ) -> AgentRuntimeResult:
        await self._versions.register(self.manifest)
        inputs: list[TurnInput] = []
        if request.text is not None:
            inputs.append(
                TurnInput.user_message(
                    text=request.text,
                    source_message_id=request.source_message_id,
                    channel_id=request.channel_id,
                    occurred_at=request.occurred_at,
                )
            )
        if request.image_bytes is not None:
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("Agent Runtime clock must be timezone-aware")
            creation = await self._assets.save(
                SaveImageAssetCommand(
                    user_id=request.user_id,
                    content=request.image_bytes,
                    declared_mime_type=request.image_mime_type,
                    channel_id=request.channel_id,
                    source_message_id=request.source_message_id,
                    expires_at=now + self._image_retention,
                )
            )
            inputs.append(
                TurnInput.image_attachment(
                    asset_id=creation.asset.id,
                    mime_type=creation.asset.mime_type,
                    source_message_id=request.source_message_id,
                    channel_id=request.channel_id,
                    occurred_at=request.occurred_at,
                )
            )
        run = await self._runner.run(
            request=TurnInitializationRequest(
                user_id=request.user_id,
                agent_version_id=self.manifest.version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                execution_mode=request.execution_mode,
                deadline_at=request.deadline_at,
                inputs=tuple(inputs),
            ),
            grants=HarnessTurnGrants(
                isolated_write_environment=request.isolated_write_environment,
            ),
        )
        return AgentRuntimeResult(
            thread_id=run.initialized.thread.id,
            turn_id=run.initialized.turn.id,
            agent_version_id=run.initialized.turn.agent_version_id,
            termination=run.loop.termination,
            final_text=run.final_text,
            failure_code=run.loop.failure.code if run.loop.failure is not None else None,
        )

    async def run_scheduled(
        self,
        request: AgentScheduledRequest,
    ) -> AgentRuntimeResult:
        await self._versions.register(self.manifest)
        run = await self._runner.run(
            request=TurnInitializationRequest(
                user_id=request.user_id,
                agent_version_id=self.manifest.version_id,
                trigger=request.trigger,
                execution_mode=request.execution_mode,
                deadline_at=request.deadline_at,
                inputs=(),
            ),
            grants=HarnessTurnGrants(allowed_tool_names=()),
        )
        return AgentRuntimeResult(
            thread_id=run.initialized.thread.id,
            turn_id=run.initialized.turn.id,
            agent_version_id=run.initialized.turn.agent_version_id,
            termination=run.loop.termination,
            final_text=run.final_text,
            failure_code=run.loop.failure.code if run.loop.failure is not None else None,
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
