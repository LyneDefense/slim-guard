from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import exists, select

from slim_guard.db.models import AgentItemRecord, AgentThreadRecord, AgentTurnRecord
from slim_guard.db.session import Database


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    turn_id: str
    messages: tuple[DialogueMessage, ...]


class ConversationWindowRepository:
    """Loads only completed, user-visible dialogue under a deterministic budget."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def recent(
        self,
        user_id: str,
        *,
        turn_limit: int = 3,
        char_limit: int = 1500,
    ) -> tuple[DialogueTurn, ...]:
        if not 1 <= turn_limit <= 10:
            raise ValueError("Dialogue turn limit must be between 1 and 10")
        if not 100 <= char_limit <= 10_000:
            raise ValueError("Dialogue character limit must be between 100 and 10000")
        async with self._database.session() as session:
            turn_ids = tuple(
                await session.scalars(
                    select(AgentTurnRecord.id)
                    .join(
                        AgentThreadRecord,
                        AgentThreadRecord.id == AgentTurnRecord.thread_id,
                    )
                    .where(
                        AgentThreadRecord.user_id == user_id,
                        AgentTurnRecord.status == "completed",
                        exists().where(
                            AgentItemRecord.turn_id == AgentTurnRecord.id,
                            AgentItemRecord.item_type.in_(
                                ("user_message", "agent_message")
                            ),
                            AgentItemRecord.status == "completed",
                        ),
                    )
                    .order_by(AgentTurnRecord.completed_at.desc(), AgentTurnRecord.id.desc())
                    .limit(turn_limit)
                )
            )
            if not turn_ids:
                return ()
            rows = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .where(
                        AgentItemRecord.turn_id.in_(turn_ids),
                        AgentItemRecord.item_type.in_(("user_message", "agent_message")),
                        AgentItemRecord.status == "completed",
                    )
                    .order_by(AgentItemRecord.turn_id, AgentItemRecord.sequence)
                )
            )
        by_turn: dict[str, list[DialogueMessage]] = {turn_id: [] for turn_id in turn_ids}
        for row in rows:
            message = self._message(row)
            if message is not None:
                by_turn[row.turn_id].append(message)
        remaining = char_limit
        newest: list[DialogueTurn] = []
        for turn_id in turn_ids:
            messages: list[DialogueMessage] = []
            for message in reversed(by_turn[turn_id]):
                if remaining <= 0:
                    break
                content = message.content[-remaining:]
                if content:
                    messages.append(DialogueMessage(role=message.role, content=content))
                    remaining -= len(content)
            if messages:
                newest.append(
                    DialogueTurn(turn_id=turn_id, messages=tuple(reversed(messages)))
                )
            if len(newest) >= turn_limit or remaining <= 0:
                break
        return tuple(reversed(newest))

    @staticmethod
    def _message(row: AgentItemRecord) -> DialogueMessage | None:
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            return None
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        role = "user" if row.item_type == "user_message" else "assistant"
        return DialogueMessage(role=role, content=text.strip())
