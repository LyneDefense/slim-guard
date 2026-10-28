"""Mobile history projection, including stable notice identities and user photos."""

import json
from datetime import UTC

from sqlalchemy import select

from slim_guard.db.models import AgentItemRecord, AgentThreadRecord
from slim_guard.db.session import Database
from slim_guard.harness.events import ItemType
from slim_guard.mobile.contracts import ChatAttachmentView, ChatHistoryView, ChatMessageView


async def load_chat_history(database: Database, user_id: str, *, limit: int) -> ChatHistoryView:
    async with database.session() as session:
        rows = tuple(
            await session.scalars(
                select(AgentItemRecord)
                .join(
                    AgentThreadRecord,
                    AgentThreadRecord.id == AgentItemRecord.thread_id,
                )
                .where(
                    AgentThreadRecord.user_id == user_id,
                    AgentItemRecord.item_type.in_(
                        (
                            ItemType.USER_MESSAGE.value,
                            ItemType.IMAGE_ATTACHMENT.value,
                            ItemType.AGENT_MESSAGE.value,
                        )
                    ),
                )
                .order_by(AgentItemRecord.created_at.desc(), AgentItemRecord.sequence.desc())
                .limit(limit)
            )
        )
    items: list[ChatMessageView] = []
    user_messages: dict[tuple[str, str], int] = {}
    for row in reversed(rows):
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("redacted") is True:
            continue
        item_type = ItemType(row.item_type)
        raw_text = payload.get("text")
        text = raw_text if isinstance(raw_text, str) else None
        created = row.created_at
        created = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
        if item_type is ItemType.AGENT_MESSAGE:
            participant = payload.get("participant", "system_assistant")
            if participant not in {"coach", "system_assistant"}:
                participant = "system_assistant"
            kind = payload.get("kind", "text")
            if kind not in {"text", "image", "card"}:
                kind = "text"
            notice = payload.get("notice_text")
            identity = payload.get("id")
            items.append(
                ChatMessageView(
                    id=identity if isinstance(identity, str) and identity else row.id,
                    turn_id=row.turn_id,
                    participant=participant,
                    role="assistant",
                    kind=kind,
                    text=text,
                    card=payload.get("card") if isinstance(payload.get("card"), dict) else None,
                    requires_response=payload.get("requires_response") is True,
                    notice_text=notice if isinstance(notice, str) else None,
                    created_at=created,
                )
            )
            continue
        source = payload.get("source_message_id")
        key = (row.turn_id, source if isinstance(source, str) and source else row.id)
        index = user_messages.get(key)
        if item_type is ItemType.USER_MESSAGE:
            if index is None:
                user_messages[key] = len(items)
                items.append(
                    ChatMessageView(
                        id=row.id,
                        turn_id=row.turn_id,
                        participant="user",
                        role="user",
                        kind="text",
                        text=text,
                        created_at=created,
                    )
                )
            else:
                items[index] = items[index].model_copy(update={"text": text})
        elif item_type is ItemType.IMAGE_ATTACHMENT:
            asset_id, mime_type = payload.get("asset_id"), payload.get("mime_type")
            if not isinstance(asset_id, str) or not isinstance(mime_type, str):
                continue
            attachment = ChatAttachmentView(
                asset_id=asset_id,
                mime_type=mime_type,
                url=f"/api/mobile/v1/chat/assets/{asset_id}",
            )
            if index is None:
                user_messages[key] = len(items)
                items.append(
                    ChatMessageView(
                        id=row.id,
                        turn_id=row.turn_id,
                        participant="user",
                        role="user",
                        kind="image",
                        text=None,
                        attachments=[attachment],
                        created_at=created,
                    )
                )
            else:
                existing = items[index]
                items[index] = existing.model_copy(
                    update={
                        "kind": "text" if existing.text else "image",
                        "attachments": [*existing.attachments, attachment],
                    }
                )
    return ChatHistoryView(items=items)
