"""Public health-record application API."""

from typing import Protocol

from slim_guard.features.health_records.commands import (
    CreateRecord,
    RecordReceipt,
    RecordWriteContext,
    UpdateRecord,
)
from slim_guard.features.health_records.meal.editing import MealEditRequest
from slim_guard.features.health_records.queries import RecordPage, RecordQuery
from slim_guard.features.health_records.views import RecordView


class RecordService(Protocol):
    async def edit_meal(
        self, user_id: str, record_id: str, request: MealEditRequest
    ) -> RecordView: ...
    async def get(self, user_id: str, kind: str, record_id: str) -> RecordView: ...
    async def search(self, user_id: str, query: RecordQuery) -> RecordPage: ...
    async def write(
        self, context: RecordWriteContext, command: CreateRecord | UpdateRecord
    ) -> RecordReceipt: ...
    async def receipt(self, user_id: str, operation_id: str) -> RecordReceipt | None: ...
