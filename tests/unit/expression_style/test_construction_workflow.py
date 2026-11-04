"""Multi-batch construction, adversarial provenance, and durable recovery regressions."""

import json

import pytest

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.expression_style.package import StylePackage
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import BuildRun, Version
from slim_guard.style_management.runtime import RuntimeStyles

from .fakes import TrainerGateway
from .fakes import make_style_worker as StyleWorker


async def add_materials(db, count, *, excluded=0, negative=0):
    for i in range(count):
        await CorpusRepository(db).append(
            "doctor",
            ExampleInput(
                user_input=f"{'排除' if i < excluded else '对话'}-{i}",
                original_response=f"不用客气，很高兴和你聊第{i}个话题。",
                desired_response="" if excluded <= i < excluded + negative else f"聊第{i}个。",
                correction_opinion="不要多余客套",
            ),
            "admin",
        )


def alter(result, value):
    return result.model_copy(
        update={
            "message": ModelMessage(
                role="assistant",
                content=json.dumps(value, ensure_ascii=False),
            )
        }
    )


class FaultGateway(TrainerGateway):
    def __init__(self):
        super().__init__()
        self.injected = set()

    async def complete(self, request):
        result = await super().complete(request)
        value = json.loads(result.message.content)
        payload = json.loads(request.messages[1].content)
        schema = request.output_schema_name
        if schema == "AnalysisBatch":
            for material, item in zip(payload, value["items"], strict=True):
                if material["user_input"].startswith("排除"):
                    item["role"] = "out_of_scope"
            return alter(result, value)
        if schema == "GuideDraft":
            phase = "merge" if "guides" in payload else "extract"
            if phase not in self.injected:
                self.injected.add(phase)
                if phase == "extract":
                    # This was previously cached and discovered only at final package validation.
                    value["rules"][0]["quote"] = "拼接原回答与期望回答的伪证据"
                else:
                    value["rules"][0]["evidence_refs"] = ["preview:unavailable-case"]
                return alter(result, value)
        if schema == "ExampleSelection" and "selection" not in self.injected:
            self.injected.add("selection")
            value["example_ids"] = ["preview:unavailable-case"]
            return alter(result, value)
        return result


async def test_fifty_material_build_repairs_each_stage_and_never_reintroduces_exclusions(style_db):
    await add_materials(style_db, 50, excluded=10, negative=5)
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = FaultGateway()
    await StyleWorker(style_db, gateway, "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    assert detail["analyzed_count"] == 50
    async with style_db.session() as session:
        snapshot = (await session.get(BuildRun, run["id"])).snapshot
    materials = {m["id"]: m for m in snapshot["materials"]}
    excluded = {m["id"] for m in materials.values() if m["user_input"].startswith("排除")}
    package = StylePackage.model_validate(
        (await builds.artifact("doctor", run["id"], "selected"))["value"]
    )
    for rule in package.guide.rules:
        for evidence in rule.evidence:
            assert evidence.example_id not in excluded
            source = materials[evidence.example_id]
            assert evidence.revision == source["revision"]
            assert any(
                evidence.quote in source[field]
                for field in (
                    "original_response",
                    "desired_response",
                    "correction_opinion",
                )
            )
    assert {e.id for e in package.examples}.isdisjoint(excluded)
    guides = (await builds.artifact("doctor", run["id"], "construction_guides"))["items"]
    assert [g["phase"] for g in guides] == ["extract"] * 3 + ["merge"]
    guide_requests = [r for r in gateway.requests if r.output_schema_name == "GuideDraft"]
    assert len(guide_requests) == 6  # Three extracts, one merge, two immediate repairs.
    for request in guide_requests:
        payload = json.loads(request.messages[1].content)
        assert "feedback" not in payload
        assert not {e["material_ref"] for e in payload["evidence"]} - {
            f"m{i}" for i in range(1, 41)
        }
        assert not any(m["user_input"].startswith("排除") for m in payload.get("materials", []))
    diagnostics = [key for key in detail["artifacts"] if key.startswith("validation:")]
    assert len(diagnostics) == 3
    types = []
    for key in diagnostics:
        value = (await builds.artifact("doctor", run["id"], key))["value"]
        assert value["status"] == "repaired"
        assert value["contract"]["references"]
        types.extend(e["type"] for failure in value["failures"] for e in failure["errors"])
    assert {"extra_forbidden", "unknown_evidence", "unsupported_example"} <= set(types)
    assert detail["usage"]["calls"] == len(gateway.requests)
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"


@pytest.mark.parametrize("phase", ["extract", "merge"])
async def test_exhausted_guide_validation_resumes_only_failed_step(style_db, phase):
    class BrokenGuideGateway(TrainerGateway):
        broken = True

        async def complete(self, request):
            result = await super().complete(request)
            if request.output_schema_name == "GuideDraft" and self.broken:
                payload = json.loads(request.messages[1].content)
                is_target = (
                    "guides" in payload
                    if phase == "merge"
                    else "materials" in payload and len(payload["materials"]) == 1
                )
                if is_target:
                    value = json.loads(result.message.content)
                    value["rules"][0]["evidence_refs"] = ["e99999"]
                    return alter(result, value)
            return result

    await add_materials(style_db, 17)
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = BrokenGuideGateway()
    worker = StyleWorker(style_db, gateway, "test-model")
    await worker.run_once()
    failed = await builds.detail("doctor", run["id"])
    assert failed["status"] == "failed" and failed["can_resume"]
    assert failed["analyzed_count"] == 17
    key = next(k for k in failed["artifacts"] if k.startswith("validation:GuideDraft:"))
    diagnostic = (await builds.artifact("doctor", run["id"], key))["value"]
    assert diagnostic["status"] == "unresolved" and len(diagnostic["failures"]) == 3
    assert diagnostic["failures"][0]["errors"][0]["path"] == "rules.0.evidence_refs.0"
    assert all("@" in value for value in diagnostic["contract"]["references"].values())
    async with style_db.session() as session:
        assert await session.get(Version, run["id"]) is None
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
    before = len(gateway.requests)
    gateway.broken = False
    await builds.action("doctor", run["id"], "resume")
    await worker.run_once()
    completed = await builds.detail("doctor", run["id"])
    assert completed["status"] == "completed", completed["error"]
    new = gateway.requests[before:]
    assert new[0].output_schema_name == "GuideDraft"
    assert "unknown_evidence" in new[0].messages[-1].content
    assert not any(r.output_schema_name == "AnalysisBatch" for r in new)
    assert len([r for r in new if r.output_schema_name == "GuideDraft"]) == (
        1 if phase == "merge" else 2
    )
    assert completed["usage"]["calls"] == len(gateway.requests)
    assert completed["usage"]["seconds"] >= failed["usage"]["seconds"]
    diagnostic = (await builds.artifact("doctor", run["id"], key))["value"]
    assert diagnostic["status"] == "repaired" and len(diagnostic["failures"]) == 3
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
