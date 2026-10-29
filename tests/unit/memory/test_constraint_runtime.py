"""Actual Harness, source validation, memory persistence and receipt-bound replies."""

import json

from slim_guard.agent.composition import build_agent_runtime
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.runtime import AgentRuntimeRequest
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.memory.contracts import MemoryKey
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolExecutionMode
from tests.conversation_script import SystemConversationScript
from tests.unit.test_agent_runtime import FIXED_NOW, final_reply, prepare_database, tool_call


class GroundingScript(ScriptedModelGateway):
    async def complete(self, request):
        if request.output_schema_name != "GroundingDecision":
            return await super().complete(request)
        self.requests.append(request)
        data = json.loads(request.messages[-1].content)
        existing = data["existing"]
        return final_reply(
            json.dumps(
                {
                    "verdict": "supported",
                    "topic_relation": "same" if existing else "new",
                    "matched_memory_id": existing[0]["id"] if existing else None,
                    "subject": "辛辣食物",
                    "reason_code": "faithful_user_constraint",
                }
            )
        )


def profile_claims(payload):
    return [
        {
            "quote": payload["candidate_text"],
            "mutation_kind": "user_profile",
            "status": "succeeded",
            "operation_ids": [r["operation_id"] for r in payload["receipts"]],
        }
    ]


async def test_normalized_constraint_and_synonym_update_survive_the_full_runtime(tmp_path):
    database = await prepare_database(tmp_path)
    quote = "我不能吃太辣的"
    allergy = "我对西兰花有点过敏"
    follow_up = "口味上我还是不太能吃辣"
    model = GroundingScript(
        [
            tool_call(
                "spice-1",
                "record_user_constraint",
                {
                    "category": "dietary",
                    "subject": "太辣的食物",
                    "statement": quote,
                    "evidence_excerpt": quote,
                },
            ),
            tool_call(
                "allergy-1",
                "record_user_constraint",
                {
                    "category": "health_context",
                    "subject": "西兰花",
                    "statement": allergy,
                    "evidence_excerpt": allergy,
                },
            ),
            final_reply("已记录你的口味限制和西兰花过敏自述。"),
            tool_call(
                "spice-2",
                "record_user_constraint",
                {
                    "category": "dietary",
                    "subject": "辣",
                    "statement": follow_up,
                    "evidence_excerpt": follow_up,
                },
            ),
            final_reply("已记录口味偏好，会留意辣度。"),
        ]
    )
    wrapper = SystemConversationScript(model, mutation_claims=profile_claims)
    runtime = build_agent_runtime(
        database=database,
        model=wrapper,
        definition=AgentRuntimeDefinition(
            models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
            code_revision="constraint-integration",
        ),
        clock=lambda: FIXED_NOW,
    )
    try:
        results = []
        for text in [f"我观察了几天，{quote}，以及{allergy}", follow_up]:
            results.append(
                await runtime.run_user_message(
                    AgentRuntimeRequest(
                        user_id="user-1",
                        text=text,
                        execution_mode=ToolExecutionMode.EVALUATION,
                        isolated_write_environment=True,
                    )
                )
            )
        assert [r.final_text for r in results] == [
            "已记录你的口味限制和西兰花过敏自述。",
            "已记录口味偏好，会留意辣度。",
        ]
        repo = MemoryRepository(database)
        dietary = await repo.active("user-1", key=MemoryKey.DIETARY_CONSTRAINT)
        health = await repo.active("user-1", key=MemoryKey.HEALTH_CONTEXT)
        assert len(dietary) == len(health) == 1
        assert dietary[0].value == {"subject": "辛辣食物", "statement": follow_up}
        assert health[0].value == {"subject": "西兰花", "statement": allergy}
        assert dietary[0].evidence_item_id != health[0].evidence_item_id
        assert len([r for r in model.requests if r.output_schema_name == "GroundingDecision"]) == 2
        assert len(wrapper.review_requests) == 2
        model.assert_exhausted()
    finally:
        await database.close()
