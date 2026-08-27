from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from slim_guard.harness.events import ItemStatus, ItemType, TurnTrigger
from slim_guard.harness.loop import HarnessTurnContext
from slim_guard.harness.state_repository import (
    ItemRef,
    NewTurnItem,
    ThreadRef,
    TurnInitializationStore,
    TurnRef,
)
from slim_guard.tools.contracts import ToolExecutionMode

_INPUT_ITEM_TYPES = frozenset(
    {
        ItemType.USER_MESSAGE,
        ItemType.IMAGE_ATTACHMENT,
        ItemType.APPROVAL_RESULT,
    }
)


class TurnInput(BaseModel):
    """Validated external input that may be written while a Turn is initialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_type: ItemType
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_input_type(self) -> Self:
        if self.item_type not in _INPUT_ITEM_TYPES:
            raise ValueError(f"Item type {self.item_type.value} is not a Turn input")
        return self

    @classmethod
    def user_message(
        cls,
        *,
        text: str,
        source_message_id: str | None = None,
        channel_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> TurnInput:
        stripped = text.strip()
        if not stripped:
            raise ValueError("User message text cannot be empty")
        return cls(
            item_type=ItemType.USER_MESSAGE,
            payload=cls._source_payload(
                content={"text": stripped},
                source_message_id=source_message_id,
                channel_id=channel_id,
                occurred_at=occurred_at,
            ),
        )

    @classmethod
    def image_attachment(
        cls,
        *,
        asset_id: str,
        mime_type: str,
        source_message_id: str | None = None,
        channel_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> TurnInput:
        normalized_asset_id = asset_id.strip()
        normalized_mime_type = mime_type.strip().lower()
        if not normalized_asset_id:
            raise ValueError("Image attachment requires an asset ID")
        if not normalized_mime_type.startswith("image/"):
            raise ValueError("Image attachment MIME type must start with image/")
        return cls(
            item_type=ItemType.IMAGE_ATTACHMENT,
            payload=cls._source_payload(
                content={
                    "asset_id": normalized_asset_id,
                    "mime_type": normalized_mime_type,
                },
                source_message_id=source_message_id,
                channel_id=channel_id,
                occurred_at=occurred_at,
            ),
        )

    @staticmethod
    def _source_payload(
        *,
        content: dict[str, Any],
        source_message_id: str | None,
        channel_id: str | None,
        occurred_at: datetime | None,
    ) -> dict[str, Any]:
        if occurred_at is not None and occurred_at.utcoffset() is None:
            raise ValueError("Turn input occurrence time must be timezone-aware")
        payload = dict(content)
        if source_message_id is not None:
            payload["source_message_id"] = source_message_id
        if channel_id is not None:
            payload["channel_id"] = channel_id
        if occurred_at is not None:
            payload["occurred_at"] = occurred_at.isoformat()
        return payload


class TurnInitializationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    agent_version_id: str = Field(min_length=1, max_length=128)
    trigger: TurnTrigger
    execution_mode: ToolExecutionMode
    inputs: tuple[TurnInput, ...] = Field(default=(), max_length=32)
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if self.deadline_at is not None and self.deadline_at.utcoffset() is None:
            raise ValueError("Turn deadline must be timezone-aware")
        if self.trigger is TurnTrigger.USER_MESSAGE and not any(
            item.item_type in {ItemType.USER_MESSAGE, ItemType.IMAGE_ATTACHMENT}
            for item in self.inputs
        ):
            raise ValueError("A user message Turn requires text or an image attachment")
        return self


@dataclass(frozen=True, slots=True)
class InitializedTurn:
    thread: ThreadRef
    turn: TurnRef
    input_items: tuple[ItemRef, ...]
    context: HarnessTurnContext
    source_item_id: str | None


class TurnInitializer:
    """Creates the durable boundary that every Harness run starts from."""

    def __init__(self, store: TurnInitializationStore) -> None:
        self._store = store

    async def initialize(self, request: TurnInitializationRequest) -> InitializedTurn:
        started = await self._store.start_turn_with_items(
            user_id=request.user_id,
            agent_version_id=request.agent_version_id,
            trigger=request.trigger,
            deadline_at=request.deadline_at,
            items=tuple(
                NewTurnItem(
                    item_type=item.item_type,
                    status=ItemStatus.COMPLETED,
                    payload=item.payload,
                )
                for item in request.inputs
            ),
        )
        source_item = next(
            (
                item
                for item in started.items
                if item.item_type is ItemType.USER_MESSAGE
            ),
            started.items[0] if started.items else None,
        )
        context = HarnessTurnContext(
            thread_id=started.thread.id,
            turn_id=started.turn.id,
            user_id=started.thread.user_id,
            agent_version_id=started.turn.agent_version_id,
            execution_mode=request.execution_mode,
            deadline_at=started.turn.deadline_at,
        )
        return InitializedTurn(
            thread=started.thread,
            turn=started.turn,
            input_items=started.items,
            context=context,
            source_item_id=source_item.id if source_item is not None else None,
        )
