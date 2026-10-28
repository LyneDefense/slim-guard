"""Atomic direct edits with ownership, revision and retry protection."""

import hashlib
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from slim_guard.db.models import MealRecord
from slim_guard.db.session import Database
from slim_guard.features.health_records.content import MealContent
from slim_guard.features.health_records.meal.editing import MealEditRequest, edit_patch
from slim_guard.features.health_records.mutations import apply_record_patch
from slim_guard.features.health_records.persistence.edit_sources import MealEditSource
from slim_guard.features.health_records.persistence.projection import row_values, to_view
from slim_guard.features.health_records.references import RecordConflict, RecordError
from slim_guard.features.health_records.views import RecordView


class ManualMealEditor:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def edit_meal(self, user_id: str, record_id: str, request: MealEditRequest) -> RecordView:
        source_id = str(uuid5(NAMESPACE_URL, f"{user_id}/meal-edit/{request.operation_id}"))
        fingerprint = hashlib.sha256((record_id + request.model_dump_json()).encode()).hexdigest()
        try:
            async with self.database.session() as session, session.begin():
                previous = await session.get(MealEditSource, source_id)
                if previous:
                    return self._replay(previous, user_id, fingerprint)
                row = await session.scalar(
                    select(MealRecord)
                    .where(
                        MealRecord.id == record_id,
                        MealRecord.user_id == user_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise RecordError("Record not found for this user")
                if row.revision != request.expected_revision:
                    # An identical request may have committed while this request
                    # waited for the row lock. Replay that receipt before rejecting
                    # a genuinely stale edit.
                    previous = await session.get(MealEditSource, source_id)
                    if previous:
                        return self._replay(previous, user_id, fingerprint)
                current = to_view("meal", row)
                assert isinstance(current.content, MealContent)
                patch, new_ids = edit_patch(current.content, request, source_id=source_id)
                allocated = iter(new_ids)
                result = apply_record_patch(
                    current,
                    patch,
                    expected_revision=request.expected_revision,
                    now=datetime.now(UTC),
                    new_id=lambda: next(allocated),
                )
                effect = await session.execute(
                    update(MealRecord)
                    .where(
                        MealRecord.id == record_id,
                        MealRecord.user_id == user_id,
                        MealRecord.revision == request.expected_revision,
                    )
                    .values(**row_values(result.record))
                    .returning(MealRecord.id)
                )
                if effect.scalar_one_or_none() is None:
                    previous = await session.get(MealEditSource, source_id)
                    if previous:
                        return self._replay(previous, user_id, fingerprint)
                    raise RecordConflict("Record revision changed")
                session.add(
                    MealEditSource(
                        id=source_id,
                        user_id=user_id,
                        record_id=record_id,
                        command_hash=fingerprint,
                        input_json=request.model_dump_json(),
                        result_json=result.record.model_dump_json(),
                    )
                )
                await session.flush()
                return result.record
        except IntegrityError as exc:
            async with self.database.session() as session:
                previous = await session.get(MealEditSource, source_id)
                if previous:
                    return self._replay(previous, user_id, fingerprint)
            raise RecordConflict("Edit conflicted; reload the meal") from exc

    @staticmethod
    def _replay(row: MealEditSource, user_id: str, fingerprint: str) -> RecordView:
        if row.user_id != user_id or row.command_hash != fingerprint:
            raise RecordConflict("Edit identity reused with different contents")
        return RecordView.model_validate_json(row.result_json)
