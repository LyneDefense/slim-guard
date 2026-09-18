import json

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from slim_guard.expression_style.trainer.contracts import BuildBudget
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput, ReviewInput, StyleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import BuildRun, Example, ReviewCase, Version
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.style_management.versions import VersionRepository
from slim_guard.style_management.worker import StyleWorker

from .fakes import TrainerGateway


async def material(db, **overrides):
    return await CorpusRepository(db).append(
        "doctor",
        ExampleInput(
            **{
                "user_input": "谢谢你的帮助",
                "original_response": "不用客气，很高兴帮到你。",
                "desired_response": "不客气。",
                **overrides,
            }
        ),
        "admin",
    )


async def test_fresh_schema_is_clean_and_default_is_a_verified_package(style_db):
    async with style_db.engine.connect() as c:
        tables = await c.run_sync(lambda conn: inspect(conn).get_table_names())
        fields = await c.run_sync(lambda conn: inspect(conn).get_columns("expression_versions"))
    assert "expression_build_runs" in tables
    assert not any(t.startswith("style_") for t in tables)
    assert "snapshot" not in {c["name"] for c in fields}
    assert await style_db.migrate() == ()
    runtime = RuntimeStyles(style_db)
    result = await runtime.get_runtime_snapshot(await runtime.resolve())
    assert result.profile.version == "doctor_builtin_v1"
    assert result.package_hash and result.compiled_prompt
    assert not result.examples


def test_manual_negative_feedback_and_reject_requirements():
    assert ExampleInput(user_input="你好", original_response="输出", correction_opinion="不要说教")
    with pytest.raises(ValidationError):
        ExampleInput(user_input="你好", original_response="输出")
    assert ReviewInput(style_match=4, fidelity=5, appropriateness=5, decision="accept")
    with pytest.raises(ValidationError):
        ReviewInput(style_match=4, fidelity=5, appropriateness=5, decision="reject")


async def test_freeze_all_revisions_idempotency_and_cancel(style_db):
    corpus, builds = CorpusRepository(style_db), BuildRepository(style_db)
    first = await material(style_db)
    negative = await material(style_db, desired_response="", correction_opinion="不要套话")
    async with style_db.session() as s, s.begin():
        row = await s.get(Example, first["id"])
        row.last_result = {"role": "conflict", "reason": "旧结论"}
        row.processed_revision = 1
    run = await builds.build("doctor", "admin", model="test-model", request_key="one")
    assert (await builds.build("doctor", "admin", model="test-model", request_key="one"))[
        "id"
    ] == run["id"]
    with pytest.raises(ValueError):
        await builds.build("doctor", "admin", model="test-model")
    await corpus.edit(
        "doctor",
        first["id"],
        ExampleInput(user_input="谢谢", original_response="不客气", desired_response="客气了"),
    )
    frozen = await builds.artifact("doctor", run["id"], "input_materials")
    assert {m["id"] for m in frozen["items"]} == {first["id"], negative["id"]}
    assert all(m["revision"] == 1 for m in frozen["items"])
    assert (await corpus.examples("doctor", participation="unused"))["total"] == 2
    await builds.action("doctor", run["id"], "cancel")
    assert not await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    assert (await builds.detail("doctor", run["id"]))["status"] == "cancelled"
    await builds.action("doctor", run["id"], "resume")


async def test_full_build_uses_independent_cases_then_review_publish(style_db):
    corpus, builds, versions = (
        CorpusRepository(style_db),
        BuildRepository(style_db),
        VersionRepository(style_db),
    )
    first = await material(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = TrainerGateway()
    assert await StyleWorker(style_db, gateway, "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "completed", detail["error"]
    assert detail["report"]["conclusion"] == "无明确提升"
    assert detail["usage"]["calls"] == len(gateway.requests)
    assert (await corpus.examples("doctor", participation="used"))["total"] == 1
    cases = await versions.cases("doctor", run["id"])
    assert cases["total"] == 12  # Independent of the single input material.
    assert all(c["user_input"] != first["user_input"] for c in cases["items"])
    events = await builds.events("doctor", run["id"], limit=10000)
    assert [e["sequence"] for e in events["items"]] == list(range(1, events["total"] + 1))
    assert any(e.get("state") == "waiting_model" for e in events["items"])
    with pytest.raises(ValueError):
        await versions.action("doctor", run["id"], "publish")
    for case in cases["items"]:
        await versions.review(
            "doctor",
            case["id"],
            ReviewInput(style_match=4, fidelity=5, appropriateness=4, decision="accept"),
            "admin",
        )
    assert (await corpus.examples("doctor"))["total"] == 1  # No synthetic positive feedback loop.
    await versions.action("doctor", run["id"], "publish")
    assert await RuntimeStyles(style_db).resolve() == "doctor_builtin_v1"
    await versions.action("doctor", run["id"], "activate")
    assert await RuntimeStyles(style_db).resolve() == run["id"]
    with pytest.raises(ValueError):
        await versions.review(
            "doctor",
            cases["items"][0]["id"],
            ReviewInput(
                style_match=1, fidelity=1, appropriateness=1, decision="reject", reason="修改"
            ),
            "admin",
        )
    runtime = await RuntimeStyles(style_db).get_runtime_snapshot(run["id"])
    assert runtime.examples[0].example_id == first["id"]
    assert runtime.package_hash
    with pytest.raises(LookupError):
        await builds.artifact("doctor", run["id"], "model:private-cache")
    # Rewriting receives current context, never test labels or hidden reference answers.
    for request in gateway.requests:
        if request.output_schema_name == "StyledResponse":
            raw = json.loads(request.messages[1].content)
            assert "acceptance" not in raw and "human_references" not in raw


async def test_failed_build_does_not_mark_material_used_and_resume_reuses_cache(style_db):
    await material(style_db)
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    gateway = TrainerGateway()
    gateway.fail_schema = "TestSuite"
    worker = StyleWorker(style_db, gateway, "test-model")
    await worker.run_once()
    assert (await builds.detail("doctor", run["id"]))["status"] == "failed"
    assert (await CorpusRepository(style_db).examples("doctor", participation="unused"))[
        "total"
    ] == 1
    analysis_calls = sum(r.output_schema_name == "AnalysisBatch" for r in gateway.requests)
    gateway.fail_schema = None
    await builds.action("doctor", run["id"], "resume")
    await worker.run_once()
    assert (await builds.detail("doctor", run["id"]))["status"] == "completed"
    assert sum(r.output_schema_name == "AnalysisBatch" for r in gateway.requests) == analysis_calls


async def test_budget_failures_and_style_scoping(style_db):
    corpus, builds = CorpusRepository(style_db), BuildRepository(style_db)
    first = await material(style_db)
    other = await corpus.create_style(StyleInput(name="另一风格"))
    run = await builds.build(
        "doctor", "admin", model="test-model", budget=BuildBudget(max_calls=20)
    )
    with pytest.raises(LookupError):
        await builds.detail(other["id"], run["id"])
    with pytest.raises(LookupError):
        await corpus.delete(other["id"], first["id"])
    await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    detail = await builds.detail("doctor", run["id"])
    assert detail["status"] == "failed"
    assert detail["usage"]["calls"] == 20
    async with style_db.session() as s:
        assert await s.get(Version, run["id"]) is None


async def test_bad_test_objection_never_becomes_style_material_or_regression(style_db):
    from .fakes import suite_payload

    await material(style_db)
    case = suite_payload()["acceptance"][0]
    async with style_db.session() as s, s.begin():
        s.add(Version(id="bad-test-version", style_id="doctor", name="bad-test", actor="test"))
        await s.flush()
        s.add(
            ReviewCase(
                id="bad-case",
                version_id="bad-test-version",
                test_case=case,
                user_input=case["user_input"],
                original_response=case["source_text"],
                doctor_response="原稿已有问题",
                baseline_response="原稿已有问题",
            )
        )
    await VersionRepository(style_db).review(
        "doctor",
        "bad-case",
        ReviewInput(
            style_match=3,
            fidelity=3,
            appropriateness=3,
            decision="reject",
            concern="test_case",
            reason="题目声称保存失败，但中性原稿却说保存成功",
        ),
        "admin",
    )
    run = await BuildRepository(style_db).build("doctor", "admin", model="test-model")
    async with style_db.session() as s:
        snapshot = (await s.get(BuildRun, run["id"])).snapshot
    assert snapshot["feedback"] == []
    assert snapshot["human_feedback_count"] == 0
    assert len(snapshot["evaluation_objections"]) == 1
    assert snapshot["consumed_tests"] == [case]
