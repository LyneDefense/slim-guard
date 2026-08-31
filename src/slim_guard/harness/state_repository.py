from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
from slim_guard.harness.errors import (
    InvalidTurnTransition,
    ItemStateConflict,
    TurnNotWritable,
    TurnStateConflict,
)
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
    step_count: int = 0


@dataclass(frozen=True, slots=True)
class ItemRef:
    id: str
    turn_id: str
    sequence: int
    item_type: ItemType
    status: ItemStatus
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NewTurnItem:
    item_type: ItemType
    status: ItemStatus
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StoredTurnStart:
    thread: ThreadRef
    turn: TurnRef
    items: tuple[ItemRef, ...]


class TurnStateStore(Protocol):
    async def get_thread(self, thread_id: str) -> ThreadRef | None: ...

    async def get_turn(self, turn_id: str) -> TurnRef | None: ...

    async def transition_turn(
        self,
        *,
        turn_id: str,
        target: TurnStatus,
        expected: TurnStatus | None = None,
        step_count: int | None = None,
    ) -> TurnRef: ...


class HarnessRunStore(TurnStateStore, Protocol):
    async def append_item(
        self,
        *,
        turn_id: str,
        item_type: ItemType,
        status: ItemStatus,
        payload: Mapping[str, Any],
    ) -> ItemRef: ...

    async def finish_item(
        self,
        *,
        item_id: str,
        status: ItemStatus,
        payload: Mapping[str, Any],
    ) -> ItemRef: ...


class TurnInitializationStore(Protocol):
    async def start_turn_with_items(
        self,
        *,
        user_id: str,
        agent_version_id: str,
        trigger: TurnTrigger,
        items: Sequence[NewTurnItem],
        deadline_at: datetime | None = None,
    ) -> StoredTurnStart: ...


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
        started = await self.start_turn_with_items(
            user_id=user_id,
            agent_version_id=agent_version_id,
            trigger=trigger,
            items=(),
            deadline_at=deadline_at,
        )
        return started.turn

    async def start_turn_with_items(
        self,
        *,
        user_id: str,
        agent_version_id: str,
        trigger: TurnTrigger,
        items: Sequence[NewTurnItem],
        deadline_at: datetime | None = None,
    ) -> StoredTurnStart:
        serialized_items = [(item, self._payload_json(item.payload)) for item in items]
        async with self.database.session() as session, session.begin():
            thread_row = await session.scalar(
                select(AgentThreadRecord).where(AgentThreadRecord.user_id == user_id)
            )
            if thread_row is None:
                thread_row = AgentThreadRecord(
                    user_id=user_id,
                    status=ThreadStatus.ACTIVE.value,
                )
                session.add(thread_row)
                await session.flush()
            turn_row = AgentTurnRecord(
                thread_id=thread_row.id,
                agent_version_id=agent_version_id,
                trigger_type=trigger.value,
                status=TurnStatus.RUNNING.value,
                deadline_at=deadline_at,
            )
            session.add(turn_row)
            await session.flush()
            item_rows = [
                AgentItemRecord(
                    thread_id=thread_row.id,
                    turn_id=turn_row.id,
                    sequence=sequence,
                    item_type=item.item_type.value,
                    status=item.status.value,
                    payload_json=payload_json,
                )
                for sequence, (item, payload_json) in enumerate(serialized_items, start=1)
            ]
            session.add_all(item_rows)
            thread_row.last_active_at = utc_now()
            await session.flush()
            return StoredTurnStart(
                thread=self._thread_ref(thread_row),
                turn=self._turn_ref(turn_row),
                items=tuple(self._item_ref(row) for row in item_rows),
            )

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

    async def finish_item(
        self,
        *,
        item_id: str,
        status: ItemStatus,
        payload: Mapping[str, Any],
    ) -> ItemRef:
        if status is ItemStatus.STARTED:
            raise ValueError("A finished item must be completed or failed")
        payload_json = self._payload_json(payload)
        async with self.database.session() as session, session.begin():
            row = await session.get(AgentItemRecord, item_id)
            if row is None:
                raise LookupError(f"Agent item not found: {item_id}")
            current = ItemStatus(row.status)
            if current is status and row.payload_json == payload_json:
                return self._item_ref(row)
            if current is not ItemStatus.STARTED:
                raise ItemStateConflict(
                    f"Cannot finish item {item_id} from state {current.value} as {status.value}"
                )
            result = await session.execute(
                update(AgentItemRecord)
                .where(
                    AgentItemRecord.id == item_id,
                    AgentItemRecord.status == ItemStatus.STARTED.value,
                )
                .values(status=status.value, payload_json=payload_json)
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ItemStateConflict(f"Agent item {item_id} changed concurrently")
            await session.refresh(row)
            return self._item_ref(row)

    async def get_thread(self, thread_id: str) -> ThreadRef | None:
        async with self.database.session() as session:
            row = await session.get(AgentThreadRecord, thread_id)
            return self._thread_ref(row) if row is not None else None

    async def transition_turn(
        self,
        *,
        turn_id: str,
        target: TurnStatus,
        expected: TurnStatus | None = None,
        step_count: int | None = None,
    ) -> TurnRef:
        if step_count is not None and step_count < 0:
            raise ValueError("Turn step count cannot be negative")
        async with self.database.session() as session, session.begin():
            row = await session.get(AgentTurnRecord, turn_id)
            if row is None:
                raise LookupError(f"Agent turn not found: {turn_id}")
            current = TurnStatus(row.status)
            if current is target:
                if step_count is not None and row.step_count != step_count:
                    row.step_count = step_count
                    row.updated_at = utc_now()
                    await session.flush()
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
                    **({"step_count": step_count} if step_count is not None else {}),
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
            step_count=row.step_count,
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
