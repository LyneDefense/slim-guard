"""Workbench drafts and server-owned release checks; legacy releases stay intact."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, new_uuid, utc_now


class StudioDraft(Base):
    __tablename__ = "expression_studio_drafts"

    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_version_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StudioCheck(Base):
    __tablename__ = "expression_studio_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    style_id: Mapped[str] = mapped_column(ForeignKey("expression_styles.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published_version_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
