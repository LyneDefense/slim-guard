import pytest
from sqlalchemy import select, text

from slim_guard.expression_style.trainer.ports import BuildInterrupted
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.job_store import JobStore
from slim_guard.style_management.models import BuildRun, Example, Style, Version
from slim_guard.style_management.trainer_migration import migrate_trainer_schema


async def test_legacy_material_preserved_derived_data_removed_only_in_style_feature(style_db):
    async with style_db.engine.begin() as c:
        await c.execute(text("DROP TABLE expression_examples"))
        await c.execute(
            text(
                "CREATE TABLE expression_examples (id TEXT PRIMARY KEY, style_id TEXT, "
                "user_input TEXT, original_response TEXT, desired_response TEXT, "
                "status TEXT, category TEXT, source TEXT, actor TEXT)"
            )
        )
        await c.execute(
            text(
                "INSERT INTO expression_examples VALUES "
                "('human','doctor','用户','原答','期望','excluded','conflict','correction','admin'),"
                "('auto','doctor','用户','原答','模型回答','approved','expression','accepted_review','admin')"
            )
        )
        await c.execute(text("CREATE TABLE unrelated_sentinel (id INTEGER PRIMARY KEY)"))
        await c.execute(text("INSERT INTO unrelated_sentinel VALUES (1)"))
        await migrate_trainer_schema(c)
        assert (await c.execute(text("SELECT id FROM unrelated_sentinel"))).scalar_one() == 1
    async with style_db.session() as s:
        rows = list(await s.scalars(select(Example)))
        assert [r.id for r in rows] == ["human"]
        assert rows[0].desired_response == "期望" and rows[0].processed_revision == 0
        versions = list(await s.scalars(select(Version)))
        assert [v.id for v in versions] == ["doctor_builtin_v1"]
        assert (await s.get(Style, "doctor")).active_version_id == "doctor_builtin_v1"


async def test_stale_worker_cannot_write_after_cancellation_or_takeover(style_db):
    await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(user_input="谢谢", original_response="不客气", desired_response="客气了"),
        "admin",
    )
    builds = BuildRepository(style_db)
    run = await builds.build("doctor", "admin", model="test-model")
    async with style_db.session() as s, s.begin():
        row = await s.get(BuildRun, run["id"])
        row.status, row.worker_token = "running", "old"
    store = JobStore(style_db, run["id"], "old")
    await store.save("materials", [], stage="materials", message="空阶段")
    await builds.action("doctor", run["id"], "cancel")
    with pytest.raises(BuildInterrupted):
        await store.save("materials", [{"fabricated": True}], stage="materials", message="过期")
    with pytest.raises(BuildInterrupted):
        await store.reserve_call(max_calls=10, max_tokens=1000)
    assert (await builds.artifact("doctor", run["id"], "materials"))["items"] == []


async def test_edit_during_build_does_not_falsely_mark_new_revision_used(style_db):
    corpus, builds = CorpusRepository(style_db), BuildRepository(style_db)
    material = await corpus.append(
        "doctor",
        ExampleInput(user_input="谢谢", original_response="不用客气", desired_response="不客气"),
        "admin",
    )
    run = await builds.build("doctor", "admin", model="test-model")
    await corpus.edit(
        "doctor",
        material["id"],
        ExampleInput(user_input="谢谢", original_response="不用客气", desired_response="客气了"),
    )
    from slim_guard.style_management.worker import StyleWorker

    from .fakes import TrainerGateway

    await StyleWorker(style_db, TrainerGateway(), "test-model").run_once()
    assert (await builds.detail("doctor", run["id"]))["status"] == "completed"
    assert (await corpus.examples("doctor", participation="unused"))["total"] == 1
    frozen = (await builds.artifact("doctor", run["id"], "input_materials"))["items"][0]
    assert frozen["revision"] == 1 and frozen["desired_response"] == "不客气"
