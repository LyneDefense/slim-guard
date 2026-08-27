from __future__ import annotations

from slim_guard.db.models import AgentVersionRecord
from slim_guard.db.session import Database
from slim_guard.harness.manifest import AgentManifest


class AgentVersionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def register(self, manifest: AgentManifest) -> bool:
        """Persist a manifest once and return whether a new row was created."""

        version_id = manifest.version_id
        manifest_json = manifest.to_json()
        async with self.database.session() as session, session.begin():
            existing = await session.get(AgentVersionRecord, version_id)
            if existing is None:
                session.add(
                    AgentVersionRecord(
                        id=version_id,
                        manifest_json=manifest_json,
                        code_revision=manifest.code_revision,
                    )
                )
                return True
            if existing.manifest_json != manifest_json:
                raise RuntimeError(f"Agent manifest hash collision for {version_id}")
            return False

    async def get(self, version_id: str) -> AgentVersionRecord | None:
        async with self.database.session() as session:
            return await session.get(AgentVersionRecord, version_id)
