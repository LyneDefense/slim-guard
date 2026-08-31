from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from slim_guard.db.models import AgentItemRecord
from slim_guard.domain.source import validate_record_source
from slim_guard.harness.events import (
    ItemStatus,
    ItemType,
    PendingActionStatus,
    PendingActionType,
    TurnStatus,
)
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

if TYPE_CHECKING:
    from slim_guard.harness.pending_actions import PendingActionRepository
    from slim_guard.harness.pending_resume import PendingActionResumeCoordinator

RESOLVE_PENDING_USER_ACTION_TOOL_NAME = "resolve_pending_user_action"
PENDING_ACTION_TOOL_VERSION = "v1"


class ResolvePendingUserActionArguments(ToolArguments):
    action_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]
    evidence_excerpt: str = Field(min_length=1, max_length=512)


class PendingActionToolHandlers:
    def __init__(
        self,
        *,
        pending_actions: PendingActionRepository,
        state: HarnessStateRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pending_actions = pending_actions
        self._state = state
        self._clock = clock or self._utc_now
        self._resume: PendingActionResumeCoordinator | None = None

    def bind(self, resume: PendingActionResumeCoordinator) -> None:
        if self._resume is not None:
            raise RuntimeError("Pending action resolver is already bound")
        self._resume = resume

    async def resolve_pending_user_action(
        self,
        context: ToolContext,
        arguments: ResolvePendingUserActionArguments,
    ) -> ToolResult:
        if self._resume is None:
            return ToolResult.failed(
                code="pending_action_resolver_unavailable",
                message="Pending action resolution is unavailable.",
            )
        if context.source_item_id is None:
            return ToolResult.failed(
                code="missing_confirmation_source",
                message="Resolving an action requires a current user message.",
            )
        if not await self._valid_current_evidence(
            context=context,
            evidence_excerpt=arguments.evidence_excerpt,
        ):
            return ToolResult.failed(
                code="confirmation_evidence_mismatch",
                message="The decision must be grounded in the current user message.",
            )
        action = await self._pending_actions.get(arguments.action_id)
        if action is None or action.thread_id != context.thread_id:
            return ToolResult.failed(
                code="pending_action_not_found",
                message="That pending action is not visible to the current user.",
            )
        thread = await self._state.get_thread(action.thread_id)
        if thread is None or thread.user_id != context.user_id:
            return ToolResult.failed(
                code="pending_action_not_found",
                message="That pending action is not visible to the current user.",
            )
        if action.action_type is not PendingActionType.USER_CONFIRMATION:
            return ToolResult.failed(
                code="pending_action_not_user_confirmable",
                message="That pending action cannot be resolved by the current user.",
            )
        wanted = (
            PendingActionStatus.APPROVED
            if arguments.decision == "approve"
            else PendingActionStatus.REJECTED
        )
        if action.status in {
            PendingActionStatus.CONSUMED,
            PendingActionStatus.REJECTED,
            PendingActionStatus.CANCELLED,
        }:
            return ToolResult.success(
                output={
                    "action_id": action.id,
                    "status": action.status.value,
                    "changed": False,
                },
                source_ids=(action.id,),
            )
        now = self._clock()
        if now.utcoffset() is None:
            return ToolResult.failed(
                code="invalid_confirmation_clock",
                message="Pending action resolution time is invalid.",
            )
        outcome = await self._resume.resolve(
            action_id=action.id,
            resolution=wanted,
            resolved_by=context.user_id,
            now=now.astimezone(UTC),
        )
        if outcome.turn.id != context.turn_id and outcome.turn.status is TurnStatus.RUNNING:
            await self._state.append_item(
                turn_id=outcome.turn.id,
                item_type=ItemType.APPROVAL_RESULT,
                status=ItemStatus.COMPLETED,
                payload={
                    "action_id": action.id,
                    "decision": arguments.decision,
                    "status": outcome.action.status.value,
                },
            )
            await self._state.transition_turn(
                turn_id=outcome.turn.id,
                target=TurnStatus.COMPLETED,
                expected=TurnStatus.RUNNING,
            )
        nested = outcome.tool_outcome
        if nested is not None and nested.execution.result.failure is not None:
            return ToolResult.failed(
                code="confirmed_action_failed",
                message="The confirmed action could not be completed.",
                retryable=nested.execution.result.failure.retryable,
            )
        return ToolResult.success(
            output={
                "action_id": action.id,
                "status": outcome.action.status.value,
                "changed": True,
                **({"resolved_tool": nested.execution.tool_name} if nested is not None else {}),
            },
            source_ids=(action.id,),
        )

    async def _valid_current_evidence(
        self,
        *,
        context: ToolContext,
        evidence_excerpt: str,
    ) -> bool:
        async with self._state.database.session() as session:
            mismatch = await validate_record_source(
                session,
                user_id=context.user_id,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
            )
            if mismatch is not None:
                return False
            source = await session.get(AgentItemRecord, context.source_item_id)
            if source is None or source.item_type != ItemType.USER_MESSAGE.value:
                return False
            try:
                payload = json.loads(source.payload_json)
            except (TypeError, json.JSONDecodeError):
                return False
            text = payload.get("text")
            return isinstance(text, str) and evidence_excerpt in text

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


def pending_action_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=RESOLVE_PENDING_USER_ACTION_TOOL_NAME,
            description=(
                "Approve or reject exactly one pending user-confirmation action when the "
                "current user clearly confirms or cancels it. action_id must come from "
                "working_memory.pending_user_confirmations and evidence_excerpt must be an "
                "exact current-message excerpt. If the user's intent is ambiguous, ask them "
                "instead of calling this tool."
            ),
            version=PENDING_ACTION_TOOL_VERSION,
            arguments_model=ResolvePendingUserActionArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=10,
        ),
    )


def pending_action_tool_executors(
    handlers: PendingActionToolHandlers,
) -> Mapping[str, ToolExecutor[Any]]:
    return {
        RESOLVE_PENDING_USER_ACTION_TOOL_NAME: ToolExecutor(
            arguments_model=ResolvePendingUserActionArguments,
            handler=handlers.resolve_pending_user_action,
        )
    }
