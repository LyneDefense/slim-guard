from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.harness.errors import InvalidTurnTransition, TurnNotWritable, TurnStateConflict
from slim_guard.harness.events import ItemStatus, ItemType, ThreadStatus, TurnStatus, TurnTrigger

_TERMINAL_TURN_STATUSES = frozenset(
    {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.SUSPENDED}
)
_ALLOWED_TURN_TRANSITIONS: dict[TurnStatus, frozenset[TurnStatus]] = {
    TurnStatus.RUNNING: frozenset(
        {
            TurnStatus.WAITING_USER_CONFIRMATION,
            TurnStatus.WAITING_HUMAN_REVIEW,
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.SUSPENDED,
        }
    ),
    TurnStatus.WAITING_USER_CONFIRMATION: frozenset(
        {
            TurnStatus.RUNNING,
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.SUSPENDED,
        }
    ),
    TurnStatus.WAITING_HUMAN_REVIEW: frozenset(
        {
            TurnStatus.RUNNING,
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.SUSPENDED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ThreadRef:
    id: str
    user_id: str
    status: ThreadStatus


@dataclass(frozen=True, slots=True)
class TurnRef:
    id: str
    thread_id: str
    agent_version_id: str
    trigger: TurnTrigger
    status: TurnStatus
    deadline_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ItemRef:
    id: str
    turn_id: str
    sequence: int
    item_type: ItemType
    status: ItemStatus
    payload: dict[str, Any]


class TurnStateStore(Protocol):
    async def get_turn(self, turn_id: str) -> TurnRef | None: ...

    async def transition_turn(
        self,
        *,
        turn_id: str,
        target: TurnStatus,
        expected: TurnStatus | None = None,
    ) -> TurnRef: ...


class HarnessStateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_or_create_thread(self, user_id: str) -> ThreadRef:
        async with self.database.session() as session, session.begin():
            thread = await session.scalar(
                select(AgentThreadRecord).where(AgentThreadRecord.user_id == user_id)
            )
            if thread is None:
                thread = AgentThreadRecord(user_id=user_id, status=ThreadStatus.ACTIVE.value)
                session.add(thread)
                await session.flush()
            return self._thread_ref(thread)

    async def start_turn(
        self,
        *,
        user_id: str,
        agent_version_id: str,
        trigger: TurnTrigger,
        deadline_at: datetime | None = None,
    ) -> TurnRef:
        thread = await self.get_or_create_thread(user_id)
        async with self.database.session() as session, session.begin():
            row = AgentTurnRecord(
                thread_id=thread.id,
                agent_version_id=agent_version_id,
                trigger_type=trigger.value,
                status=TurnStatus.RUNNING.value,
                deadline_at=deadline_at,
            )
            session.add(row)
            stored_thread = await session.get(AgentThreadRecord, thread.id)
            assert stored_thread is not None
            stored_thread.last_active_at = utc_now()
            await session.flush()
            return self._turn_ref(row)

    async def append_item(
        self,
        *,
        turn_id: str,
        item_type: ItemType,
        status: ItemStatus,
        payload: Mapping[str, Any],
    ) -> ItemRef:
        async with self.database.session() as session, session.begin():
            turn = await session.get(AgentTurnRecord, turn_id)
            if turn is None:
                raise LookupError(f"Agent turn not found: {turn_id}")
            turn_status = TurnStatus(turn.status)
            if turn_status is not TurnStatus.RUNNING:
                raise TurnNotWritable(
                    f"Cannot append an item to turn {turn_id} in state {turn_status.value}"
                )
            last_sequence = await session.scalar(
                select(func.max(AgentItemRecord.sequence)).where(
                    AgentItemRecord.turn_id == turn_id
                )
            )
            row = AgentItemRecord(
                thread_id=turn.thread_id,
                turn_id=turn.id,
                sequence=(last_sequence or 0) + 1,
                item_type=item_type.value,
                status=status.value,
                payload_json=self._payload_json(payload),
            )
            session.add(row)
            await session.flush()
            return self._item_ref(row)

    async def get_turn(self, turn_id: str) -> TurnRef | None:
        async with self.database.session() as session:
            row = await session.get(AgentTurnRecord, turn_id)
            return self._turn_ref(row) if row is not None else None

    async def transition_turn(
        self,
        *,
        turn_id: str,
        target: TurnStatus,
        expected: TurnStatus | None = None,
    ) -> TurnRef:
        async with self.database.session() as session, session.begin():
            row = await session.get(AgentTurnRecord, turn_id)
            if row is None:
                raise LookupError(f"Agent turn not found: {turn_id}")
            current = TurnStatus(row.status)
            if current is target:
                return self._turn_ref(row)
            if expected is not None and current is not expected:
                raise TurnStateConflict(
                    f"Expected turn {turn_id} to be {expected.value}, found {current.value}"
                )
            if target not in _ALLOWED_TURN_TRANSITIONS.get(current, frozenset()):
                raise InvalidTurnTransition(
                    f"Cannot transition turn {turn_id} from {current.value} to {target.value}"
                )

            transitioned_at = utc_now()
            result = await session.execute(
                update(AgentTurnRecord)
                .where(AgentTurnRecord.id == turn_id, AgentTurnRecord.status == current.value)
                .values(
                    status=target.value,
                    updated_at=transitioned_at,
                    completed_at=(
                        transitioned_at if target in _TERMINAL_TURN_STATUSES else None
                    ),
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise TurnStateConflict(f"Turn {turn_id} changed concurrently")
            await session.refresh(row)
            thread = await session.get(AgentThreadRecord, row.thread_id)
            assert thread is not None
            thread.last_active_at = transitioned_at
            return self._turn_ref(row)

    async def list_items(self, turn_id: str) -> list[ItemRef]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(AgentItemRecord)
                .where(AgentItemRecord.turn_id == turn_id)
                .order_by(AgentItemRecord.sequence)
            )
            return [self._item_ref(row) for row in rows]

    @staticmethod
    def _payload_json(payload: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _thread_ref(row: AgentThreadRecord) -> ThreadRef:
        return ThreadRef(id=row.id, user_id=row.user_id, status=ThreadStatus(row.status))

    @staticmethod
    def _turn_ref(row: AgentTurnRecord) -> TurnRef:
        return TurnRef(
            id=row.id,
            thread_id=row.thread_id,
            agent_version_id=row.agent_version_id,
            trigger=TurnTrigger(row.trigger_type),
            status=TurnStatus(row.status),
            deadline_at=row.deadline_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _item_ref(row: AgentItemRecord) -> ItemRef:
        payload = json.loads(row.payload_json)
        if not isinstance(payload, dict):
            raise ValueError(f"Agent item payload is not an object: {row.id}")
        return ItemRef(
            id=row.id,
            turn_id=row.turn_id,
            sequence=row.sequence,
            item_type=ItemType(row.item_type),
            status=ItemStatus(row.status),
            payload=payload,
        )
