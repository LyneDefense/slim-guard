from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, new_uuid, utc_now


class Style(Base):
    __tablename__ = "expression_styles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index(
            "uq_expression_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default"),
        ),
    )


class Example(Base):
    __tablename__ = "expression_examples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), index=True)
    user_input: Mapped[str] = mapped_column(Text)
    original_response: Mapped[str] = mapped_column(Text)
    desired_response: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="unclassified")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="correction")
    source_case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Version(Base):
    __tablename__ = "expression_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[str] = mapped_column(String(64), default="冻结示例库")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    guide: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_token: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = (
        Index(
            "uq_expression_open_build",
            "style_id",
            unique=True,
            sqlite_where=text("status IN ('queued','building')"),
            postgresql_where=text("status IN ('queued','building')"),
        ),
    )


class ReviewCase(Base):
    __tablename__ = "expression_review_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("expression_versions.id"), index=True)
    example_id: Mapped[str] = mapped_column(String(36))
    user_input: Mapped[str] = mapped_column(Text)
    original_response: Mapped[str] = mapped_column(Text)
    doctor_response: Mapped[str] = mapped_column(Text)
    desired_response: Mapped[str] = mapped_column(Text)
    automated: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON)
