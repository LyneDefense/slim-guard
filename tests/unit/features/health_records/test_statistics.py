from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from slim_guard.db.models import (
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    BodyFatRecord,
    ExerciseRecord,
    MealRecord,
    SlimGuardUser,
    WeightRecord,
)
from slim_guard.db.session import Database
from slim_guard.features.health_records.statistics import RecordPeriod
from slim_guard.mobile.period_progress import period_progress
from slim_guard.services.record_changes import build_record_service

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


@pytest.fixture
async def statistics_db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'statistics.sqlite'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add_all(
            [
                SlimGuardUser(id=identity, first_seen_at=NOW, last_seen_at=NOW)
                for identity in ("u1", "u2")
            ]
        )
        session.add(AgentVersionRecord(id="v", manifest_json="{}", code_revision="test"))
        await session.flush()
        session.add(AgentThreadRecord(id="thread", user_id="u1"))
        await session.flush()
        session.add(
            AgentTurnRecord(
                id="turn", thread_id="thread", agent_version_id="v", trigger_type="user_message"
            )
        )
    yield database
    await database.close()


def row(kind, identity, *, on=TODAY, at=None, user="u1", status="active", value=None, created=NOW):
    common = dict(
        id=identity,
        user_id=user,
        status=status,
        idempotency_key=identity,
        source_turn_id="turn",
        source_tool_call_id=identity,
        created_at=created,
        time_precision="datetime" if at else "date" if on else "unknown",
        occurred_on=on if at is None else None,
        timezone="Asia/Shanghai",
        time_basis="user_reported" if at or on else "unknown",
    )
    if kind == "meal":
        return MealRecord(**common, meal_type="lunch", foods_json="[]", occurred_at=at)
    if kind == "exercise":
        return ExerciseRecord(**common, activity_name="跑步", occurred_at=at, duration_minutes=30)
    if kind == "weight":
        grams = value or 79000
        return WeightRecord(
            **common,
            weight_grams=grams,
            original_value=str(grams / 1000),
            original_unit="kg",
            measurement_condition="unspecified",
            measured_at=at,
        )
    bps = value or 2300
    return BodyFatRecord(
        **common, body_fat_basis_points=bps, original_value=str(bps / 100), measured_at=at
    )


async def test_period_counts_are_complete_and_isolate_user_status_and_dates(statistics_db):
    async with statistics_db.session() as session, session.begin():
        session.add_all([row("meal", f"meal-{i}") for i in range(145)])
        session.add_all(
            [
                row("meal", "boundary-7", on=TODAY - timedelta(days=6)),
                row("meal", "outside-7", on=TODAY - timedelta(days=7)),
                row("meal", "boundary-30", on=TODAY - timedelta(days=29)),
                row("meal", "outside-30", on=TODAY - timedelta(days=30)),
                row("meal", "future", on=TODAY + timedelta(days=1)),
                row("meal", "unknown", on=None),
                row("meal", "deleted", status="voided"),
                row("meal", "old-version", status="superseded"),
                row("meal", "foreign", user="u2"),
                row("exercise", "exercise"),
                row("weight", "weight"),
                row("body_fat", "fat"),
            ]
        )
    service = build_record_service(statistics_db)
    seven = await period_progress(service, "u1", days=7, timezone="Asia/Shanghai", now=NOW)
    thirty = await period_progress(service, "u1", days=30, timezone="Asia/Shanghai", now=NOW)
    assert seven.counts.model_dump() == {"weight": 1, "body_fat": 1, "meal": 146, "exercise": 1}
    assert thirty.counts.meal == 148
    assert seven.from_date == TODAY - timedelta(days=6)
    assert thirty.from_date == TODAY - timedelta(days=29)
    assert seven.to_date == thirty.to_date == TODAY
    assert seven.weights[0].value == 79.0
    assert seven.body_fat[0].value == 23.0


async def test_statistics_respect_local_midnight_and_date_only_values(statistics_db):
    async with statistics_db.session() as session, session.begin():
        session.add_all(
            [
                row("meal", "before", at=datetime(2026, 10, 21, 15, 59, 59, tzinfo=UTC)),
                row("meal", "start", at=datetime(2026, 10, 21, 16, tzinfo=UTC)),
                row("meal", "start-again", at=datetime(2026, 10, 21, 16, 1, tzinfo=UTC)),
                row("meal", "end", at=datetime(2026, 10, 28, 15, 59, 59, tzinfo=UTC)),
                row("meal", "after", at=datetime(2026, 10, 28, 16, tzinfo=UTC)),
                row("meal", "date-only", on=date(2026, 10, 22)),
            ]
        )
    service = build_record_service(statistics_db)
    result = await period_progress(service, "u1", days=7, timezone="Asia/Shanghai", now=NOW)
    assert result.counts.meal == 4
    utc = await period_progress(service, "u1", days=7, timezone="UTC", now=NOW)
    assert utc.counts.meal == 3
    # Shifting the clock across midnight changes the local window, not its length.
    next_day = await period_progress(
        service, "u1", days=7, timezone="Asia/Shanghai", now=datetime(2026, 10, 28, 16, tzinfo=UTC)
    )
    assert next_day.to_date == TODAY + timedelta(days=1)
    assert next_day.from_date == date(2026, 10, 23)
    assert next_day.counts.meal == 2


async def test_daily_trend_uses_latest_measurement_without_reducing_record_counts(statistics_db):
    async with statistics_db.session() as session, session.begin():
        session.add_all(
            [
                row(
                    "weight",
                    "late-entered-earlier",
                    at=datetime(2026, 10, 28, 1, tzinfo=UTC),
                    value=81000,
                    created=NOW + timedelta(hours=1),
                ),
                row("weight", "latest", at=datetime(2026, 10, 28, 3, tzinfo=UTC), value=79000),
                row("weight", "date-only-today", value=90000, created=NOW + timedelta(hours=2)),
                row("weight", "yesterday-first", on=TODAY - timedelta(days=1), value=83000),
                row(
                    "weight",
                    "yesterday-last",
                    on=TODAY - timedelta(days=1),
                    value=82000,
                    created=NOW + timedelta(seconds=1),
                ),
                row(
                    "weight",
                    "deleted-latest",
                    at=datetime(2026, 10, 28, 4, tzinfo=UTC),
                    status="voided",
                    value=70000,
                ),
            ]
        )
    result = await period_progress(
        build_record_service(statistics_db), "u1", days=7, timezone="Asia/Shanghai", now=NOW
    )
    assert result.counts.weight == 5
    assert [(item.record_id, item.on, item.value) for item in result.weights] == [
        ("yesterday-last", TODAY - timedelta(days=1), 82.0),
        ("latest", TODAY, 79.0),
    ]
    assert result.model_dump(mode="json")["weights"][0]["on"] == "2026-10-27"


async def test_empty_period_is_real_zero_not_missing_or_nan(statistics_db):
    result = await period_progress(
        build_record_service(statistics_db),
        "u1",
        days=7,
        timezone="America/New_York",
        now=datetime(2026, 11, 2, 2, tzinfo=UTC),
    )
    assert result.to_date == date(2026, 11, 1)
    assert (result.to_date - result.from_date).days == 6
    assert all(value == 0 for value in result.counts.model_dump().values())
    assert result.weights == result.body_fat == ()


async def test_daylight_saving_repeated_hour_does_not_change_calendar_window(statistics_db):
    async with statistics_db.session() as session, session.begin():
        session.add_all(
            [
                row("meal", "dst-before", at=datetime(2026, 10, 26, 3, 59, tzinfo=UTC)),
                row("meal", "dst-start", at=datetime(2026, 10, 26, 4, tzinfo=UTC)),
                row(
                    "weight",
                    "dst-first-hour",
                    at=datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
                    value=80000,
                ),
                row(
                    "weight",
                    "dst-second-hour",
                    at=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
                    value=79000,
                ),
            ]
        )
    result = await period_progress(
        build_record_service(statistics_db),
        "u1",
        days=7,
        timezone="America/New_York",
        now=datetime(2026, 11, 2, 2, tzinfo=UTC),
    )
    assert result.from_date == date(2026, 10, 26)
    assert result.to_date == date(2026, 11, 1)
    assert result.counts.meal == 1
    assert result.counts.weight == 2
    assert result.weights[0].record_id == "dst-second-hour"
    assert result.weights[0].value == 79.0


@pytest.mark.parametrize("timezone", ["not-a-timezone", "", "../UTC"])
def test_invalid_statistics_timezone(timezone):
    with pytest.raises(ValidationError):
        RecordPeriod(from_date=TODAY, to_date=TODAY, timezone=timezone)


def test_reversed_statistics_dates():
    with pytest.raises(ValidationError):
        RecordPeriod(from_date=TODAY, to_date=TODAY - timedelta(days=1), timezone="UTC")
