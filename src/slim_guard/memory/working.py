from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
)
from slim_guard.db.session import Database
from slim_guard.domain.assets import ImageAssetRepository

COMPACTION_POLICY_VERSION = "bounded-dialogue-attachments-redaction-v5"


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    role: str
    content: str
    evidence_ref: str | None = None
    attachments: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    turn_id: str
    messages: tuple[DialogueMessage, ...]


@dataclass(frozen=True, slots=True)
class UserMessageEvidence:
    evidence_ref: str
    content: str
    created_at: datetime


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
                                ("user_message", "agent_message", "image_attachment")
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
                        AgentItemRecord.item_type.in_(
                            ("user_message", "agent_message", "image_attachment")
                        ),
                        AgentItemRecord.status == "completed",
                    )
                    .order_by(AgentItemRecord.turn_id, AgentItemRecord.sequence)
                )
            )
        attachment_ids = tuple(
            str(json.loads(row.payload_json).get("asset_id", ""))
            for row in rows
            if row.item_type == "image_attachment"
        )
        annotations = await ImageAssetRepository(self._database).annotations(
            user_id=user_id, asset_ids=attachment_ids
        )
        by_turn: dict[str, list[DialogueMessage]] = {turn_id: [] for turn_id in turn_ids}
        for row in rows:
            messages = by_turn[row.turn_id]
            if row.item_type == "image_attachment":
                asset_id = json.loads(row.payload_json).get("asset_id")
                attachment = annotations.get(asset_id)
                if attachment is None:
                    continue
                # An attachment belongs to its inbound message/Turn, not a second
                # independently selected window of recent images.
                if messages and messages[-1].role == "user":
                    prior = messages[-1]
                    messages[-1] = DialogueMessage(
                        role=prior.role,
                        content=prior.content,
                        evidence_ref=prior.evidence_ref,
                        attachments=(*prior.attachments, attachment),
                    )
                else:
                    messages.append(
                        DialogueMessage(
                            role="user",
                            content="[图片附件]",
                            attachments=(attachment,),
                        )
                    )
                continue
            message = self._message(row)
            if message is not None:
                messages.append(message)
        remaining = char_limit
        newest: list[DialogueTurn] = []
        for turn_id in turn_ids:
            messages = []
            for message in reversed(by_turn[turn_id]):
                if remaining <= 0:
                    break
                attachments = message.attachments
                attachment_cost = (
                    len(json.dumps(attachments, ensure_ascii=False)) if attachments else 0
                )
                if attachment_cost >= remaining:
                    attachments = ()
                    attachment_cost = 0
                content = message.content[-(remaining - attachment_cost) :]
                if content:
                    messages.append(
                        DialogueMessage(
                            role=message.role,
                            content=content,
                            evidence_ref=message.evidence_ref,
                            attachments=attachments,
                        )
                    )
                    remaining -= len(content) + attachment_cost
            if messages:
                newest.append(DialogueTurn(turn_id=turn_id, messages=tuple(reversed(messages))))
            if len(newest) >= turn_limit or remaining <= 0:
                break
        return tuple(reversed(newest))

    async def recent_user_evidence(
        self,
        user_id: str,
        *,
        exclude_turn_id: str,
        limit: int = 20,
        char_limit: int = 6000,
    ) -> tuple[UserMessageEvidence, ...]:
        """Load a bounded, user-only evidence backlog for model-first ingestion."""
        if not 1 <= limit <= 100:
            raise ValueError("Evidence message limit must be between 1 and 100")
        if not 100 <= char_limit <= 20_000:
            raise ValueError("Evidence character limit must be between 100 and 20000")
        async with self._database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .join(
                        AgentThreadRecord,
                        AgentThreadRecord.id == AgentItemRecord.thread_id,
                    )
                    .join(AgentTurnRecord, AgentTurnRecord.id == AgentItemRecord.turn_id)
                    .where(
                        AgentThreadRecord.user_id == user_id,
                        AgentTurnRecord.status == "completed",
                        AgentItemRecord.turn_id != exclude_turn_id,
                        AgentItemRecord.item_type == "user_message",
                        AgentItemRecord.status == "completed",
                    )
                    .order_by(AgentItemRecord.created_at.desc(), AgentItemRecord.id.desc())
                    .limit(limit)
                )
            )
        remaining = char_limit
        newest: list[UserMessageEvidence] = []
        for row in rows:
            message = self._message(row)
            if message is None or message.evidence_ref is None:
                continue
            content = message.content[-remaining:]
            if not content:
                break
            newest.append(
                UserMessageEvidence(
                    evidence_ref=message.evidence_ref,
                    content=content,
                    created_at=self._as_utc(row.created_at),
                )
            )
            remaining -= len(content)
            if remaining <= 0:
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
        return DialogueMessage(
            role=role,
            content=text.strip(),
            evidence_ref=row.id if role == "user" else None,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
