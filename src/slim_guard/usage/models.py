"""One durable row per actual model request; no prompts or credentials stored."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, utc_now


class ModelUsageRecord(Base):
    __tablename__ = "model_usage_calls"
    __table_args__ = (
        Index("ix_usage_user_turn", "user_id", "turn_id", "started_at"),
        Index("ix_usage_request", "user_id", "request_id"),
        Index("ix_usage_invocation", "invocation_id"),
        Index("ix_usage_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str | None] = mapped_column(String(256))
    turn_id: Mapped[str | None] = mapped_column(String(128))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    agent_role: Mapped[str | None] = mapped_column(String(64))
    invocation_id: Mapped[str | None] = mapped_column(String(128))
    parent_invocation_id: Mapped[str | None] = mapped_column(String(128))
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    job_id: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    component: Mapped[str] = mapped_column(String(128))
    execution_kind: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(256))
    purpose: Mapped[str] = mapped_column(String(128))
    provider_request_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
