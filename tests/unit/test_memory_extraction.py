from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from slim_guard.agent.composition import AgentRuntimeDefinition, build_agent_runtime
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import MemoryExtractionJobRecord, SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.memory.extraction import (
    MemoryExtractionJobRepository,
    MemoryExtractionService,
)
from slim_guard.memory.long_term import LongTermMemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode

NOW = datetime(2026, 9, 17, 18, 0, tzinfo=UTC)


def _final(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


class ExtractionGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        payload = json.loads(request.messages[-1].content or "{}")
        source = payload["current_user_message"]
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    NormalizedToolCall(
                        id="proposal-1",
                        name="propose_long_term_memories",
                        arguments={
                            "memories": [
                                {
                                    "content_text": "用户周日通常和家人聚餐。",
                                    "category": "social_routine",
                                    "durability": "long_term",
                                    "expires_at": None,
                                    "sensitivity": "normal",
                                    "evidence_ref": source["evidence_ref"],
                                    "evidence_excerpt": "周日一般会和家人聚餐",
                                    "operation": "create",
                                    "supersedes_memory_id": None,
                                }
                            ]
                        },
                    ),
                ),
            ),
            finish_reason="tool_calls",
        )

    async def close(self) -> None:
        return None


async def test_completed_reply_queues_then_asynchronously_extracts_text_memory(
    tmp_path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'extraction.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    conversation = ScriptedModelGateway((_final("好，聚餐时我们再具体安排。"),))
    extraction = ExtractionGateway()
    runtime = build_agent_runtime(
        database=database,
        model=conversation,
        memory_extraction_model=extraction,
        definition=AgentRuntimeDefinition(
            model_provider="test",
            text_model="test",
            vision_model="test",
            code_revision="post-turn-memory-test",
        ),
        clock=lambda: NOW,
    )
    try:
        result = await runtime.run_user_message(
            AgentRuntimeRequest(
                user_id="user-1",
                text="周日一般会和家人聚餐",
                execution_mode=ToolExecutionMode.EVALUATION,
                isolated_write_environment=True,
            )
        )
        assert result.final_text == "好，聚餐时我们再具体安排。"
        assert extraction.requests == []
        async with database.session() as session:
            jobs = tuple(await session.scalars(select(MemoryExtractionJobRecord)))
        assert len(jobs) == 1
        assert jobs[0].status == "queued"

        service = MemoryExtractionService(
            jobs=MemoryExtractionJobRepository(database, clock=lambda: NOW),
            memories=LongTermMemoryRepository(database, clock=lambda: NOW),
            model=extraction,
            model_name="test",
            clock=lambda: NOW,
        )
        assert await service.process_once() == 1
        active = await LongTermMemoryRepository(
            database,
            clock=lambda: NOW,
        ).active("user-1")

        assert [memory.content_text for memory in active] == ["用户周日通常和家人聚餐。"]
        assert active[0].category == "social_routine"
        assert len(extraction.requests) == 1
        request_payload = json.loads(extraction.requests[0].messages[-1].content or "{}")
        assert request_payload["final_response"] == "好，聚餐时我们再具体安排。"
        assert "身高省略单位" not in (extraction.requests[0].messages[0].content or "")
        async with database.session() as session:
            completed = await session.get(MemoryExtractionJobRecord, jobs[0].id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.extracted_count == 1
    finally:
        await conversation.close()
        await extraction.close()
        await database.close()
