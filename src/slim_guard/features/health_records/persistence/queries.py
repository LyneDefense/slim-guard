import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, case, cast, event, func, or_, select

from slim_guard.db.models import AgentItemRecord
from slim_guard.db.session import Database
from slim_guard.features.health_records.persistence.projection import RECORD_MODELS, to_view
from slim_guard.features.health_records.queries import RecordPage, RecordQuery
from slim_guard.features.health_records.references import RecordError


def install_sqlite_dates(database: Database) -> None:
    if database.engine.dialect.name != "sqlite":
        return

    def register(connection: Any, _: Any) -> None:
        def local_date(value: str | None, timezone: str) -> str | None:
            if value is None:
                return None
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            return instant.astimezone(ZoneInfo(timezone)).date().isoformat()

        connection.create_function("sg_local_date", 2, local_date, deterministic=True)

    event.listen(database.engine.sync_engine, "connect", register)


class HealthRecordQueries:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def search(self, user_id: str, query: RecordQuery) -> RecordPage:
        model = RECORD_MODELS[query.kind]
        time = model.occurred_at if query.kind in {"meal", "exercise"} else model.measured_at

        def local_day(column: Any) -> Any:
            if self.database.engine.dialect.name == "sqlite":
                return func.sg_local_date(column, query.timezone)
            return cast(func.date(func.timezone(query.timezone, column)), String)

        day = func.coalesce(
            case(
                (model.time_precision == "date", cast(model.occurred_on, String)),
                else_=local_day(time),
            ),
            "",
        )
        rank = case((model.time_precision == "datetime", 1), else_=0)
        instant = func.coalesce(cast(time, String), "")
        created = cast(model.created_at, String)
        keys = tuple(
            column.label(f"history_key_{index}")
            for index, column in enumerate((day, rank, instant, created, model.id))
        )
        stmt = select(model, *keys).where(model.user_id == user_id, model.status == query.status)
        if query.from_date:
            stmt = stmt.where(day >= query.from_date.isoformat())
        if query.to_date:
            stmt = stmt.where(day != "", day <= query.to_date.isoformat())
        if query.meal_type:
            stmt = stmt.where(model.meal_type == query.meal_type)
        if query.text:
            column = (
                model.foods_json
                if query.kind == "meal"
                else model.activity_name
                if query.kind == "exercise"
                else model.original_value
            )
            stmt = stmt.where(column.contains(query.text, autoescape=True))
        if query.source_ref:
            stmt = stmt.where(
                or_(
                    model.source_item_id == query.source_ref,
                    model.source_refs_json.contains(json.dumps(query.source_ref), autoescape=True),
                )
            )
        if query.source_from or query.source_to:
            stmt = stmt.join(AgentItemRecord, AgentItemRecord.id == model.source_item_id)
            source_day = local_day(AgentItemRecord.created_at)
            if query.source_from:
                stmt = stmt.where(source_day >= query.source_from.isoformat())
            if query.source_to:
                stmt = stmt.where(source_day <= query.source_to.isoformat())
        fingerprint = hashlib.sha256(
            (user_id + query.model_dump_json(exclude={"cursor", "limit"})).encode()
        ).hexdigest()
        if query.cursor:
            try:
                payload = json.loads(base64.urlsafe_b64decode(query.cursor.encode()))
                last = payload["keys"]
                if payload["filter"] != fingerprint or len(last) != len(keys):
                    raise ValueError("Cursor does not match the query")
                stmt = stmt.where(
                    or_(
                        *(
                            and_(*(keys[j] == last[j] for j in range(i)), keys[i] < last[i])
                            for i in range(len(keys))
                        )
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise RecordError("Invalid history cursor") from exc
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    stmt.order_by(*(key.desc() for key in keys)).limit(query.limit + 1)
                )
            ).all()
        more = len(rows) > query.limit
        rows = rows[: query.limit]
        cursor = None
        if more:
            cursor = base64.urlsafe_b64encode(
                json.dumps({"filter": fingerprint, "keys": list(rows[-1][1:])}).encode()
            ).decode()
        return RecordPage(
            records=tuple(to_view(query.kind, row[0]) for row in rows),
            next_cursor=cursor,
            has_more=more,
        )
