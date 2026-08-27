from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class AgentVersionRecord(Base):
    __tablename__ = "agent_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    code_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AgentThreadRecord(Base):
    __tablename__ = "agent_threads"
    __table_args__ = (UniqueConstraint("user_id", name="uq_agent_thread_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AgentTurnRecord(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        Index("ix_agent_turn_thread_created", "thread_id", "created_at"),
        Index("ix_agent_turn_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False
    )
    agent_version_id: Mapped[str] = mapped_column(
        ForeignKey("agent_versions.id"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="running")
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentItemRecord(Base):
    __tablename__ = "agent_items"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_agent_item_turn_sequence"),
        Index("ix_agent_item_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WeightRecord(Base):
    __tablename__ = "weight_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_weight_record_idempotency_key"),
        UniqueConstraint("supersedes_id", name="uq_weight_record_supersedes_id"),
        CheckConstraint(
            "weight_grams BETWEEN 10000 AND 500000",
            name="ck_weight_record_safe_range",
        ),
        CheckConstraint(
            "original_unit IN ('kg','jin','lb')",
            name="ck_weight_record_original_unit",
        ),
        CheckConstraint(
            "measurement_condition IN ('fasting','post_meal','unspecified')",
            name="ck_weight_record_condition",
        ),
        CheckConstraint(
            "status IN ('active','superseded','voided')",
            name="ck_weight_record_status",
        ),
        Index("ix_weight_record_user_measured", "user_id", "measured_at"),
        Index("ix_weight_record_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    weight_grams: Mapped[int] = mapped_column(Integer, nullable=False)
    original_value: Mapped[str] = mapped_column(String(32), nullable=False)
    original_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    measurement_condition: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_items.id", ondelete="SET NULL"), nullable=True
    )
    source_tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("weight_records.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ImageAssetRecord(Base):
    __tablename__ = "image_assets"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "source_message_id",
            name="uq_image_asset_channel_source",
        ),
        CheckConstraint("size_bytes > 0", name="ck_image_asset_nonempty"),
        CheckConstraint(
            "mime_type IN ('image/jpeg','image/png','image/gif','image/webp')",
            name="ck_image_asset_mime_type",
        ),
        Index("ix_image_asset_user_created", "user_id", "created_at"),
        Index("ix_image_asset_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MealRecord(Base):
    __tablename__ = "meal_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_meal_record_idempotency_key"),
        CheckConstraint(
            "meal_type IN ('breakfast','lunch','dinner','snack','unspecified')",
            name="ck_meal_record_type",
        ),
        CheckConstraint(
            "status IN ('active','superseded','voided')",
            name="ck_meal_record_status",
        ),
        Index("ix_meal_record_user_occurred", "user_id", "occurred_at"),
        Index("ix_meal_record_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    foods_json: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_items.id", ondelete="SET NULL"), nullable=True
    )
    source_tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PendingActionRecord(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        UniqueConstraint(
            "execution_key",
            "action_type",
            name="uq_pending_action_execution_type",
        ),
        Index("ix_pending_action_thread_status", "thread_id", "status"),
        Index("ix_pending_action_status_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="CASCADE"), nullable=False
    )
    source_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_items.id", ondelete="SET NULL"), nullable=True
    )
    execution_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    isolated_write_environment: Mapped[bool] = mapped_column(Boolean, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolExecutionRecord(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint("turn_id", "tool_call_id", name="uq_tool_execution_turn_call"),
        Index("ix_tool_execution_status", "status"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class SlimGuardUser(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_user_last_seen_at", "last_seen_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    nickname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        Index("ix_channel_identity_user_id", "user_id"),
        Index("ix_channel_identity_unionid", "channel_id", "unionid"),
    )

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_userid: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    unionid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    profile_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    profile_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
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
