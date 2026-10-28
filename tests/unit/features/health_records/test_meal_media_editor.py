import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import update

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    MealRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.features.health_records.commands import (
    CreateRecord,
    RecordWriteContext,
    UpdateRecord,
)
from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.meal.changes import MealPatch
from slim_guard.features.health_records.meal.editing import MealEditRequest
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.features.health_records.persistence.evidence import validate_evidence
from slim_guard.features.health_records.persistence.meal_media_migration import migrate_meal_media
from slim_guard.features.health_records.provenance import (
    FieldEvidence,
    FieldProvenance,
    RecognitionIssue,
)
from slim_guard.features.health_records.queries import RecordQuery
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.services.record_changes import build_record_service


@pytest.fixture
async def setup_meal(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'meal-editor.sqlite'}")
    await db.migrate()
    async with db.session() as session, session.begin():
        rows = [
            SlimGuardUser(id="u1", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)),
            SlimGuardUser(id="u2", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)),
            AgentVersionRecord(id="v1", manifest_json="{}", code_revision="test"),
            AgentThreadRecord(id="t1", user_id="u1"),
            AgentTurnRecord(
                id="turn1", thread_id="t1", agent_version_id="v1", trigger_type="user_message"
            ),
            AgentItemRecord(
                id="m1",
                thread_id="t1",
                turn_id="turn1",
                sequence=1,
                item_type="user_message",
                status="completed",
                payload_json='{"text":"早餐", "source_message_id":"source1"}',
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
    photo = (
        await ImageAssetRepository(db).save(
            SaveImageAssetCommand(
                user_id="u1",
                content=b"\x89PNG\r\n\x1a\nmeal",
                declared_mime_type="image/png",
            )
        )
    ).asset.id
    service = build_record_service(db)
    context = RecordWriteContext(
        user_id="u1",
        thread_id="t1",
        turn_id="turn1",
        source_item_id="m1",
        tool_call_id="c1",
        operation_id="op1",
    )
    command = CreateRecord(
        content=MealContent(
            meal_type="breakfast",
            photo_asset_ids=(photo,),
            foods=(
                MealItem(item_id="f1", name="鸡肉炒菜", portion="一份"),
                MealItem(item_id="f2", name="米饭"),
            ),
        ),
        occurrence=Occurrence(),
        field_provenance=(
            FieldProvenance(
                field_ref="foods/f1/name",
                value_revision=1,
                source_kind="image_recognized",
                source_refs=(photo,),
            ),
        ),
        recognition_issues=(
            RecognitionIssue(
                issue_id="issue1",
                field_ref="foods/f1/name",
                value_revision=1,
                reason="遮挡",
                user_message="如需更正请告诉我",
            ),
        ),
    )
    record = (await service.write(context, command)).record
    yield db, service, record, photo, context, command
    await db.close()


def edit(record, **values):
    return MealEditRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "expected_revision": record.ref.revision,
            "meal_type": "dinner",
            "foods": [
                {"item_id": "f1", "name": "香菇炒鸡肉"},
                {"name": "炒花菜"},
            ],
            **values,
        }
    )


async def test_manual_edit_add_remove_rename_and_meal_type_preserves_photos_and_portions(
    setup_meal,
):
    db, service, record, photo, _, _ = setup_meal
    request = edit(record)
    changed = await service.edit_meal("u1", record.ref.record_id, request)
    assert changed.ref.revision == 2
    assert changed.content.meal_type == "dinner"
    assert [(food.name, food.portion) for food in changed.content.foods] == [
        ("香菇炒鸡肉", "一份"),
        ("炒花菜", None),
    ]
    assert changed.content.foods[0].item_id == "f1"
    assert changed.content.foods[1].item_id not in {"f1", "f2"}
    assert changed.content.photo_asset_ids == (photo,)
    assert changed.recognition_issues == ()
    evidence = next(p for p in changed.field_provenance if p.field_ref == "foods/f1/name")
    assert evidence.source_kind == "user_reported"
    async with db.session() as session:
        await validate_evidence(
            session,
            "u1",
            FieldEvidence.model_validate(
                evidence.model_dump(exclude={"field_ref", "value_revision"})
            ),
        )
    page = await service.search("u1", RecordQuery(kind="meal"))
    assert page.records[0].content.photo_asset_ids == (photo,)
    assert await service.edit_meal("u1", record.ref.record_id, request) == changed


async def test_editor_isolates_users_stale_revisions_and_changed_retry_payload(setup_meal):
    _, service, record, _, _, _ = setup_meal
    request = edit(record)
    with pytest.raises(RecordError):
        await service.edit_meal("u2", record.ref.record_id, request)
    await service.edit_meal("u1", record.ref.record_id, request)
    with pytest.raises(RecordConflict):
        await service.edit_meal("u1", record.ref.record_id, edit(record))
    with pytest.raises(RecordConflict):
        await service.edit_meal(
            "u1", record.ref.record_id, request.model_copy(update={"meal_type": "snack"})
        )
    with pytest.raises(RecordError):
        await service.edit_meal(
            "u1",
            record.ref.record_id,
            edit(record, foods=[{"item_id": "other-meal-food", "name": "肉"}]),
        )


async def test_identical_concurrent_edits_replay_one_committed_revision(setup_meal):
    _, service, record, _, _, _ = setup_meal
    command = edit(record)
    first, second = await asyncio.gather(
        service.edit_meal("u1", record.ref.record_id, command),
        service.edit_meal("u1", record.ref.record_id, command),
    )
    assert first == second
    assert first.ref.revision == 2
    assert (await service.get("u1", "meal", record.ref.record_id)).ref.revision == 2


async def test_late_recognition_cannot_replace_direct_user_edits(setup_meal):
    _, service, record, photo, context, _ = setup_meal
    changed = await service.edit_meal("u1", record.ref.record_id, edit(record))
    patch = MealPatch.model_validate(
        {
            "food_changes": [
                {
                    "action": "replace_name",
                    "item_id": "f1",
                    "name": "炸鸡",
                    "evidence": {"source_kind": "image_recognized", "source_refs": [photo]},
                }
            ]
        }
    )
    with pytest.raises(RecordConflict):
        await service.write(
            context.model_copy(update={"operation_id": "late"}),
            UpdateRecord(
                target=changed.ref,
                patch=patch,
            ),
        )


async def test_foreign_photo_cannot_be_attached(setup_meal):
    db, service, _, _, context, command = setup_meal
    other = (
        await ImageAssetRepository(db).save(
            SaveImageAssetCommand(
                user_id="u2",
                content=b"\x89PNG\r\n\x1a\nother",
                declared_mime_type="image/png",
            )
        )
    ).asset.id
    content = command.content.model_copy(update={"photo_asset_ids": (other,)})
    with pytest.raises(RecordError, match="Meal photo"):
        await service.write(
            context.model_copy(update={"operation_id": "foreign"}),
            command.model_copy(update={"content": content}),
        )


async def test_backfill_uses_owned_evidence_and_is_idempotent(setup_meal):
    db, service, record, photo, _, _ = setup_meal
    async with db.session() as session, session.begin():
        await session.execute(
            update(MealRecord)
            .where(MealRecord.id == record.ref.record_id)
            .values(
                photo_asset_ids_json="[]",
                source_refs_json=json.dumps(["m1", photo]),
            )
        )
    async with db.engine.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE meal_records DROP COLUMN photo_asset_ids_json"
        )
        await migrate_meal_media(connection)
        await migrate_meal_media(connection)
    assert (await service.get("u1", "meal", record.ref.record_id)).content.photo_asset_ids == (
        photo,
    )


async def test_missing_evidence_backfill_only_binds_a_single_meal_source_message(setup_meal):
    db, service, record, photo, context, command = setup_meal
    async with db.session() as session, session.begin():
        session.add(
            AgentItemRecord(
                id="image-item",
                thread_id="t1",
                turn_id="turn1",
                sequence=2,
                item_type="image_attachment",
                status="completed",
                payload_json=json.dumps(
                    {
                        "source_message_id": "source1",
                        "asset_id": photo,
                    }
                ),
            )
        )
        await session.execute(
            update(MealRecord)
            .where(MealRecord.id == record.ref.record_id)
            .values(
                photo_asset_ids_json="[]",
                source_refs_json='["m1"]',
            )
        )
    async with db.engine.begin() as connection:
        await migrate_meal_media(connection)
    assert (await service.get("u1", "meal", record.ref.record_id)).content.photo_asset_ids == (
        photo,
    )
    await service.write(context.model_copy(update={"operation_id": "second-meal"}), command)
    async with db.session() as session, session.begin():
        await session.execute(
            update(MealRecord).values(photo_asset_ids_json="[]", source_refs_json='["m1"]')
        )
    async with db.engine.begin() as connection:
        await migrate_meal_media(connection)
    assert not (await service.get("u1", "meal", record.ref.record_id)).content.photo_asset_ids


async def test_attach_supplemental_photo_preserves_record_identity(setup_meal):
    db, service, record, photo, context, _ = setup_meal
    supplemental = (
        await ImageAssetRepository(db).save(
            SaveImageAssetCommand(
                user_id="u1",
                content=b"\x89PNG\r\n\x1a\nsecond",
                declared_mime_type="image/png",
            )
        )
    ).asset.id
    patch = MealPatch.model_validate(
        {
            "photo_asset_ids": {
                "value": [photo, supplemental],
                "evidence": {"source_kind": "user_reported", "source_refs": ["m1"]},
            }
        }
    )
    result = await service.write(
        context.model_copy(update={"operation_id": "attach-photo"}),
        UpdateRecord(target=record.ref, patch=patch),
    )
    assert result.record.ref.record_id == record.ref.record_id
    assert result.record.content.photo_asset_ids == (photo, supplemental)


def test_editor_can_replace_all_twenty_foods_without_losing_photos():
    from slim_guard.features.health_records.meal.editing import edit_patch
    from slim_guard.features.health_records.mutations import apply_record_patch
    from slim_guard.features.health_records.references import RecordReference
    from slim_guard.features.health_records.views import RecordView

    now = datetime.now(UTC)
    record = RecordView(
        ref=RecordReference(kind="meal", record_id="meal1", revision=1),
        status="active",
        occurrence=Occurrence(),
        recorded_at=now,
        updated_at=now,
        content=MealContent(
            photo_asset_ids=("photo1",),
            foods=tuple(
                MealItem(item_id=f"food-{index}", name=f"旧菜{index}") for index in range(20)
            ),
        ),
    )
    command = edit(record, foods=[{"name": f"新菜{index}"} for index in range(20)])
    patch, ids = edit_patch(record.content, command, source_id="manual-input")
    allocated = iter(ids)
    result = apply_record_patch(
        record, patch, expected_revision=1, now=now, new_id=lambda: next(allocated)
    )
    assert len(result.record.content.foods) == 20
    assert result.record.content.foods[0].name == "新菜0"
    assert result.record.content.photo_asset_ids == ("photo1",)
