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
from slim_guard.memory.long_term import (
    LongTermMemoryDurability,
    LongTermMemoryRef,
    LongTermMemorySensitivity,
)
from slim_guard.memory.recall import LongTermMemoryRecaller
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
                            "reason_summary": "当前问题与家庭聚餐习惯直接相关。",
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

    async def search(self, *, user_id: str, query: str, limit: int) -> tuple[SemanticMemory, ...]:
        self.searches.append((user_id, query, limit))
        return (
            SemanticMemory(
                id="remote-family",
                text="用户周日通常和家人聚餐。",
                metadata={
                    "slim_guard_memory_id": "family",
                    "memory_type": "conversational_long_term",
                },
                score=0.93,
            ),
            # Old structured projections in the same Mem0 deployment are ignored.
            SemanticMemory(
                id="remote-height",
                text="身高 179cm",
                metadata={"slim_guard_memory_id": "height"},
                score=0.99,
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


class MemoryStore:
    def __init__(self, memories: tuple[LongTermMemoryRef, ...]) -> None:
        self.memories = memories
        self.calls: list[tuple[str, tuple[str, ...] | None, int]] = []

    async def active(
        self,
        user_id: str,
        *,
        memory_ids=None,
        limit: int = 100,
    ) -> tuple[LongTermMemoryRef, ...]:
        ids = tuple(memory_ids) if memory_ids is not None else None
        self.calls.append((user_id, ids, limit))
        selected = (
            tuple(memory for memory in self.memories if memory.id in set(ids))
            if ids is not None
            else self.memories
        )
        return selected[:limit]


def initialized(text: str = "周日聚餐怎么安排？") -> InitializedTurn:
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


def memory(memory_id: str, text: str) -> LongTermMemoryRef:
    return LongTermMemoryRef(
        id=memory_id,
        user_id="user-a",
        content_text=text,
        category="other",
        durability=LongTermMemoryDurability.LONG_TERM,
        status="active",
        sensitivity=LongTermMemorySensitivity.NORMAL,
        supersedes_id=None,
        source_turn_id="source-turn",
        source_item_id="source-item",
        valid_from=NOW,
        expires_at=None,
        review_after=None,
        created_at=NOW,
        ended_at=None,
    )


async def test_semantic_ids_are_reloaded_from_authority_before_model_selection() -> None:
    model = RecallGateway(("family", "invented-id"))
    engine = ScopedEngine()
    store = MemoryStore(
        (
            memory("family", "用户周日通常和家人聚餐。"),
            memory("evening", "用户觉得晚上比白天更难控制饮食。"),
        )
    )
    recaller = LongTermMemoryRecaller(
        model=model,
        model_name="glm-test",
        recorder=NullHarnessRunRecorder(),
        memories=store,  # type: ignore[arg-type]
        engine=engine,
    )

    result = await recaller.recall(
        initialized=initialized(),
        current_time=NOW,
        context={"profile": {"nickname": "小胡"}},
    )

    assert result.context["profile"] == {"nickname": "小胡"}
    assert result.context["long_term_memory"] == [
        {
            "memory_id": "family",
            "content_text": "用户周日通常和家人聚餐。",
            "category": "other",
            "sensitivity": "normal",
            "source_turn_id": "source-turn",
            "expires_at": None,
        }
    ]
    assert result.selected_count == 1
    assert result.engine_candidate_count == 2
    assert engine.searches == [("user-a", "周日聚餐怎么安排？", 12)]
    assert store.calls == [("user-a", ("family",), 12)]
    assert model.requests[0].purpose.value == "memory_recall"


async def test_recall_model_failure_does_not_inject_unfiltered_private_context() -> None:
    store = MemoryStore(
        (
            memory("family", "用户周日通常和家人聚餐。"),
            memory("evening", "用户觉得晚上比白天更难控制饮食。"),
        )
    )
    recaller = LongTermMemoryRecaller(
        model=FailingGateway(),
        model_name="glm-test",
        recorder=NullHarnessRunRecorder(),
        memories=store,  # type: ignore[arg-type]
    )

    result = await recaller.recall(
        initialized=initialized(),
        current_time=NOW,
        context={"profile": {"nickname": "小胡"}},
    )

    assert result.degraded is True
    assert result.selected_count == 0
    assert "long_term_memory" not in result.context
    assert result.context["profile"] == {"nickname": "小胡"}
