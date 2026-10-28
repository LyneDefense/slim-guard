"""SQL calendar-day projection shared by record history and statistics."""

from typing import Any

from sqlalchemy import String, case, cast, func


def local_day(column: Any, timezone: str, dialect: str) -> Any:
    if dialect == "sqlite":
        return func.sg_local_date(column, timezone)
    return cast(func.date(func.timezone(timezone, column)), String)


def record_day(model: Any, kind: str, timezone: str, dialect: str) -> Any:
    time = model.occurred_at if kind in {"meal", "exercise"} else model.measured_at
    return case(
        (model.time_precision == "date", cast(model.occurred_on, String)),
        (model.time_precision == "datetime", local_day(time, timezone, dialect)),
        else_=None,
    )
