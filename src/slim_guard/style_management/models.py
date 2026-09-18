"""Administrative persistence. Domain code only sees snapshots and public ports."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, new_uuid, utc_now


class Style(Base):
    __tablename__ = "expression_styles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
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
    desired_response: Mapped[str] = mapped_column(Text, default="")
    correction_opinion: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    processed_revision: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[str | None] = mapped_column(String(36))
    last_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BuildRun(Base):
    __tablename__ = "expression_build_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    actor: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[str] = mapped_column(String(64), default="freeze")
    activity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    worker_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index(
            "uq_expression_open_build",
            "style_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )


class Version(Base):
    __tablename__ = "expression_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), index=True)
    build_run_id: Mapped[str | None] = mapped_column(ForeignKey("expression_build_runs.id"))
    name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="ready_for_review")
    package: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewCase(Base):
    __tablename__ = "expression_review_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("expression_versions.id"), index=True)
    test_case: Mapped[dict[str, Any]] = mapped_column(JSON)
    user_input: Mapped[str] = mapped_column(Text)
    original_response: Mapped[str] = mapped_column(Text)
    doctor_response: Mapped[str] = mapped_column(Text)
    baseline_response: Mapped[str] = mapped_column(Text)
    automated: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON)
