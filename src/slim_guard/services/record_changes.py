"""Composition of the record store and its same-database task participant."""

from slim_guard.db.session import Database
from slim_guard.features.health_records.persistence.queries import HealthRecordQueries
from slim_guard.features.health_records.persistence.store import HealthRecordStore
from slim_guard.features.health_records.service import RecordService
from slim_guard.tasks.record_changes import commit_record_tasks


class _RecordService(HealthRecordStore, HealthRecordQueries):
    def __init__(self, database: Database) -> None:
        HealthRecordStore.__init__(self, database, task_commit=commit_record_tasks)


def build_record_service(database: Database) -> RecordService:
    return _RecordService(database)
