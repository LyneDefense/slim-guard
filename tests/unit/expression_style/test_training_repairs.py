import json

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import Version
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.style_management.worker import StyleWorker

from .fakes import TrainerGateway


async def material(db, **overrides):
    return await CorpusRepository(db).append(
        "doctor",
        ExampleInput(
            user_input="谢谢",
            original_response="不用客气",
            **{"desired_response": "不客气", **overrides},
        ),
        "admin",
    )


async def test_invalid_example_reference_is_repaired_without_generating_tests(style_db):
    class RepairGateway(TrainerGateway):
        changed = False

        async def complete(self, request):
            result = await super().complete(request)
            if request.output_schema_name == "CandidateProposal" and not self.changed:
                self.changed = True
                value = json.loads(result.message.content)
                value["example_ids"] = ["不存在的素材"]
                return result.model_copy(
                    update={
                        "message": ModelMessage(
                            role="assistant",
                            content=json.dumps(value),
                        )
                    }
                )
            return result

    await material(style_db)
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = RepairGateway()
    await StyleWorker(style_db, gateway, "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    calls = [r for r in gateway.requests if r.output_schema_name == "CandidateProposal"]
    assert len(calls) == 2
    assert json.loads(calls[1].messages[1].content)["repair_issues"]
    assert all(r.output_schema_name not in {"TestSuite", "Comparison"} for r in gateway.requests)


async def test_negative_only_material_builds_guide_without_positive_examples(style_db):
    await material(style_db, desired_response="", correction_opinion="不要套话")
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    assert detail["report"]["conclusion"] == "试答完成，请人工确认语气"
    package = (await builds.artifact("doctor", run["id"], "selected"))["value"]
    assert package["examples"] == [] and package["guide"]["rules"][0]["evidence"]


async def test_schema_failure_resume_reuses_analysis_and_retains_diagnostics(style_db):
    class BrokenProposalGateway(TrainerGateway):
        broken = True

        async def complete(self, request):
            result = await super().complete(request)
            if request.output_schema_name == "CandidateProposal" and self.broken:
                return result.model_copy(
                    update={
                        "message": ModelMessage(
                            role="assistant",
                            content='{"example_ids":"bad"}',
                        )
                    }
                )
            return result

    await material(style_db)
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = BrokenProposalGateway()
    worker = StyleWorker(style_db, gateway, "test-model")
    await worker.run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "failed" and detail["can_resume"]
    key = next(k for k in detail["artifacts"] if k.startswith("validation:CandidateProposal:"))
    failure = (await builds.artifact("doctor", run["id"], key))["value"]
    assert len(failure["failures"]) == 3 and failure["status"] == "unresolved"
    async with style_db.session() as session:
        assert await session.get(Version, run["id"]) is None
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
    calls_before = len(gateway.requests)
    gateway.broken = False
    await builds.action("doctor", run["id"], "resume")
    await worker.run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    assert gateway.requests[calls_before].output_schema_name == "CandidateProposal"
    assert "validation_errors" in gateway.requests[calls_before].messages[-1].content
    assert sum(r.output_schema_name == "AnalysisBatch" for r in gateway.requests) == 1
    assert detail["usage"]["calls"] == len(gateway.requests)
    assert detail["usage"]["tokens"] == len(gateway.requests) * 10
    repaired = (await builds.artifact("doctor", run["id"], key))["value"]
    assert repaired["status"] == "repaired" and len(repaired["failures"]) == 3
