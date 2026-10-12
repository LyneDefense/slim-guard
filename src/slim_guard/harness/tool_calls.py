"""Execute tools inside a live Turn; clarification belongs to conversation tasks."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.harness.errors import TurnNotWritable
from slim_guard.harness.events import TurnStatus
from slim_guard.harness.state_repository import TurnRef, TurnStateStore
from slim_guard.tools.contracts import ToolContext, ToolExecution
from slim_guard.tools.gateway import ToolGatewayProtocol
from slim_guard.tools.policy import ToolAuthorization


@dataclass(frozen=True, slots=True)
class ToolCallOutcome:
    execution: ToolExecution
    turn: TurnRef


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
    def __init__(self, *, gateway: ToolGatewayProtocol, turn_state: TurnStateStore) -> None:
        self._gateway = gateway
        self._turn_state = turn_state

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
        if turn.thread_id != context.thread_id or turn.agent_version_id != context.agent_version_id:
            raise TurnNotWritable("Tool context does not match its persisted Turn")
        if turn.status is not TurnStatus.RUNNING:
            raise TurnNotWritable(f"Cannot execute a tool for Turn in {turn.status.value}")
        execution = await self._gateway.execute(
            call=call,
            context=context,
            authorization=authorization,
        )
        return ToolCallOutcome(execution=execution, turn=turn)
