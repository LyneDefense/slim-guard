"""Composition of the record store and its same-database task participant."""

from slim_guard.db.session import Database
from slim_guard.features.health_records.meal.editing import MealEditRequest
from slim_guard.features.health_records.persistence.manual_edits import ManualMealEditor
from slim_guard.features.health_records.persistence.queries import HealthRecordQueries
from slim_guard.features.health_records.persistence.store import HealthRecordStore
from slim_guard.features.health_records.service import RecordService
from slim_guard.features.health_records.views import RecordView
from slim_guard.tasks.record_changes import commit_record_tasks


class _RecordService(HealthRecordStore, HealthRecordQueries):
    def __init__(self, database: Database) -> None:
        HealthRecordStore.__init__(self, database, task_commit=commit_record_tasks)
        self._meal_editor = ManualMealEditor(database)

    async def edit_meal(self, user_id: str, record_id: str, request: MealEditRequest) -> RecordView:
        return await self._meal_editor.edit_meal(user_id, record_id, request)


def build_record_service(database: Database) -> RecordService:
    return _RecordService(database)
