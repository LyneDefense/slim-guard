import json

import pytest
from pydantic import ValidationError

from slim_guard.agent_models.gateway import ModelMessage
from slim_guard.expression_style.package import StylePackage
from slim_guard.expression_style.trainer.guide_edit import EditableGuide
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import BuildInput, ExampleInput, ReviewInput, StyleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import BuildRun
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.style_management.versions import VersionRepository
from slim_guard.style_management.worker import StyleWorker

from .fakes import TrainerGateway


async def build(db, gateway=None):
    await CorpusRepository(db).append(
        "doctor",
        ExampleInput(
            user_input="你好",
            original_response="你好，很高兴见到你。",
            desired_response="你好。",
        ),
        "admin",
    )
    run = await BuildRepository(db).build("doctor", "admin", model="test-model")
    await StyleWorker(db, gateway or TrainerGateway(), "test-model").run_once()
    assert (await BuildRepository(db).detail("doctor", run["id"]))["status"] == "completed"
    return run


async def accept_all(db, version):
    repo = VersionRepository(db)
    cases = await repo.cases("doctor", version)
    for case in cases["items"]:
        await repo.review(
            "doctor",
            case["id"],
            ReviewInput(
                style_match=1,
                fidelity=5,
                appropriateness=3,
                decision="accept",
                reason="语气由我确认，不由自动偏好评分决定",
            ),
            "admin",
        )


class ReviewGateway(TrainerGateway):
    def __init__(self, dimension):
        super().__init__()
        self.dimension = dimension

    async def complete(self, request):
        result = await super().complete(request)
        if request.output_schema_name == "ReplyCheck":
            payload = json.loads(request.messages[1].content)
            value = {
                "fidelity_passed": self.dimension != "fidelity",
                "expression_passed": self.dimension != "expression",
                "issues": [
                    {
                        "dimension": self.dimension,
                        "severity": "blocking",
                        "code": "test_issue",
                        "explanation": "测试审查意见",
                        "source_excerpt": payload["original"],
                        "output_excerpt": payload["rewritten"],
                        "repair_requirement": "核对对应维度",
                    }
                ],
            }
            return result.model_copy(
                update={
                    "message": ModelMessage(
                        role="assistant",
                        content=json.dumps(value),
                    )
                }
            )
        return result


async def test_expression_opinion_never_blocks_human_publication(style_db):
    gateway = ReviewGateway("expression")
    run = await build(style_db, gateway)
    report = (await BuildRepository(style_db).detail("doctor", run["id"]))["report"]
    assert report["release_eligible"] and report["preview_metrics"]["expression_advisories"] == 9
    cases = (await VersionRepository(style_db).cases("doctor", run["id"]))["items"]
    assert all(c["automated"]["passed"] and not c["automated"]["fallback"] for c in cases)
    assert all(len(c["automated"]["checks"]) == 1 for c in cases)
    await accept_all(style_db, run["id"])
    await VersionRepository(style_db).action("doctor", run["id"], "publish")
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"


async def test_fidelity_failure_still_blocks_even_if_human_accepts(style_db):
    run = await build(style_db, ReviewGateway("fidelity"))
    report = (await BuildRepository(style_db).detail("doctor", run["id"]))["report"]
    assert not report["release_eligible"] and report["preview_metrics"]["content_failed"] == 9
    await accept_all(style_db, run["id"])
    with pytest.raises(ValueError, match="语义检查"):
        await VersionRepository(style_db).action("doctor", run["id"], "publish")


async def test_repeated_builds_reuse_fixed_cases_and_preserve_output_feedback(style_db):
    first = await build(style_db)
    repo, builds = VersionRepository(style_db), BuildRepository(style_db)
    case = (await repo.cases("doctor", first["id"]))["items"][0]
    await repo.review(
        "doctor",
        case["id"],
        ReviewInput(
            style_match=2,
            fidelity=5,
            appropriateness=3,
            decision="reject",
            reason="少一点客套",
            desired_response="说重点。",
        ),
        "admin",
    )
    second = await builds.build("doctor", "admin", model="test-model")
    async with style_db.session() as session:
        snapshot = (await session.get(BuildRun, second["id"])).snapshot
    assert snapshot["human_feedback_count"] == 1
    assert snapshot["materials"][-1]["desired_response"] == "说重点。"
    gateway = TrainerGateway()
    await StyleWorker(style_db, gateway, "test-model").run_once()
    assert (await builds.detail("doctor", second["id"]))["status"] == "completed"
    one = (await builds.artifact("doctor", first["id"], "preview_suite"))["value"]
    two = (await builds.artifact("doctor", second["id"], "preview_suite"))["value"]
    assert one["version"] == two["version"]
    assert {c["id"] for c in one["cases"] if c["origin"] == "fixed"} <= {
        c["id"] for c in two["cases"]
    }
    assert not {"TestSuite", "Comparison"} & {r.output_schema_name for r in gateway.requests}


async def test_guide_edit_creates_new_preview_without_mutating_source_or_online(style_db):
    first = await build(style_db)
    versions, builds = VersionRepository(style_db), BuildRepository(style_db)
    source = (await builds.artifact("doctor", first["id"], "selected"))["value"]
    edited = EditableGuide(
        summary="自然直接，不说教。",
        rules=(
            {
                "rule_id": source["guide"]["rules"][0]["rule_id"],
                "text": "少用“很高兴”，不加\n客套开场。",
                "boundary": "不删事实或条件",
            },
        ),
        prohibited_phrases=("亲亲",),
    )
    revised = await builds.build(
        "doctor",
        "admin",
        model="test-model",
        payload=BuildInput(
            source_version_id=first["id"],
            guide=edited,
        ),
    )
    gateway = TrainerGateway()
    await StyleWorker(style_db, gateway, "test-model").run_once()
    detail = await builds.detail("doctor", revised["id"])
    assert detail["status"] == "completed", detail["error"]
    package = StylePackage.model_validate(
        (await builds.artifact("doctor", revised["id"], "selected"))["value"]
    )
    assert (
        package.guide.summary == edited.summary
        and package.guide.rules[0].text == edited.rules[0].text
    )
    assert package.examples and package.version_id == revised["id"]
    assert (await builds.artifact("doctor", first["id"], "selected"))["value"] == source
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
    assert all(r.output_schema_name in {"StyledResponse", "ReplyCheck"} for r in gateway.requests)
    with pytest.raises(ValueError):
        await versions.action("doctor", revised["id"], "publish")
    await accept_all(style_db, revised["id"])
    await versions.action("doctor", revised["id"], "publish")
    await versions.action("doctor", revised["id"], "activate")
    runtime = await RuntimeStyles(style_db).get_runtime_snapshot(revised["id"])
    assert runtime.package_hash == package.package_hash
    next_run = await builds.build("doctor", "admin", model="test-model")
    inputs = (await builds.artifact("doctor", next_run["id"], "input_materials"))["items"]
    assert any(edited.rules[0].text in m["correction_opinion"] for m in inputs)


async def test_guide_edit_is_scoped_and_invalid_edit_does_not_add_materials(style_db):
    source = await build(style_db)
    other = await CorpusRepository(style_db).create_style(StyleInput(name="其他风格"))
    with pytest.raises(LookupError):
        await BuildRepository(style_db).build(
            other["id"],
            "admin",
            model="test-model",
            payload=BuildInput(
                source_version_id=source["id"],
                guide=EditableGuide(summary="不要套话"),
            ),
        )
    assert (await CorpusRepository(style_db).examples(other["id"]))["total"] == 0


async def test_legacy_resume_is_rejected_without_losing_materials(style_db):
    await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(
            user_input="你好",
            original_response="你好",
            desired_response="你好。",
        ),
        "admin",
    )
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    async with style_db.session() as session, session.begin():
        row = await session.get(BuildRun, run["id"])
        row.status = "failed"
        row.snapshot = {**row.snapshot, "workflow_version": "legacy"}
    assert not (await builds.detail("doctor", run["id"]))["can_resume"]
    with pytest.raises(ValueError, match="旧构建流程"):
        await builds.action("doctor", run["id"], "resume")
    assert (await CorpusRepository(style_db).examples("doctor"))["total"] == 1


def test_guide_edit_input_requires_source_and_rejects_blank_prohibited_phrase():
    with pytest.raises(ValidationError):
        BuildInput(guide=EditableGuide(summary="直接说重点"))
    with pytest.raises(ValidationError):
        EditableGuide(summary="直接说重点", prohibited_phrases=("",))


async def test_suite_is_frozen_at_submission_not_reloaded_on_worker_start(style_db, monkeypatch):
    from slim_guard.expression_style.trainer.preview import fixed_suite

    await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(
            user_input="你好",
            original_response="你好",
            desired_response="你好。",
        ),
        "admin",
    )
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    monkeypatch.setattr(
        "slim_guard.style_management.builds.fixed_suite",
        lambda: fixed_suite().model_copy(
            update={"version": "next-fixtures", "cases": fixed_suite().cases[:1]},
        ),
    )
    await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    frozen = (await builds.artifact("doctor", run["id"], "preview_suite"))["value"]
    assert frozen["version"] == fixed_suite().version
    assert len([c for c in frozen["cases"] if c["origin"] == "fixed"]) == 8
