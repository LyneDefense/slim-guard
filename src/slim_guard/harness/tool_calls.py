from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.harness.errors import PendingActionConfigurationError, TurnNotWritable
from slim_guard.harness.events import (
    PendingActionStatus,
    PendingActionType,
    TurnStatus,
)
from slim_guard.harness.pending_actions import PendingActionRef, PendingActionStore
from slim_guard.harness.state_repository import TurnRef, TurnStateStore
from slim_guard.tools.contracts import ToolContext, ToolExecution, ToolPolicyDecision
from slim_guard.tools.gateway import ToolGatewayProtocol
from slim_guard.tools.policy import ToolAuthorization


@dataclass(frozen=True, slots=True)
class ToolCallOutcome:
    execution: ToolExecution
    turn: TurnRef
    pending_action: PendingActionRef | None


class ToolCallRunner(Protocol):
    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
    ) -> ToolCallOutcome: ...


class ToolCallCoordinator:
    """Coordinates policy-gated tool calls with durable Harness waiting state."""

    def __init__(
        self,
        *,
        gateway: ToolGatewayProtocol,
        pending_actions: PendingActionStore,
        turn_state: TurnStateStore,
        confirmation_ttl: timedelta,
        review_ttl: timedelta,
    ) -> None:
        if confirmation_ttl <= timedelta(0) or review_ttl <= timedelta(0):
            raise ValueError("Pending action TTLs must be positive")
        self._gateway = gateway
        self._pending_actions = pending_actions
        self._turn_state = turn_state
        self._confirmation_ttl = confirmation_ttl
        self._review_ttl = review_ttl

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
    ) -> ToolCallOutcome:
        turn = await self._turn_state.get_turn(context.turn_id)
        if turn is None:
            raise LookupError(f"Agent turn not found: {context.turn_id}")
        if turn.thread_id != context.thread_id:
            raise PendingActionConfigurationError(
                "Tool context thread does not match its persisted turn"
            )
        if turn.agent_version_id != context.agent_version_id:
            raise PendingActionConfigurationError(
                "Tool context agent version does not match its persisted turn"
            )
        if turn.status is not TurnStatus.RUNNING:
            raise TurnNotWritable(
                f"Cannot execute a tool for turn {turn.id} in state {turn.status.value}"
            )

        execution = await self._gateway.execute(
            call=call,
            context=context,
            authorization=authorization,
        )
        waiting = self._waiting_spec(execution.policy_decision)
        if waiting is None:
            return ToolCallOutcome(execution=execution, turn=turn, pending_action=None)

        action_type, target_status, ttl = waiting
        if (
            execution.idempotency_key is None
            or execution.tool_version is None
            or execution.canonical_arguments is None
            or execution.result.failure is None
        ):
            raise PendingActionConfigurationError(
                "Policy-gated tool execution is missing its frozen command"
            )
        creation = await self._pending_actions.create(
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            source_item_id=source_item_id,
            execution_key=execution.idempotency_key,
            tool_call_id=execution.tool_call_id,
            tool_name=execution.tool_name,
            tool_version=execution.tool_version,
            canonical_arguments=execution.canonical_arguments,
            execution_mode=context.execution_mode,
            isolated_write_environment=authorization.isolated_write_environment,
            action_type=action_type,
            reason=execution.result.failure.message,
            expires_at=now + ttl,
        )
        if creation.action.status is not PendingActionStatus.PENDING:
            raise PendingActionConfigurationError(
                f"Existing pending action is already {creation.action.status.value}"
            )
        waiting_turn = await self._turn_state.transition_turn(
            turn_id=context.turn_id,
            target=target_status,
            expected=TurnStatus.RUNNING,
        )
        return ToolCallOutcome(
            execution=execution,
            turn=waiting_turn,
            pending_action=creation.action,
        )

    def _waiting_spec(
        self,
        decision: ToolPolicyDecision | None,
    ) -> tuple[PendingActionType, TurnStatus, timedelta] | None:
        if decision is ToolPolicyDecision.CONFIRM:
            return (
                PendingActionType.USER_CONFIRMATION,
                TurnStatus.WAITING_USER_CONFIRMATION,
                self._confirmation_ttl,
            )
        if decision is ToolPolicyDecision.REVIEW:
            return (
                PendingActionType.HUMAN_REVIEW,
                TurnStatus.WAITING_HUMAN_REVIEW,
                self._review_ttl,
            )
        return None
