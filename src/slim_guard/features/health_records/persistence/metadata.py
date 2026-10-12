from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class RecordMetadata:
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    time_precision: Mapped[str] = mapped_column(
        String(16), default="datetime", server_default="datetime"
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    time_basis: Mapped[str] = mapped_column(String(32), default="legacy", server_default="legacy")
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    field_provenance_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    recognition_issues_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    retired_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
