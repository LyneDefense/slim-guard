from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.harness.errors import PendingActionConfigurationError, TurnNotWritable
from slim_guard.harness.events import (
    PendingActionStatus,
    PendingActionType,
    TurnStatus,
)
from slim_guard.harness.pending_actions import PendingActionRef, PendingActionStore
from slim_guard.harness.state_repository import TurnRef, TurnStateStore
from slim_guard.harness.tool_calls import ToolCallCoordinator, ToolCallOutcome
from slim_guard.tools.contracts import ToolContext, ToolPolicyDecision
from slim_guard.tools.policy import ToolAuthorization

_GRANT_STATUSES = frozenset(
    {PendingActionStatus.APPROVED, PendingActionStatus.CONSUMED}
)


@dataclass(frozen=True, slots=True)
class PendingResumeOutcome:
    action: PendingActionRef
    turn: TurnRef
    tool_outcome: ToolCallOutcome | None


class PendingActionResumeCoordinator:
    """Resolves a frozen action and safely resumes its original tool call."""

    def __init__(
        self,
        *,
        pending_actions: PendingActionStore,
        turn_state: TurnStateStore,
        tool_calls: ToolCallCoordinator,
    ) -> None:
        self._pending_actions = pending_actions
        self._turn_state = turn_state
        self._tool_calls = tool_calls

    async def resolve(
        self,
        *,
        action_id: str,
        resolution: PendingActionStatus,
        resolved_by: str,
        now: datetime,
    ) -> PendingResumeOutcome:
        action = await self._pending_actions.get(action_id)
        if action is None:
            raise LookupError(f"Pending action not found: {action_id}")
        expected_waiting = self._waiting_status(action.action_type)
        turn = await self._turn_state.get_turn(action.turn_id)
        if turn is None:
            raise LookupError(f"Agent turn not found: {action.turn_id}")
        if turn.thread_id != action.thread_id:
            raise PendingActionConfigurationError(
                "Pending action thread does not match its persisted turn"
            )
        if (
            action.status is PendingActionStatus.PENDING
            and turn.status is not expected_waiting
        ):
            raise TurnNotWritable(
                f"Cannot resolve action {action.id} while turn is {turn.status.value}"
            )

        resolved = await self._pending_actions.resolve(
            action_id=action.id,
            resolution=resolution,
            resolved_by=resolved_by,
            resolved_at=now,
        )
        if resolved.status is PendingActionStatus.CONSUMED:
            if turn.status is not TurnStatus.RUNNING:
                raise TurnNotWritable(
                    f"Consumed action {action.id} has turn state {turn.status.value}"
                )
            return PendingResumeOutcome(action=resolved, turn=turn, tool_outcome=None)
        if resolved.status is not PendingActionStatus.APPROVED:
            running_turn = await self._ensure_running(turn, expected_waiting=expected_waiting)
            return PendingResumeOutcome(
                action=resolved,
                turn=running_turn,
                tool_outcome=None,
            )

        if turn.status not in {expected_waiting, TurnStatus.RUNNING}:
            related_actions = await self._pending_actions.list_for_execution(
                resolved.execution_key
            )
            if self._has_pending_gate_for_turn(related_actions, turn.status):
                return PendingResumeOutcome(
                    action=resolved,
                    turn=turn,
                    tool_outcome=None,
                )
            raise TurnNotWritable(
                f"Cannot resume approved action {action.id} while turn is {turn.status.value}"
            )
        running_turn = await self._ensure_running(turn, expected_waiting=expected_waiting)

        thread = await self._turn_state.get_thread(turn.thread_id)
        if thread is None:
            raise LookupError(f"Agent thread not found: {turn.thread_id}")
        related_actions = await self._pending_actions.list_for_execution(
            resolved.execution_key
        )
        authorization = self._authorization(resolved, related_actions)
        tool_outcome = await self._tool_calls.execute(
            call=NormalizedToolCall(
                id=resolved.tool_call_id,
                name=resolved.tool_name,
                arguments=resolved.canonical_arguments,
            ),
            context=ToolContext(
                thread_id=resolved.thread_id,
                turn_id=running_turn.id,
                tool_call_id=resolved.tool_call_id,
                user_id=thread.user_id,
                agent_version_id=running_turn.agent_version_id,
                execution_mode=resolved.execution_mode,
            ),
            authorization=authorization,
            source_item_id=resolved.source_item_id,
            now=now,
        )
        if tool_outcome.execution.policy_decision is ToolPolicyDecision.ALLOW:
            related_actions = await self._consume_approved(related_actions, now=now)
            resolved = next(item for item in related_actions if item.id == resolved.id)
        return PendingResumeOutcome(
            action=resolved,
            turn=tool_outcome.turn,
            tool_outcome=tool_outcome,
        )

    @staticmethod
    def _waiting_status(action_type: PendingActionType) -> TurnStatus:
        if action_type is PendingActionType.USER_CONFIRMATION:
            return TurnStatus.WAITING_USER_CONFIRMATION
        return TurnStatus.WAITING_HUMAN_REVIEW

    async def _ensure_running(
        self,
        turn: TurnRef,
        *,
        expected_waiting: TurnStatus,
    ) -> TurnRef:
        if turn.status is TurnStatus.RUNNING:
            return turn
        if turn.status is not expected_waiting:
            raise TurnNotWritable(
                f"Cannot resume turn {turn.id} from {turn.status.value}"
            )
        return await self._turn_state.transition_turn(
            turn_id=turn.id,
            target=TurnStatus.RUNNING,
            expected=expected_waiting,
        )

    @classmethod
    def _has_pending_gate_for_turn(
        cls,
        actions: list[PendingActionRef],
        turn_status: TurnStatus,
    ) -> bool:
        return any(
            action.status is PendingActionStatus.PENDING
            and cls._waiting_status(action.action_type) is turn_status
            for action in actions
        )

    @staticmethod
    def _authorization(
        action: PendingActionRef,
        related_actions: list[PendingActionRef],
    ) -> ToolAuthorization:
        confirmed = frozenset(
            item.execution_key
            for item in related_actions
            if item.action_type is PendingActionType.USER_CONFIRMATION
            and item.status in _GRANT_STATUSES
        )
        reviewed = frozenset(
            item.execution_key
            for item in related_actions
            if item.action_type is PendingActionType.HUMAN_REVIEW
            and item.status in _GRANT_STATUSES
        )
        return ToolAuthorization(
            allowed_tool_names=frozenset({action.tool_name}),
            confirmed_execution_keys=confirmed,
            reviewed_execution_keys=reviewed,
            isolated_write_environment=action.isolated_write_environment,
        )

    async def _consume_approved(
        self,
        actions: list[PendingActionRef],
        *,
        now: datetime,
    ) -> list[PendingActionRef]:
        consumed: list[PendingActionRef] = []
        for action in actions:
            if action.status is PendingActionStatus.APPROVED:
                action = await self._pending_actions.consume(
                    action_id=action.id,
                    consumed_at=now,
                )
            consumed.append(action)
        return consumed
