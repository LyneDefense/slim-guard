from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.weight.contracts import (
    WeightMeasurementCommand,
    WeightMeasurementCondition,
    WeightTrendDirection,
    WeightUnit,
)
from slim_guard.domain.weight.errors import WeightRecordCollision, WeightSourceMismatch
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import ToolExecutionMode


def manifest() -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"max_output_tokens": 512},
        system_prompt_version="test-v1",
        system_prompt="You are SlimGuard.",
        tool_versions={"record_weight": "v1"},
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test-revision",
    )


async def prepare_weight_repository(
    tmp_path,
) -> tuple[Database, WeightRepository, str, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'weight.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
        session.add(SlimGuardUser(id="user-2", first_seen_at=now, last_seen_at=now))
    agent_manifest = manifest()
    await AgentVersionRepository(database).register(agent_manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=agent_manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="今天 77.6kg"),),
        )
    )
    assert initialized.source_item_id is not None
    return (
        database,
        WeightRepository(database),
        initialized.turn.id,
        initialized.source_item_id,
        initialized.thread.user_id,
    )


def command(
    *,
    user_id: str,
    turn_id: str,
    item_id: str,
    value: str = "77.6",
    unit: WeightUnit = WeightUnit.KG,
    measured_at: datetime | None = None,
    idempotency_key: str = "weight-execution-1",
    tool_call_id: str = "call-1",
) -> WeightMeasurementCommand:
    return WeightMeasurementCommand(
        user_id=user_id,
        value=Decimal(value),
        unit=unit,
        measured_at=measured_at or datetime(2026, 8, 27, 7, 30, tzinfo=UTC),
        condition=WeightMeasurementCondition.FASTING,
        idempotency_key=idempotency_key,
        source_turn_id=turn_id,
        source_item_id=item_id,
        source_tool_call_id=tool_call_id,
    )


@pytest.mark.parametrize(
    ("value", "unit", "expected_grams"),
    (
        ("77.6", WeightUnit.KG, 77_600),
        ("155.2", WeightUnit.JIN, 77_600),
        ("170", WeightUnit.LB, 77_111),
    ),
)
def test_weight_units_are_converted_deterministically(
    value: str,
    unit: WeightUnit,
    expected_grams: int,
) -> None:
    measurement = WeightMeasurementCommand(
        user_id="user-1",
        value=Decimal(value),
        unit=unit,
        measured_at=datetime.now(UTC),
        idempotency_key="key-1",
        source_turn_id="turn-1",
        source_tool_call_id="call-1",
    )

    assert measurement.weight_grams == expected_grams


def test_weight_command_rejects_unsafe_range_and_naive_time() -> None:
    with pytest.raises(ValidationError, match="between 10kg and 500kg"):
        WeightMeasurementCommand(
            user_id="user-1",
            value=Decimal("776"),
            unit=WeightUnit.KG,
            measured_at=datetime.now(UTC),
            idempotency_key="key-1",
            source_turn_id="turn-1",
            source_tool_call_id="call-1",
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        WeightMeasurementCommand(
            user_id="user-1",
            value=Decimal("77.6"),
            unit=WeightUnit.KG,
            measured_at=datetime(2026, 8, 27, 7, 30),
            idempotency_key="key-1",
            source_turn_id="turn-1",
            source_tool_call_id="call-1",
        )


async def test_record_is_idempotent_and_keeps_original_measurement(tmp_path) -> None:
    database, repository, turn_id, item_id, user_id = await prepare_weight_repository(
        tmp_path
    )
    measurement = command(
        user_id=user_id,
        turn_id=turn_id,
        item_id=item_id,
        value="155.2",
        unit=WeightUnit.JIN,
    )
    try:
        first = await repository.record(measurement)
        repeated = await repository.record(measurement)
        loaded = await repository.get(first.record.id)

        assert first.created is True
        assert repeated.created is False
        assert repeated.record == first.record
        assert loaded == first.record
        assert first.record.weight_grams == 77_600
        assert first.record.weight_kg == Decimal("77.6")
        assert first.record.original_value == "155.2"
        assert first.record.original_unit is WeightUnit.JIN
        assert first.record.source_turn_id == turn_id
        assert first.record.source_item_id == item_id
    finally:
        await database.close()


async def test_idempotency_collision_cannot_overwrite_weight(tmp_path) -> None:
    database, repository, turn_id, item_id, user_id = await prepare_weight_repository(
        tmp_path
    )
    try:
        await repository.record(
            command(user_id=user_id, turn_id=turn_id, item_id=item_id)
        )

        with pytest.raises(WeightRecordCollision, match="idempotency collision"):
            await repository.record(
                command(
                    user_id=user_id,
                    turn_id=turn_id,
                    item_id=item_id,
                    value="76.9",
                )
            )
    finally:
        await database.close()


async def test_recent_trend_is_code_calculated_and_newest_first(tmp_path) -> None:
    database, repository, turn_id, item_id, user_id = await prepare_weight_repository(
        tmp_path
    )
    first_time = datetime(2026, 8, 25, 7, 30, tzinfo=UTC)
    try:
        for index, value in enumerate(("77.5", "77.0", "76.8")):
            await repository.record(
                command(
                    user_id=user_id,
                    turn_id=turn_id,
                    item_id=item_id,
                    value=value,
                    measured_at=first_time + timedelta(days=index),
                    idempotency_key=f"weight-execution-{index}",
                    tool_call_id=f"call-{index}",
                )
            )

        trend = await repository.recent_trend(user_id)

        assert [record.weight_kg for record in trend.records] == [
            Decimal("76.8"),
            Decimal("77"),
            Decimal("77.5"),
        ]
        assert trend.current == trend.records[0]
        assert trend.previous == trend.records[1]
        assert trend.change_kg == Decimal("-0.2")
        assert trend.direction is WeightTrendDirection.DOWN
    finally:
        await database.close()


async def test_source_turn_must_belong_to_weight_owner(tmp_path) -> None:
    database, repository, turn_id, item_id, _ = await prepare_weight_repository(tmp_path)
    try:
        with pytest.raises(WeightSourceMismatch, match="another user"):
            await repository.record(
                command(user_id="user-2", turn_id=turn_id, item_id=item_id)
            )
    finally:
        await database.close()
