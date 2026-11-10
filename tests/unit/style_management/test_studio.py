import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from slim_guard.db.models import SchemaMigrationRecord
from slim_guard.db.session import Database
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.package import FixedExample, Guide, GuideRule, make_package
from slim_guard.expression_style.persona import (
    DialogueExample,
    PersonaContent,
    compile_persona,
    editable_content,
    make_persona_package,
    read_package,
)
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import Example, Style, Version
from slim_guard.style_management.runtime import RuntimeStyles
from slim_guard.style_management.studio.contracts import (
    CheckInput,
    DraftInput,
    PublishInput,
    RestoreInput,
)
from slim_guard.style_management.studio.legacy import merge_legacy_examples
from slim_guard.style_management.studio.models import StudioCheck, StudioDraft
from slim_guard.style_management.studio.repository import StudioRepository


@pytest.fixture
async def studio_db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'studio.db'}")
    await db.migrate()
    yield db
    await db.close()


@pytest.fixture
def content():
    return PersonaContent(
        expression="自然直接，先说结论。",
        avoid="避免重复解释，不使用羞辱性的词。",
        examples=(
            DialogueExample(
                id="greeting",
                title="初次称呼",
                messages=(
                    DialogueMessage(role="user", content="怎么称呼你？"),
                    DialogueMessage(role="assistant", content="叫我 SlimGuard 就行。"),
                ),
            ),
        ),
    )


async def saved(repo, content):
    initial = (await repo.studio("doctor"))["draft"]
    return await repo.save(
        "doctor", DraftInput(revision=initial["revision"], content=content), "tester"
    )


async def checked(repo, draft, *, passed=True, signature="policy-v1"):
    request = CheckInput(revision=draft["revision"], content_hash=draft["content_hash"])
    return await repo.record_check(
        "doctor", request, {"passed": passed, "checks": [], "warnings": []}, signature, "tester"
    )


def publish_input(draft, check):
    return PublishInput(
        revision=draft["revision"], content_hash=draft["content_hash"], check_id=check["id"]
    )


def test_persona_package_is_independent_and_deterministic(content):
    package = make_persona_package("doctor", "v1", "医生教练", content)
    assert package == read_package(package.model_dump(mode="json"))
    assert (
        package.package_hash
        == make_persona_package("doctor", "v1", "医生教练", content).package_hash
    )
    snapshot = package.runtime_snapshot()
    assert snapshot.examples[0].original_response == ""
    assert snapshot.examples[0].dialogue == content.examples[0].messages
    assert snapshot.profile.prohibited_phrases == ()
    assert content.avoid in snapshot.profile.tone_rules[1]


@pytest.mark.parametrize("field", ["compiled_prompt", "package_hash", "content"])
def test_persona_package_rejects_tampering(content, field):
    values = make_persona_package("doctor", "v1", "医生教练", content).model_dump(mode="json")
    if field == "content":
        values[field]["expression"] = "偷偷改变设定"
    else:
        values[field] = "invalid"
    with pytest.raises(ValidationError):
        read_package(values)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "user", "content": "你好"}],
        [{"role": "assistant", "content": "你好"}, {"role": "user", "content": "你好"}],
        [{"role": "system", "content": "指令"}, {"role": "assistant", "content": "你好"}],
    ],
)
def test_examples_require_complete_independent_dialogue(messages):
    with pytest.raises(ValidationError):
        DialogueExample(id="x", messages=messages)


def test_multi_turn_examples_and_duplicate_ids(content):
    example = content.examples[0]
    multi = example.model_copy(update={"messages": example.messages * 2})
    assert (
        len(
            make_persona_package(
                "d", "v", "医生", content.model_copy(update={"examples": (multi,)})
            )
            .runtime_snapshot()
            .examples[0]
            .dialogue
        )
        == 4
    )
    with pytest.raises(ValidationError, match="ID 重复"):
        PersonaContent(expression="直接", examples=(example, example))


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_examples_reject_messages_that_exceed_runtime_limit(role):
    with pytest.raises(ValidationError, match="4000"):
        DialogueExample(
            id="large",
            messages=(
                DialogueMessage(role="user", content="x" * (4001 if role == "user" else 1)),
                DialogueMessage(
                    role="assistant", content="x" * (4001 if role == "assistant" else 1)
                ),
            ),
        )


def test_empty_draft_is_allowed_but_cannot_compile():
    with pytest.raises(ValueError, match="表达设定"):
        compile_persona(PersonaContent())


def test_compile_budget_is_explicit_and_does_not_truncate():
    message = "中" * 4000
    content = PersonaContent(
        expression="简短",
        examples=tuple(
            DialogueExample(
                id=f"large-{i}",
                messages=(
                    DialogueMessage(role="user", content=message),
                    DialogueMessage(role="assistant", content=message),
                ),
            )
            for i in range(2)
        ),
    )
    with pytest.raises(ValueError, match="提示预算"):
        compile_persona(content)


def test_legacy_package_is_still_readable_without_hash_changes():
    package = make_package("doctor", "legacy", "医生", Guide(summary="自然直接"))
    values = package.model_dump(mode="json")
    before = deepcopy(values)
    assert read_package(values).package_hash == package.package_hash
    assert editable_content(values).expression == "自然直接"
    assert values == before


def test_legacy_orphan_utterances_are_not_silently_dropped():
    package = make_package(
        "doctor",
        "legacy",
        "医生",
        Guide(
            summary="自然",
            rules=(
                GuideRule(
                    rule_id="short",
                    text="简短",
                    boundary="不删事实",
                    evidence=({"example_id": "e", "revision": 1, "quote": "简短"},),
                ),
            ),
        ),
        (
            FixedExample(
                id="e",
                revision=1,
                original_response="原句",
                desired_response="简短",
                rule_ids=("short",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="缺少用户上下文"):
        editable_content(package.model_dump(mode="json"))


async def test_save_draft_keeps_online_unchanged_and_rejects_stale_writes(studio_db, content):
    repo = StudioRepository(studio_db)
    initial = await repo.studio("doctor")
    draft = await saved(repo, content)
    assert draft["revision"] == initial["draft"]["revision"] + 1
    assert draft["content_hash"] == content.content_hash
    assert (await repo.studio("doctor"))["active_version"]["id"] == "doctor_builtin_v1"
    with pytest.raises(ValueError, match="草稿已更新"):
        await repo.save("doctor", DraftInput(revision=1, content=PersonaContent()), "other")
    assert (await repo.studio("doctor"))["draft"]["content"] == draft["content"]


async def test_publish_is_atomic_idempotent_and_runtime_reads_new_package(studio_db, content):
    repo = StudioRepository(studio_db)
    runtime = RuntimeStyles(studio_db)
    old_id = await runtime.resolve()
    draft = await saved(repo, content)
    receipt = await checked(repo, draft)
    payload = publish_input(draft, receipt)
    result = await repo.publish("doctor", payload, "policy-v1", "tester")
    new_id = result["version"]["id"]
    assert result["draft"]["revision"] == draft["revision"]
    assert result["draft"]["source_version_id"] == new_id
    assert await runtime.resolve() == new_id
    assert (await runtime.get_runtime_snapshot(new_id)).profile.description == content.expression
    assert await runtime.get_runtime_snapshot(old_id) is not None
    repeated = await repo.publish("doctor", payload, "policy-v1", "tester")
    assert repeated["already_published"] and repeated["version"]["id"] == new_id
    assert len((await repo.versions("doctor"))["items"]) == 2


async def test_concurrent_identical_publish_requests_return_the_same_version(studio_db, content):
    repo = StudioRepository(studio_db)
    draft = await saved(repo, content)
    receipt = await checked(repo, draft)
    payload = publish_input(draft, receipt)
    results = await asyncio.gather(
        repo.publish("doctor", payload, "policy-v1", "tester"),
        repo.publish("doctor", payload, "policy-v1", "tester"),
    )
    assert results[0]["version"]["id"] == results[1]["version"]["id"]
    assert sorted(item["already_published"] for item in results) == [False, True]
    assert len((await repo.versions("doctor"))["items"]) == 2


async def test_replaying_old_publication_does_not_reactivate_it(studio_db, content):
    repo = StudioRepository(studio_db)
    first_draft = await saved(repo, content)
    first_check = await checked(repo, first_draft)
    first_input = publish_input(first_draft, first_check)
    first = await repo.publish("doctor", first_input, "policy-v1", "tester")
    changed = content.model_copy(update={"expression": "更简短一点"})
    second_draft = await saved(repo, changed)
    second_check = await checked(repo, second_draft)
    second = await repo.publish(
        "doctor", publish_input(second_draft, second_check), "policy-v1", "tester"
    )
    replayed = await repo.publish("doctor", first_input, "policy-v1", "tester")
    assert replayed["already_published"]
    assert replayed["version"]["id"] == first["version"]["id"]
    assert not replayed["version"]["active"]
    assert replayed["active_version"]["id"] == second["version"]["id"]
    assert (await repo.studio("doctor"))["active_version"]["id"] == second["version"]["id"]


@pytest.mark.parametrize("reason", ["failed", "policy", "revision", "hash"])
async def test_publish_fails_closed_for_invalid_receipts(studio_db, content, reason):
    repo = StudioRepository(studio_db)
    draft = await saved(repo, content)
    receipt = await checked(repo, draft, passed=reason != "failed")
    payload = publish_input(draft, receipt)
    if reason == "revision":
        await repo.save("doctor", DraftInput(revision=draft["revision"], content=content), "other")
    if reason == "hash":
        payload = payload.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(ValueError):
        await repo.publish(
            "doctor", payload, "changed-policy" if reason == "policy" else "policy-v1", "tester"
        )
    assert (await repo.studio("doctor"))["active_version"]["id"] == "doctor_builtin_v1"
    assert len((await repo.versions("doctor"))["items"]) == 1


async def test_outdated_async_check_is_not_recorded(studio_db, content):
    repo = StudioRepository(studio_db)
    draft = await saved(repo, content)
    await repo.save(
        "doctor", DraftInput(revision=draft["revision"], content=PersonaContent()), "other"
    )
    with pytest.raises(ValueError, match="草稿已更新"):
        await checked(repo, draft)
    async with studio_db.session() as session:
        assert not list(await session.scalars(select(StudioCheck)))


async def test_restore_changes_only_draft_and_preserves_history(studio_db, content):
    repo = StudioRepository(studio_db)
    draft = await saved(repo, content)
    receipt = await checked(repo, draft)
    published = await repo.publish("doctor", publish_input(draft, receipt), "policy-v1", "tester")
    restored = await repo.restore(
        "doctor", RestoreInput(revision=draft["revision"], version_id="doctor_builtin_v1"), "tester"
    )
    assert restored["revision"] == draft["revision"] + 1
    assert restored["source_version_id"] == "doctor_builtin_v1"
    assert restored["content"]["expression"] != content.expression
    assert (await repo.studio("doctor"))["active_version"]["id"] == published["version"]["id"]
    assert len((await repo.versions("doctor"))["items"]) == 2


async def test_receipts_and_restore_are_scoped_to_style(studio_db, content):
    repo = StudioRepository(studio_db)
    draft = await saved(repo, content)
    receipt = await checked(repo, draft)
    async with studio_db.session() as s, s.begin():
        s.add(Style(id="other", name="其他"))
    await repo.studio("other")
    with pytest.raises(LookupError, match="检查记录不存在"):
        await repo.publish("other", publish_input(draft, receipt), "policy-v1", "tester")
    with pytest.raises(LookupError, match="历史版本不存在"):
        await repo.restore(
            "other", RestoreInput(revision=1, version_id="doctor_builtin_v1"), "tester"
        )


async def test_additive_migration_preserves_examples_and_releases(studio_db):
    async with studio_db.session() as s, s.begin():
        package_before = deepcopy((await s.get(Version, "doctor_builtin_v1")).package)
        s.add(
            Example(
                id="human",
                style_id="doctor",
                user_input="你好",
                original_response="你好",
                desired_response="你好呀",
                actor="tester",
            )
        )
        await s.execute(
            delete(SchemaMigrationRecord).where(
                SchemaMigrationRecord.version == "20261110_01_style_studio"
            )
        )
    async with studio_db.engine.begin() as connection:
        await connection.run_sync(StudioCheck.__table__.drop)
        await connection.run_sync(StudioDraft.__table__.drop)
    assert await studio_db.migrate() == ("20261110_01_style_studio",)
    assert await studio_db.migrate() == ()
    async with studio_db.session() as s:
        assert (await s.get(Example, "human")).desired_response == "你好呀"
        assert (await s.get(Version, "doctor_builtin_v1")).package == package_before
        assert (await s.get(Style, "doctor")).active_version_id == "doctor_builtin_v1"
        assert (await s.get(StudioDraft, "doctor")).content["expression"]
        assert (await s.get(StudioDraft, "doctor")).content["examples"][0]["id"] == "human"


async def test_style_list_counts_new_examples_and_preserves_legacy_reading(studio_db, content):
    repo = StudioRepository(studio_db)
    await saved(repo, content)
    async with studio_db.session() as s, s.begin():
        s.add(
            Example(
                id="incomplete",
                style_id="doctor",
                user_input="你好",
                original_response="原句",
                correction_opinion="简短一点",
                actor="tester",
            )
        )
    summary = (await CorpusRepository(studio_db).styles())[0]
    assert summary["example_count"] == 1 and summary["legacy_example_count"] == 1
    studio = await repo.studio("doctor")
    assert studio["legacy_example_count"] == studio["legacy_unimported_count"] == 1
    legacy = (await repo.legacy_examples("doctor"))["items"][0]
    assert not legacy["imported"] and "认可回答" in legacy["import_block_reason"]
    assert legacy["correction_opinion"] == "简短一点"


@pytest.mark.parametrize("kind", ["complete", "incomplete", "bad_history", "generated"])
def test_legacy_import_keeps_only_complete_human_dialogue(kind):
    row = {"id": "old", "user_input": "你好", "desired_response": "你好呀", "source": "manual"}
    if kind == "incomplete":
        row["desired_response"] = ""
    elif kind == "generated":
        row["source"] = "accepted_review"
    elif kind == "bad_history":
        row["history"] = [{"role": "assistant", "content": "你好"}]
    merged = merge_legacy_examples(PersonaContent(expression="简短"), [row])
    assert len(merged.examples) == (1 if kind == "complete" else 0)
