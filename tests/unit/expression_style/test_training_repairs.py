import json

import pytest

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.expression_style.trainer.contracts import BuildBudget
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.worker import StyleWorker

from .fakes import TrainerGateway


class RepairGateway(TrainerGateway):
    def __init__(self, broken_schema):
        super().__init__()
        self.broken_schema = broken_schema
        self.changed = False

    async def complete(self, request):
        result = await super().complete(request)
        if request.output_schema_name == self.broken_schema and not self.changed:
            self.changed = True
            value = json.loads(result.message.content)
            if self.broken_schema == "TestSuite":
                value["acceptance"][0]["family"] = value["development"][0]["family"]
            else:
                value["example_ids"] = ["不存在的素材"]
            return result.model_copy(
                update={
                    "message": ModelMessage(role="assistant", content=json.dumps(value)),
                }
            )
        return result


@pytest.mark.parametrize("schema", ["TestSuite", "CandidateProposal"])
async def test_structurally_invalid_generated_artifact_is_repaired_with_reason(style_db, schema):
    await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(user_input="谢谢", original_response="不用客气", desired_response="不客气"),
        "admin",
    )
    builds = BuildRepository(style_db)
    run = await builds.build(
        "doctor", "admin", model="test-model", budget=BuildBudget(max_rounds=1)
    )
    gateway = RepairGateway(schema)
    await StyleWorker(style_db, gateway, "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    calls = [r for r in gateway.requests if r.output_schema_name == schema]
    assert len(calls) == 2
    assert json.loads(calls[1].messages[1].content)["repair_issues"]


async def test_negative_only_material_can_build_guide_without_positive_examples(style_db):
    await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(user_input="你好", original_response="输出", correction_opinion="不要套话"),
        "admin",
    )
    builds = BuildRepository(style_db)
    run = await builds.build(
        "doctor", "admin", model="test-model", budget=BuildBudget(max_rounds=1)
    )
    await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    assert detail["report"]["conclusion"] == "需要人工确认"
    package = (await builds.artifact("doctor", run["id"], "selected"))["value"]
    assert package["examples"] == []
    assert package["guide"]["rules"][0]["evidence"]
