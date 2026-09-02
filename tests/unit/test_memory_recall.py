from __future__ import annotations

from datetime import UTC, datetime

from slim_guard.agent_models.errors import ModelTimeoutError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.harness.events import ItemStatus, ItemType, ThreadStatus, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.loop import HarnessTurnContext
from slim_guard.harness.state_repository import ItemRef, ThreadRef, TurnRef
from slim_guard.harness.trace import NullHarnessRunRecorder
from slim_guard.memory.engine import SemanticMemory
from slim_guard.memory.recall import ModelFirstMemoryRecaller
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class RecallGateway:
    def __init__(self, selected: tuple[str, ...]) -> None:
        self.selected = selected
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    NormalizedToolCall(
                        id="recall-1",
                        name="select_relevant_memories",
                        arguments={
                            "selected_memory_ids": list(self.selected),
                            "reason_summary": "用户正在询问身高，只需要身高资料。",
                        },
                    ),
                ),
            ),
            finish_reason="tool_calls",
        )

    async def close(self) -> None:
        return None


class FailingGateway:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelTimeoutError("timeout")

    async def close(self) -> None:
        return None


class ScopedEngine:
    provider_name = "test-semantic"

    def __init__(self) -> None:
        self.searches: list[tuple[str, str, int]] = []

    async def search(
        self, *, user_id: str, query: str, limit: int
    ) -> tuple[SemanticMemory, ...]:
        self.searches.append((user_id, query, limit))
        return (
            SemanticMemory(
                id="remote-height",
                text="身高 179cm",
                metadata={"slim_guard_memory_id": "height"},
                score=0.93,
            ),
        )

    async def upsert_canonical(self, **kwargs: object) -> None:
        del kwargs

    async def delete_canonical(self, **kwargs: object) -> None:
        del kwargs

    async def delete_user(self, **kwargs: object) -> None:
        del kwargs

    async def close(self) -> None:
        return None


def initialized(text: str = "帮我保存身高") -> InitializedTurn:
    thread = ThreadRef(id="thread-1", user_id="user-a", status=ThreadStatus.ACTIVE)
    turn = TurnRef(
        id="turn-1",
        thread_id=thread.id,
        agent_version_id="version-1",
        trigger=TurnTrigger.USER_MESSAGE,
        status=TurnStatus.RUNNING,
        deadline_at=None,
        completed_at=None,
    )
    item = ItemRef(
        id="item-1",
        turn_id=turn.id,
        sequence=1,
        item_type=ItemType.USER_MESSAGE,
        status=ItemStatus.COMPLETED,
        payload={"text": text},
    )
    return InitializedTurn(
        thread=thread,
        turn=turn,
        input_items=(item,),
        context=HarnessTurnContext(
            thread_id=thread.id,
            turn_id=turn.id,
            user_id=thread.user_id,
            agent_version_id=turn.agent_version_id,
            execution_mode=ToolExecutionMode.LIVE,
        ),
        source_item_id=item.id,
    )


def candidate(memory_id: str, key: str, value: object) -> dict[str, object]:
    return {
        "memory_id": memory_id,
        "kind": "profile",
        "key": key,
        "value": value,
        "sensitivity": "health",
        "stale": False,
    }


async def test_model_selects_only_relevant_memory_and_engine_is_user_scoped() -> None:
    model = RecallGateway(("height", "invented-id"))
    engine = ScopedEngine()
    recaller = ModelFirstMemoryRecaller(
        model=model,
        model_name="glm-test",
        recorder=NullHarnessRunRecorder(),
        engine=engine,
    )
    context = {
        "profile_memory": [
            candidate("height", "profile.height", {"millimeters": 1790}),
            candidate("food", "food.preference", {"item": "香菜"}),
        ],
        "working_memory": {"recent_dialogue": []},
    }

    result = await recaller.recall(
        initialized=initialized(),
        current_time=NOW,
        context=context,
    )

    assert [row["memory_id"] for row in result.context["profile_memory"]] == ["height"]
    assert result.selected_count == 1
    assert result.engine_candidate_count == 1
    assert engine.searches == [("user-a", "帮我保存身高", 12)]
    assert model.requests[0].purpose.value == "memory_recall"


async def test_recall_model_failure_conservatively_keeps_bounded_database_facts() -> None:
    recaller = ModelFirstMemoryRecaller(
        model=FailingGateway(),
        model_name="glm-test",
        recorder=NullHarnessRunRecorder(),
    )
    memories = [
        candidate("height", "profile.height", {"millimeters": 1790}),
        candidate("goal", "goal.target_weight", {"grams": 74000}),
    ]

    result = await recaller.recall(
        initialized=initialized(),
        current_time=NOW,
        context={"profile_memory": memories},
    )

    assert result.degraded is True
    assert result.context["profile_memory"] == memories
    assert result.selected_count == 2
