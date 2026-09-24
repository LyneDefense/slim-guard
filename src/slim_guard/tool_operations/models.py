"""Storage owned by the tool-operation feature."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, utc_now


class ToolOperationRecord(Base):
    __tablename__ = "tool_operations"
    __table_args__ = (Index("ix_tool_operation_owner", "user_id", "thread_id", "status"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    initial_execution_key: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("agent_threads.id", ondelete="CASCADE"))
    source_turn_id: Mapped[str] = mapped_column(ForeignKey("agent_turns.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_version: Mapped[str] = mapped_column(String(128))
    context_json: Mapped[str] = mapped_column(Text)
    arguments_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    # Nullable unique guard: do not start another write while this capability's
    # preceding write is running/uncertain. Not an assertion of duplicate intent.
    unsettled_scope: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
