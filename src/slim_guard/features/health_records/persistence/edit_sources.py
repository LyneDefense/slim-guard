"""Durable user input from the record editor; it is not an Agent Turn."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, utc_now


class MealEditSource(Base):
    __tablename__ = "meal_edit_sources"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("meal_records.id", ondelete="CASCADE"))
    command_hash: Mapped[str] = mapped_column(String(64))
    input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
