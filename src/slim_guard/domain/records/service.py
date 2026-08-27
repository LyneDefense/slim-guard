from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select

from slim_guard.db.models import ExerciseRecord, MealRecord, WeightRecord
from slim_guard.db.session import Database


class RecordKind(StrEnum):
    WEIGHT = "weight"
    MEAL = "meal"
    EXERCISE = "exercise"


class RecordStatusAction(StrEnum):
    VOID = "void"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class RecordStatusResult:
    record_id: str
    record_kind: RecordKind
    status: str
    changed: bool


class UserRecordStatusService:
    """Voids or restores one user-owned authoritative record without deleting history."""

    _MODELS = {
        RecordKind.WEIGHT: WeightRecord,
        RecordKind.MEAL: MealRecord,
        RecordKind.EXERCISE: ExerciseRecord,
    }

    def __init__(self, database: Database) -> None:
        self._database = database

    async def apply(
        self,
        *,
        user_id: str,
        record_kind: RecordKind,
        record_id: str,
        action: RecordStatusAction,
    ) -> RecordStatusResult | None:
        model: Any = self._MODELS[record_kind]
        target_status = "voided" if action is RecordStatusAction.VOID else "active"
        async with self._database.session() as session, session.begin():
            row = await session.scalar(
                select(model).where(model.id == record_id, model.user_id == user_id)
            )
            if row is None:
                return None
            if row.status == "superseded":
                raise ValueError("A superseded record cannot change status directly")
            changed = row.status != target_status
            if changed:
                row.status = target_status
                await session.flush()
            return RecordStatusResult(
                record_id=row.id,
                record_kind=record_kind,
                status=row.status,
                changed=changed,
            )
