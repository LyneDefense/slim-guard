import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    ConversationTaskRecord,
    MealRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.features.health_records.commands import (
    CreateRecord,
    RecordWriteContext,
    TaskCompletion,
    UpdateRecord,
)
from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.persistence.migration import migrate_health_records
from slim_guard.features.health_records.provenance import FieldProvenance, RecognitionIssue
from slim_guard.features.health_records.queries import RecordQuery
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.services.record_changes import build_record_service


@pytest.fixture
async def records(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'records.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        rows = [
            SlimGuardUser(id="u1", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)),
            SlimGuardUser(id="u2", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)),
            AgentVersionRecord(id="v1", manifest_json="{}", code_revision="test"),
            AgentThreadRecord(id="thread1", user_id="u1"),
            AgentThreadRecord(id="thread2", user_id="u2"),
            AgentTurnRecord(
                id="turn1",
                thread_id="thread1",
                agent_version_id="v1",
                trigger_type="user_message",
            ),
            AgentTurnRecord(
                id="turn2",
                thread_id="thread2",
                agent_version_id="v1",
                trigger_type="user_message",
            ),
            AgentItemRecord(
                id="message1",
                thread_id="thread1",
                turn_id="turn1",
                sequence=1,
                item_type="user_message",
                status="completed",
                payload_json='{"text":"午餐记错了"}',
            ),
            AgentItemRecord(
                id="message2",
                thread_id="thread2",
                turn_id="turn2",
                sequence=1,
                item_type="user_message",
                status="completed",
                payload_json='{"text":"这是另一位用户的消息"}',
            ),
        ]
        for model in (
            SlimGuardUser,
            AgentVersionRecord,
            AgentThreadRecord,
            AgentTurnRecord,
            AgentItemRecord,
        ):
            session.add_all(row for row in rows if isinstance(row, model))
            await session.flush()
    yield database, build_record_service(database)
    await database.close()


def context(operation="operation1", user="u1"):
    return RecordWriteContext(
        user_id=user,
        thread_id="thread1",
        turn_id="turn1",
        source_item_id="message1",
        tool_call_id="call1",
        operation_id=operation,
    )


def create():
    return CreateRecord(
        content=MealContent(foods=(MealItem(item_id="food1", name="土豆肉丝"),)),
        occurrence=Occurrence(
            precision="date", on=date(2026, 9, 23), timezone="Asia/Shanghai", basis="user_reported"
        ),
        field_provenance=(
            FieldProvenance(
                field_ref="foods/food1/name",
                value_revision=1,
                source_kind="context_inferred",
                source_refs=("message1",),
            ),
        ),
        recognition_issues=(
            RecognitionIssue(
                issue_id="issue1",
                field_ref="foods/food1/name",
                value_revision=1,
                reason="遮挡",
                user_message="菜名可能不准确",
            ),
        ),
    )


def rename(record, name="青椒肉丝", **extra):
    return UpdateRecord(
        target=record.ref,
        patch=MealPatch.model_validate(
            {
                "food_changes": [
                    {
                        "action": "replace_name",
                        "item_id": "food1",
                        "name": name,
                        "evidence": {"source_kind": "user_reported", "source_refs": ["message1"]},
                    }
                ]
            }
        ),
        **extra,
    )


async def test_create_correct_and_replay_original_snapshots(records):
    _, service = records
    first = await service.write(context(), create())
    command = rename(first.record)
    changed = await service.write(context("change1"), command)
    assert changed.record.ref.record_id == first.record.ref.record_id
    assert changed.record.ref.revision == 2
    assert changed.cleared_issue_ids == ("issue1",)
    assert changed.record.recognition_issues == ()
    assert (await service.write(context(), create())) == first
    assert (await service.write(context("change1"), command)) == changed
    assert (await service.get("u1", "meal", first.record.ref.record_id)).content.foods[
        0
    ].name == "青椒肉丝"
    assert (await service.receipt("u1", "operation1")) == first


async def test_stale_version_and_user_isolation(records):
    _, service = records
    first = await service.write(context(), create())
    await service.write(context("change1"), rename(first.record))
    with pytest.raises(RecordConflict):
        await service.write(context("change2"), rename(first.record, "木瓜炒肉"))
    with pytest.raises(RecordError):
        await service.get("u2", "meal", first.record.ref.record_id)
    assert await service.receipt("u2", "operation1") is None


async def test_operation_identity_rejects_changed_command(records):
    _, service = records
    first = await service.write(context(), create())
    await service.write(context("change"), rename(first.record))
    with pytest.raises(RecordConflict):
        await service.write(context("change"), rename(first.record, "木瓜炒肉"))


async def test_history_paginates_and_binds_filters(records):
    _, service = records
    for index in range(3):
        await service.write(context(f"new-{index}"), create())
    query = RecordQuery(kind="meal", limit=1, from_date=date(2026, 9, 23))
    first = await service.search("u1", query)
    assert first.has_more
    second = await service.search("u1", query.model_copy(update={"cursor": first.next_cursor}))
    assert first.records[0].ref.record_id != second.records[0].ref.record_id
    assert first.records[0].occurrence.at is None
    with pytest.raises(RecordError):
        await service.search("u2", query.model_copy(update={"cursor": first.next_cursor}))
    empty = await service.search("u1", RecordQuery(kind="meal", from_date=date(2026, 9, 24)))
    assert empty.records == ()


async def test_task_failure_rolls_back_record_and_receipt(records):
    _, service = records
    first = await service.write(context(), create())
    with pytest.raises(RecordConflict):
        await service.write(
            context("change1"),
            rename(
                first.record,
                complete_tasks=(TaskCompletion(task_id="missing", expected_revision=1),),
            ),
        )
    assert (await service.get("u1", "meal", first.record.ref.record_id)).ref.revision == 1
    assert await service.receipt("u1", "change1") is None


async def test_task_and_record_commit_together(records):
    database, service = records
    first = await service.write(context(), create())
    async with database.session() as session, session.begin():
        session.add(
            ConversationTaskRecord(
                id="task1",
                user_id="u1",
                thread_id="thread1",
                objective="更正菜名",
                unresolved_json='["菜名"]',
                source_turn_id="turn1",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                request_json=json.dumps(
                    {
                        "action": "update",
                        "record_kind": "meal",
                        "selected_targets": [first.record.ref.model_dump()],
                    }
                ),
            )
        )
    result = await service.write(
        context("change1"),
        rename(
            first.record, complete_tasks=(TaskCompletion(task_id="task1", expected_revision=1),)
        ),
    )
    assert result.task_updates == ("task1",)
    async with database.session() as session:
        task = await session.scalar(
            select(ConversationTaskRecord).where(ConversationTaskRecord.id == "task1")
        )
        assert task.status == "resolved"
        assert task.revision == 2


async def test_legacy_record_migration_preserves_unknown_time_and_marks_evidence_legacy(records):
    database, service = records
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add_all(
            [
                MealRecord(
                    id="legacy-meal",
                    user_id="u1",
                    meal_type="lunch",
                    foods_json='[{"name":"米饭","portion":null}]',
                    note=None,
                    occurred_at=None,
                    status="active",
                    idempotency_key="legacy-meal-key",
                    source_turn_id="turn1",
                    source_item_id="message1",
                    source_tool_call_id="legacy-call",
                    created_at=now,
                ),
                MealRecord(
                    id="legacy-meal-foreign-source",
                    user_id="u1",
                    meal_type="lunch",
                    foods_json='[{"name":"米饭","portion":null}]',
                    note=None,
                    occurred_at=None,
                    status="active",
                    idempotency_key="legacy-meal-foreign-key",
                    source_turn_id="turn1",
                    source_item_id="message2",
                    source_tool_call_id="legacy-call-foreign",
                    created_at=now,
                ),
            ]
        )

    async with database.engine.begin() as connection:
        for table_name in (
            "meal_records",
            "exercise_records",
            "weight_records",
            "body_fat_records",
        ):
            for column_name in (
                "revision",
                "updated_at",
                "occurred_on",
                "time_precision",
                "timezone",
                "time_basis",
                "source_refs_json",
                "field_provenance_json",
                "recognition_issues_json",
                "retired_reason",
            ):
                await connection.exec_driver_sql(
                    f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
                )
        await migrate_health_records(connection)
        await migrate_health_records(connection)

    migrated = await service.get("u1", "meal", "legacy-meal")
    assert migrated.occurrence.precision == "unknown"
    assert migrated.occurrence.basis == "unknown"
    assert migrated.source_refs == ("message1",)
    assert migrated.ref.revision == 1
    food = migrated.content.foods[0]
    assert food.name == "米饭"
    assert food.item_id
    assert {entry.source_kind for entry in migrated.field_provenance} == {"legacy"}
    assert all(not entry.source_refs for entry in migrated.field_provenance)
    foreign_source = await service.get("u1", "meal", "legacy-meal-foreign-source")
    assert foreign_source.source_refs == ()
