from __future__ import annotations

from slim_guard.db.session import Database
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository


def build_manifest(*, prompt: str = "You are SlimGuard.") -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"thinking": {"type": "disabled"}},
        system_prompt_version="legacy-v1",
        system_prompt=prompt,
        context_policy_version="single-turn-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="legacy-v1",
        code_revision="test-revision",
    )


async def test_register_agent_version_is_idempotent(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent-versions.sqlite3'}")
    await database.create_schema()
    repository = AgentVersionRepository(database)
    manifest = build_manifest()
    try:
        assert await repository.register(manifest) is True
        assert await repository.register(manifest) is False

        stored = await repository.get(manifest.version_id)
        assert stored is not None
        assert stored.id == manifest.version_id
        assert stored.manifest_json == manifest.to_json()
        assert stored.code_revision == "test-revision"
    finally:
        await database.close()


async def test_register_stores_different_agent_versions(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent-versions.sqlite3'}")
    await database.create_schema()
    repository = AgentVersionRepository(database)
    baseline = build_manifest()
    candidate = build_manifest(prompt="You are a concise SlimGuard.")
    try:
        assert await repository.register(baseline) is True
        assert await repository.register(candidate) is True
        assert baseline.version_id != candidate.version_id
        assert await repository.get(baseline.version_id) is not None
        assert await repository.get(candidate.version_id) is not None
    finally:
        await database.close()
