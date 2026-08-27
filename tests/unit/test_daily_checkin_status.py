from __future__ import annotations

from datetime import UTC, date, datetime

from slim_guard.db.models import MealRecord, SlimGuardUser, WeightRecord
from slim_guard.db.session import Database
from slim_guard.domain.routine.status import DailyCheckinStatusRepository


async def test_daily_status_uses_user_local_day_and_active_records_only(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'daily-status.sqlite3'}")
    await database.create_schema()
    now = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now))
        session.add_all(
            (
                WeightRecord(
                    user_id="user-1",
                    weight_grams=77_600,
                    original_value="77.6",
                    original_unit="kg",
                    measured_at=now,
                    measurement_condition="fasting",
                    status="active",
                    idempotency_key="weight-inside",
                    source_turn_id="turn-1",
                    source_tool_call_id="call-1",
                ),
                WeightRecord(
                    user_id="user-1",
                    weight_grams=77_700,
                    original_value="77.7",
                    original_unit="kg",
                    measured_at=datetime(2026, 8, 26, 15, 30, tzinfo=UTC),
                    measurement_condition="unspecified",
                    status="active",
                    idempotency_key="weight-previous-local-day",
                    source_turn_id="turn-1",
                    source_tool_call_id="call-2",
                ),
                MealRecord(
                    user_id="user-1",
                    meal_type="breakfast",
                    foods_json='[{"name":"鸡蛋","portion":"一个"}]',
                    occurred_at=now,
                    status="active",
                    idempotency_key="meal-inside",
                    source_turn_id="turn-1",
                    source_tool_call_id="call-3",
                ),
                MealRecord(
                    user_id="user-1",
                    meal_type="snack",
                    foods_json='[{"name":"苹果"}]',
                    occurred_at=now,
                    status="voided",
                    idempotency_key="meal-voided",
                    source_turn_id="turn-1",
                    source_tool_call_id="call-4",
                ),
            )
        )
    try:
        status = await DailyCheckinStatusRepository(database).get(
            user_id="user-1",
            local_date=date(2026, 8, 27),
            timezone="Asia/Shanghai",
        )

        assert status.weight_count == 1
        assert status.meal_count == 1
        assert status.exercise_count == 0
        assert status.has_weight is True
        assert status.has_meal is True
    finally:
        await database.close()
