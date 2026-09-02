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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class SchemaMigrationRecord(Base):
    """Records application-owned schema migrations applied to a deployed database."""

    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


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


class AgentItemRedactionRecord(Base):
    __tablename__ = "agent_item_redactions"
    __table_args__ = (Index("ix_agent_item_redaction_created", "redacted_at"),)

    item_id: Mapped[str] = mapped_column(
        ForeignKey("agent_items.id", ondelete="CASCADE"), primary_key=True
    )
    original_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    redacted_at: Mapped[datetime] = mapped_column(
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


class BodyFatRecord(Base):
    __tablename__ = "body_fat_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_body_fat_record_idempotency_key"),
        CheckConstraint(
            "body_fat_basis_points BETWEEN 100 AND 7500",
            name="ck_body_fat_record_safe_range",
        ),
        CheckConstraint(
            "status IN ('active','superseded','voided')",
            name="ck_body_fat_record_status",
        ),
        Index("ix_body_fat_record_user_measured", "user_id", "measured_at"),
        Index("ix_body_fat_record_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body_fat_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    original_value: Mapped[str] = mapped_column(String(32), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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


class ExerciseRecord(Base):
    __tablename__ = "exercise_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_exercise_record_idempotency_key"),
        CheckConstraint(
            "status IN ('active','superseded','voided')",
            name="ck_exercise_record_status",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 1440",
            name="ck_exercise_record_duration",
        ),
        CheckConstraint(
            "steps IS NULL OR steps BETWEEN 0 AND 200000",
            name="ck_exercise_record_steps",
        ),
        CheckConstraint(
            "distance_meters IS NULL OR distance_meters BETWEEN 0 AND 1000000",
            name="ck_exercise_record_distance",
        ),
        CheckConstraint(
            "reported_energy_kcal IS NULL OR reported_energy_kcal BETWEEN 0 AND 20000",
            name="ck_exercise_record_energy",
        ),
        Index("ix_exercise_record_user_occurred", "user_id", "occurred_at"),
        Index("ix_exercise_record_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    activity_name: Mapped[str] = mapped_column(String(128), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_energy_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class UserMemoryFactRecord(Base):
    __tablename__ = "user_memory_facts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "operation_id",
            "slot_key",
            name="uq_user_memory_operation_slot",
        ),
        UniqueConstraint("supersedes_id", name="uq_user_memory_supersedes"),
        CheckConstraint(
            "kind IN ('profile','goal','constraint')",
            name="ck_user_memory_kind",
        ),
        CheckConstraint(
            "status IN ('active','superseded','revoked','expired')",
            name="ck_user_memory_status",
        ),
        CheckConstraint(
            "assertion IN ('user_explicit','user_confirmed','imported')",
            name="ck_user_memory_assertion",
        ),
        CheckConstraint(
            "sensitivity IN ('normal','health','restricted')",
            name="ck_user_memory_sensitivity",
        ),
        CheckConstraint(
            "status != 'active' OR value_json IS NOT NULL",
            name="ck_user_memory_active_value",
        ),
        Index("ix_user_memory_user_status", "user_id", "status"),
        Index("ix_user_memory_user_key", "user_id", "memory_key"),
        Index(
            "uq_user_memory_active_slot",
            "user_id",
            "slot_key",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    memory_key: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(256), nullable=False)
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    assertion: Mapped[str] = mapped_column(String(32), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_memory_facts.id", ondelete="RESTRICT"), nullable=True
    )
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str] = mapped_column(
        ForeignKey("agent_items.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_item_id: Mapped[str] = mapped_column(
        ForeignKey("agent_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserMemoryEventRecord(Base):
    __tablename__ = "user_memory_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created','superseded','revoked','expired','reviewed')",
            name="ck_user_memory_event_type",
        ),
        Index("ix_user_memory_event_memory_created", "memory_id", "created_at"),
        Index("ix_user_memory_event_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("user_memory_facts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="SET NULL"), nullable=True
    )
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_items.id", ondelete="SET NULL"), nullable=True
    )
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MemoryBulkOperationRecord(Base):
    __tablename__ = "memory_bulk_operations"
    __table_args__ = (Index("ix_memory_bulk_user_created", "user_id", "created_at"),)

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str] = mapped_column(
        ForeignKey("agent_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MemoryHandoffRecord(Base):
    __tablename__ = "memory_handoffs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','resolved','expired')",
            name="ck_memory_handoff_status",
        ),
        Index("ix_memory_handoff_user_status", "user_id", "status"),
        Index("ix_memory_handoff_expiry", "status", "expires_at"),
        Index(
            "uq_memory_handoff_active_user",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    objective: Mapped[str] = mapped_column(String(300), nullable=False)
    unresolved_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    source_item_id: Mapped[str] = mapped_column(
        ForeignKey("agent_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_tool_call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRoutinePreference(Base):
    __tablename__ = "user_routine_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    weight_reminder_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    meal_reminder_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    daily_review_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RoutineJobRecord(Base):
    __tablename__ = "routine_jobs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "job_kind",
            "local_date",
            name="uq_routine_job_user_kind_date",
        ),
        CheckConstraint(
            "job_kind IN ('weight','meal','daily_review')",
            name="ck_routine_job_kind",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','skipped','failed')",
            name="ck_routine_job_status",
        ),
        Index("ix_routine_job_due", "status", "scheduled_for"),
        Index("ix_routine_job_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="SET NULL"), nullable=True
    )
    result_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProactiveMessageRecord(Base):
    __tablename__ = "proactive_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned','sending','accepted','unknown','failed')",
            name="ck_proactive_message_status",
        ),
        Index("ix_proactive_message_route_created", "open_kfid", "external_userid", "created_at"),
        Index("ix_proactive_message_status", "status"),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("routine_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    platform_msgid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    open_kfid: Mapped[str] = mapped_column(String(128), nullable=False)
    external_userid: Mapped[str] = mapped_column(String(256), nullable=False)
    last_customer_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_turn_id: Mapped[str] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class InteractionTraceRecord(Base):
    """One user-scoped causal chain that may produce a user-visible message."""

    __tablename__ = "interaction_traces"
    __table_args__ = (
        UniqueConstraint(
            "outbound_idempotency_key",
            name="uq_interaction_trace_outbound",
        ),
        UniqueConstraint("routine_job_id", name="uq_interaction_trace_routine_job"),
        Index("ix_interaction_trace_user_created", "user_id", "created_at"),
        Index("ix_interaction_trace_generation", "generation_status"),
        Index("ix_interaction_trace_delivery", "delivery_status"),
        Index("ix_interaction_trace_turn", "agent_turn_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inbound_msgid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    outbound_idempotency_key: Mapped[str | None] = mapped_column(
        ForeignKey("outbound_messages.idempotency_key", ondelete="SET NULL"), nullable=True
    )
    routine_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("routine_jobs.id", ondelete="SET NULL"), nullable=True
    )
    agent_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_turns.id", ondelete="SET NULL"), nullable=True
    )
    agent_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reply_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    generation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="planned"
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TraceSpanRecord(Base):
    """A timed, observable component operation within an interaction trace."""

    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "sequence", name="uq_trace_span_sequence"),
        Index("ix_trace_span_trace_started", "trace_id", "started_at"),
        Index("ix_trace_span_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("interaction_traces.id", ondelete="CASCADE"), nullable=False
    )
    parent_span_id: Mapped[str | None] = mapped_column(
        ForeignKey("trace_spans.id", ondelete="SET NULL"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    attributes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminAuditEventRecord(Base):
    """Append-only audit record for access to sensitive admin resources."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index("ix_admin_audit_created", "created_at"),
        Index("ix_admin_audit_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(
        ForeignKey("interaction_traces.id", ondelete="SET NULL"), nullable=True
    )
    remote_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
