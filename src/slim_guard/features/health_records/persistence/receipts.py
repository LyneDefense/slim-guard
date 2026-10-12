from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from slim_guard.db.models import Base, utc_now


class RecordMutationReceipt(Base):
    """Immutable effect receipt, committed in the same transaction as the record."""

    __tablename__ = "health_record_operation_receipts"
    __table_args__ = (Index("ix_health_receipt_user_record", "user_id", "record_id"),)

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    record_kind: Mapped[str] = mapped_column(String(16))
    record_id: Mapped[str] = mapped_column(String(36))
    resulting_revision: Mapped[int] = mapped_column(Integer)
    command_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
