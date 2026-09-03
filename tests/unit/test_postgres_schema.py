from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from slim_guard.db.models import Base


def test_complete_schema_compiles_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl
        for index in table.indexes:
            index_ddl = str(CreateIndex(index).compile(dialect=dialect))
            assert "CREATE " in index_ddl and " INDEX " in index_ddl
