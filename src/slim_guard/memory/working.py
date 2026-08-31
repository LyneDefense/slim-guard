from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select

from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    ImageAssetRecord,
)
from slim_guard.db.session import Database


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    turn_id: str
    messages: tuple[DialogueMessage, ...]


@dataclass(frozen=True, slots=True)
class RecentImageReference:
    asset_id: str
    mime_type: str
    created_at: datetime
    expires_at: datetime
    observation: str | None
    requires_user_confirmation: bool | None


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

    async def recent_images(
        self,
        user_id: str,
        *,
        at: datetime,
        limit: int = 3,
    ) -> tuple[RecentImageReference, ...]:
        """Return recent user-owned image capabilities plus model observations.

        The asset row is the authority for ownership and expiry. Observations remain
        explicitly non-authoritative working memory and can be refreshed by calling
        ``inspect_image`` with the provided ID.
        """
        if at.utcoffset() is None:
            raise ValueError("Recent image lookup time must be timezone-aware")
        if not 1 <= limit <= 10:
            raise ValueError("Recent image limit must be between 1 and 10")
        reference_time = at.astimezone(UTC)
        async with self._database.session() as session:
            assets = tuple(
                await session.scalars(
                    select(ImageAssetRecord)
                    .where(
                        ImageAssetRecord.user_id == user_id,
                        ImageAssetRecord.expires_at > reference_time,
                    )
                    .order_by(ImageAssetRecord.created_at.desc(), ImageAssetRecord.id.desc())
                    .limit(limit)
                )
            )
            if not assets:
                return ()
            result_rows = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .join(
                        AgentThreadRecord,
                        AgentThreadRecord.id == AgentItemRecord.thread_id,
                    )
                    .where(
                        AgentThreadRecord.user_id == user_id,
                        AgentItemRecord.item_type == "tool_result",
                        AgentItemRecord.status == "completed",
                    )
                    .order_by(AgentItemRecord.created_at.desc())
                    .limit(50)
                )
            )
        observations: dict[str, tuple[str, bool | None]] = {}
        wanted_ids = {asset.id for asset in assets}
        for row in result_rows:
            parsed = self._image_observation(row)
            if parsed is None:
                continue
            asset_id, observation, requires_confirmation = parsed
            if asset_id in wanted_ids and asset_id not in observations:
                observations[asset_id] = (observation, requires_confirmation)
        return tuple(
            RecentImageReference(
                asset_id=asset.id,
                mime_type=asset.mime_type,
                created_at=self._as_utc(asset.created_at),
                expires_at=self._as_utc(asset.expires_at),
                observation=(
                    observations[asset.id][0] if asset.id in observations else None
                ),
                requires_user_confirmation=(
                    observations[asset.id][1] if asset.id in observations else None
                ),
            )
            for asset in assets
        )

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

    @staticmethod
    def _image_observation(
        row: AgentItemRecord,
    ) -> tuple[str, str, bool | None] | None:
        try:
            payload = json.loads(row.payload_json)
            execution = payload.get("execution", {})
            if execution.get("tool_name") != "inspect_image":
                return None
            result = execution.get("result", {})
            if result.get("status") != "succeeded":
                return None
            output = result.get("output", {})
            asset_id = output.get("asset_id")
            observation = output.get("description")
            requires_confirmation = output.get("requires_user_confirmation")
        except (AttributeError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(asset_id, str) or not isinstance(observation, str):
            return None
        if not asset_id or not observation.strip():
            return None
        if not isinstance(requires_confirmation, bool):
            requires_confirmation = None
        return asset_id, observation.strip(), requires_confirmation

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
