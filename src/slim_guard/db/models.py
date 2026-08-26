from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class WeComSyncState(Base):
    __tablename__ = "wecom_sync_states"

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    open_kfid: Mapped[str] = mapped_column(String(128), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeComConversation(Base):
    __tablename__ = "wecom_conversations"
    __table_args__ = (Index("ix_wecom_conversation_service_state", "service_state"),)

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    open_kfid: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_userid: Mapped[str] = mapped_column(String(256), primary_key=True)
    service_state: Mapped[int | None] = mapped_column(Integer, nullable=True)
    servicer_userid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_customer_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_servicer_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_state_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_state_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    human_timeout_handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        UniqueConstraint("channel_id", "msgid", name="uq_inbound_channel_msgid"),
        Index("ix_inbound_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    msgid: Mapped[str] = mapped_column(String(256), nullable=False)
    open_kfid: Mapped[str] = mapped_column(String(128), nullable=False)
    external_userid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    msgtype: Mapped[str] = mapped_column(String(32), nullable=False)
    origin: Mapped[int] = mapped_column(Integer, nullable=False)
    send_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "inbound_msgid"],
            ["inbound_messages.channel_id", "inbound_messages.msgid"],
            name="fk_outbound_inbound_message",
        ),
        Index("ix_outbound_status", "status"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform_msgid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    inbound_msgid: Mapped[str] = mapped_column(String(256), nullable=False)
    open_kfid: Mapped[str] = mapped_column(String(128), nullable=False)
    external_userid: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    attempt_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
