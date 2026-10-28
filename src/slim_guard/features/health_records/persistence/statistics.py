"""Database aggregates, not counts of a limited history page."""

from datetime import date

from sqlalchemy import case, func, select

from slim_guard.db.session import Database
from slim_guard.features.health_records.persistence.calendar import record_day
from slim_guard.features.health_records.persistence.projection import RECORD_MODELS
from slim_guard.features.health_records.statistics import (
    DailyMeasurement,
    RecordCounts,
    RecordPeriod,
    RecordPeriodStatistics,
)


async def record_statistics(
    database: Database, user_id: str, period: RecordPeriod
) -> RecordPeriodStatistics:
    counts: dict[str, int] = {}
    measurements: dict[str, tuple[DailyMeasurement, ...]] = {}
    async with database.session() as session:
        for kind, model in RECORD_MODELS.items():
            day = record_day(model, kind, period.timezone, database.engine.dialect.name)
            filters = (
                model.user_id == user_id,
                model.status == "active",
                day >= period.from_date.isoformat(),
                day <= period.to_date.isoformat(),
            )
            counts[kind] = (
                await session.scalar(select(func.count()).select_from(model).where(*filters)) or 0
            )
            if kind not in {"weight", "body_fat"}:
                continue
            time = model.measured_at
            value = model.weight_grams if kind == "weight" else model.body_fat_basis_points
            # Prefer actual measurement time. Date-only measurements remain date-only;
            # ties (or a day containing only date-only values) use recording order.
            ranked = (
                select(
                    model.id.label("id"),
                    day.label("day"),
                    value.label("value"),
                    func.row_number()
                    .over(
                        partition_by=day,
                        order_by=(
                            case((model.time_precision == "datetime", 1), else_=0).desc(),
                            time.desc().nulls_last(),
                            model.created_at.desc(),
                            model.id.desc(),
                        ),
                    )
                    .label("position"),
                )
                .where(*filters)
                .subquery()
            )
            rows = (
                await session.execute(
                    select(ranked.c.id, ranked.c.day, ranked.c.value)
                    .where(ranked.c.position == 1)
                    .order_by(ranked.c.day)
                )
            ).all()
            measurements[kind] = tuple(
                DailyMeasurement(
                    record_id=row.id,
                    on=date.fromisoformat(row.day),
                    value=row.value / (1000 if kind == "weight" else 100),
                    unit="kg" if kind == "weight" else "%",
                )
                for row in rows
            )
    return RecordPeriodStatistics(
        period=period,
        counts=RecordCounts.model_validate(counts),
        weights=measurements["weight"],
        body_fat=measurements["body_fat"],
    )
