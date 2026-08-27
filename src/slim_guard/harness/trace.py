from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from slim_guard.agent_models.gateway import ModelRequest, ModelResponse, NormalizedToolCall
from slim_guard.harness.errors import TurnStateConflict
from slim_guard.harness.events import ItemStatus, ItemType, TurnStatus
from slim_guard.harness.failures import HarnessFailure
from slim_guard.harness.state_repository import HarnessRunStore
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome


@dataclass(frozen=True, slots=True)
class ToolTrace:
    result_item_id: str | None


class HarnessRunRecorder(Protocol):
    async def record_context_snapshot(
        self,
        *,
        turn_id: str,
        payload: Mapping[str, Any],
    ) -> None: ...

    async def record_model_response(
        self,
        *,
        turn_id: str,
        request: ModelRequest,
        response: ModelResponse,
        call_index: int,
    ) -> None: ...

    async def start_tool_call(
        self,
        *,
        turn_id: str,
        call: NormalizedToolCall,
        call_index: int,
    ) -> ToolTrace: ...

    async def finish_tool_call(
        self,
        *,
        trace: ToolTrace,
        outcome: ToolCallOutcome,
    ) -> None: ...

    async def finish_run(
        self,
        *,
        turn_id: str,
        termination: HarnessTermination,
        final_text: str | None,
        model_call_count: int,
        tool_call_count: int,
        total_token_count: int,
        failure: HarnessFailure | None,
    ) -> None: ...


class NullHarnessRunRecorder:
    """Keeps the core loop usable in deterministic unit tests without persistence."""

    async def record_context_snapshot(
        self,
        *,
        turn_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        return None

    async def record_model_response(
        self,
        *,
        turn_id: str,
        request: ModelRequest,
        response: ModelResponse,
        call_index: int,
    ) -> None:
        return None

    async def start_tool_call(
        self,
        *,
        turn_id: str,
        call: NormalizedToolCall,
        call_index: int,
    ) -> ToolTrace:
        return ToolTrace(result_item_id=None)

    async def finish_tool_call(
        self,
        *,
        trace: ToolTrace,
        outcome: ToolCallOutcome,
    ) -> None:
        return None

    async def finish_run(
        self,
        *,
        turn_id: str,
        termination: HarnessTermination,
        final_text: str | None,
        model_call_count: int,
        tool_call_count: int,
        total_token_count: int,
        failure: HarnessFailure | None,
    ) -> None:
        return None


class PersistentHarnessRunRecorder:
    """Persists a reconstructable trace and owns normal Turn termination."""

    def __init__(self, store: HarnessRunStore) -> None:
        self._store = store

    async def record_context_snapshot(
        self,
        *,
        turn_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        await self._store.append_item(
            turn_id=turn_id,
            item_type=ItemType.CONTEXT_SNAPSHOT,
            status=ItemStatus.COMPLETED,
            payload=payload,
        )

    async def record_model_response(
        self,
        *,
        turn_id: str,
        request: ModelRequest,
        response: ModelResponse,
        call_index: int,
    ) -> None:
        await self._store.append_item(
            turn_id=turn_id,
            item_type=ItemType.MODEL_MESSAGE,
            status=ItemStatus.COMPLETED,
            payload={
                "call_index": call_index,
                "model": request.model,
                "purpose": request.purpose.value,
                "message": response.message.model_dump(mode="json"),
                "finish_reason": response.finish_reason,
                "usage": response.usage.model_dump(mode="json"),
                "provider_request_id": response.provider_request_id,
            },
        )

    async def start_tool_call(
        self,
        *,
        turn_id: str,
        call: NormalizedToolCall,
        call_index: int,
    ) -> ToolTrace:
        await self._store.append_item(
            turn_id=turn_id,
            item_type=ItemType.TOOL_CALL,
            status=ItemStatus.COMPLETED,
            payload={
                "call_index": call_index,
                "tool_call_id": call.id,
                "tool_name": call.name,
                "arguments": call.arguments,
            },
        )
        result_item = await self._store.append_item(
            turn_id=turn_id,
            item_type=ItemType.TOOL_RESULT,
            status=ItemStatus.STARTED,
            payload={
                "call_index": call_index,
                "tool_call_id": call.id,
                "tool_name": call.name,
            },
        )
        return ToolTrace(result_item_id=result_item.id)

    async def finish_tool_call(
        self,
        *,
        trace: ToolTrace,
        outcome: ToolCallOutcome,
    ) -> None:
        if trace.result_item_id is None:
            return
        await self._store.finish_item(
            item_id=trace.result_item_id,
            status=ItemStatus.COMPLETED,
            payload={
                "tool_call_id": outcome.execution.tool_call_id,
                "tool_name": outcome.execution.tool_name,
                "execution": outcome.execution.model_dump(mode="json"),
                "pending_action_id": (
                    outcome.pending_action.id if outcome.pending_action is not None else None
                ),
            },
        )

    async def finish_run(
        self,
        *,
        turn_id: str,
        termination: HarnessTermination,
        final_text: str | None,
        model_call_count: int,
        tool_call_count: int,
        total_token_count: int,
        failure: HarnessFailure | None,
    ) -> None:
        if termination is HarnessTermination.FINAL_RESPONSE:
            if final_text is None:
                raise ValueError("Final response termination requires final text")
            await self._store.append_item(
                turn_id=turn_id,
                item_type=ItemType.AGENT_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"text": final_text},
            )
            await self._store.transition_turn(
                turn_id=turn_id,
                target=TurnStatus.COMPLETED,
                expected=TurnStatus.RUNNING,
            )
            return

        if termination in {
            HarnessTermination.WAITING_USER_CONFIRMATION,
            HarnessTermination.WAITING_HUMAN_REVIEW,
        }:
            turn = await self._store.get_turn(turn_id)
            expected = (
                TurnStatus.WAITING_USER_CONFIRMATION
                if termination is HarnessTermination.WAITING_USER_CONFIRMATION
                else TurnStatus.WAITING_HUMAN_REVIEW
            )
            if turn is None:
                raise LookupError(f"Agent turn not found: {turn_id}")
            if turn.status is not expected:
                raise TurnStateConflict(
                    f"Turn {turn_id} should be {expected.value}, found {turn.status.value}"
                )
            return

        if termination is HarnessTermination.FATAL_ERROR and failure is None:
            raise ValueError("Fatal error termination requires failure details")
        await self._store.append_item(
            turn_id=turn_id,
            item_type=ItemType.ERROR,
            status=ItemStatus.FAILED,
            payload={
                "code": termination.value,
                "model_call_count": model_call_count,
                "tool_call_count": tool_call_count,
                "total_token_count": total_token_count,
                "failure": failure.to_payload() if failure is not None else None,
            },
        )
        await self._store.transition_turn(
            turn_id=turn_id,
            target=(
                TurnStatus.SUSPENDED
                if failure is None or failure.retryable
                else TurnStatus.FAILED
            ),
            expected=TurnStatus.RUNNING,
        )
