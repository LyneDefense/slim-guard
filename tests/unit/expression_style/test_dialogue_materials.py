from sqlalchemy import text

from slim_guard.expression_style.trainer.contracts import Material, MaterialAnalysis
from slim_guard.expression_style.trainer.preview import fixed_suite, replay_suite
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.dialogue_migration import migrate_dialogue_examples


async def test_sample_history_round_trips_and_revises_without_changing_releases(style_db):
    corpus = CorpusRepository(style_db)
    history = [
        {"role": "user", "content": "我不想运动"},
        {"role": "assistant", "content": "那就休息一天。"},
    ]
    original = ExampleInput(
        user_input="我不爱运动",
        original_response="旧回答",
        desired_response="散步也不喜欢？",
        history=history,
    )
    item = await corpus.append("doctor", original, "admin")
    assert item["history"] == history and item["revision"] == 1
    unchanged = await corpus.edit("doctor", item["id"], original)
    assert unchanged["revision"] == 1
    changed = await corpus.edit("doctor", item["id"], original.model_copy(update={"history": ()}))
    assert changed["revision"] == 2 and changed["history"] == []
    async with style_db.session() as session:
        assert (
            await session.scalar(
                text("SELECT active_version_id FROM expression_styles WHERE id='doctor'")
            )
            == "doctor_builtin_v1"
        )


async def test_additive_history_migration_preserves_old_materials_and_is_idempotent(style_db):
    item = await CorpusRepository(style_db).append(
        "doctor",
        ExampleInput(user_input="你好", original_response="旧回答", desired_response="你好。"),
        "admin",
    )
    async with style_db.engine.begin() as connection:
        before = (
            await connection.execute(text("SELECT id, package FROM expression_versions"))
        ).all()
        await connection.execute(text("ALTER TABLE expression_examples DROP COLUMN history"))
        await migrate_dialogue_examples(connection)
        await migrate_dialogue_examples(connection)
        after = (
            await connection.execute(text("SELECT id, package FROM expression_versions"))
        ).all()
        assert before == after
    restored = (await CorpusRepository(style_db).examples("doctor"))["items"][0]
    assert restored["id"] == item["id"] and restored["history"] == []
    assert restored["desired_response"] == "你好。" and restored["revision"] == 1


def test_replay_preserves_history_without_treating_original_answer_as_factual_evidence():
    material = Material(
        id="m",
        revision=1,
        user_input="当前消息",
        original_response="旧原稿",
        desired_response="期望回答",
        history=[
            {"role": "user", "content": "前一句"},
            {"role": "assistant", "content": "实际回答"},
        ],
    )
    suite = replay_suite(
        fixed_suite(),
        (material,),
        (MaterialAnalysis(example_id="m", role="positive", reason="可用"),),
        [],
    )
    replay = suite.cases[-1]
    assert replay.history == material.history and replay.context == ()
    assert (
        replay.source_text == "旧原稿"
    )  # Audit only; the preview port never receives it as evidence.
