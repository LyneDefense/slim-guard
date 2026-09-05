from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from slim_guard.agents.contracts import AgentArtifact, AgentInvocation, AgentResult
from slim_guard.db.models import (
    AgentArtifactRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.orchestration.artifacts import (
    ArtifactAlreadyExists,
    ArtifactIntegrityError,
    ArtifactReferenceError,
)
from slim_guard.orchestration.repository import OrchestrationRepository


async def prepare_repository(
    tmp_path,
) -> tuple[Database, OrchestrationRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'orchestration.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        session.add(
            AgentVersionRecord(
                id="version-1",
                manifest_json="{}",
                code_revision="test",
                created_at=now,
            )
        )
        session.add(
            AgentThreadRecord(
                id="thread-1",
                user_id="user-1",
                created_at=now,
                last_active_at=now,
            )
        )
        session.add_all(
            (
                AgentTurnRecord(
                    id="turn-1",
                    thread_id="thread-1",
                    agent_version_id="version-1",
                    trigger_type="user_message",
                    status="running",
                    created_at=now,
                    updated_at=now,
                ),
                AgentTurnRecord(
                    id="turn-2",
                    thread_id="thread-1",
                    agent_version_id="version-1",
                    trigger_type="user_message",
                    status="running",
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
    return database, OrchestrationRepository(database)


def artifact(
    artifact_id: str,
    *,
    turn_id: str = "turn-1",
    parents: tuple[str, ...] = (),
    payload: dict[str, object] | None = None,
) -> AgentArtifact:
    return AgentArtifact.create(
        artifact_id=artifact_id,
        turn_id=turn_id,
        producer_role="orchestrator",
        artifact_type="TurnDirective",
        schema_version="1",
        parent_artifact_ids=parents,
        payload=payload or {"artifact_id": artifact_id},
        created_at=datetime.now(UTC),
    )


def invocation(*artifact_ids: str, turn_id: str = "turn-1") -> AgentInvocation:
    return AgentInvocation(
        invocation_id=f"invocation-{turn_id}",
        trace_id="standalone-correlation",
        turn_id=turn_id,
        graph_version="shadow-v1",
        agent_role="orchestrator",
        agent_version="orchestrator-v1",
        input_artifact_ids=artifact_ids,
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        max_model_calls=1,
        max_tool_calls=0,
        max_total_tokens=2000,
    )


async def test_repository_persists_invocation_and_immutable_artifact_lineage(
    tmp_path,
) -> None:
    database, repository = await prepare_repository(tmp_path)
    try:
        await repository.append_artifact(artifact("input"))
        started = await repository.start_invocation(
            invocation("input"),
            reason_summary="生成结构化回复计划",
        )
        output = artifact("output", parents=("input",), payload={"text": "候选回复"})
        await repository.append_artifact(
            output,
            invocation_id=started.invocation_id,
        )
        completed = await repository.complete_invocation(
            AgentResult(
                invocation_id=started.invocation_id,
                status="succeeded",
                output_schema="TurnDirective",
                output_schema_version="1",
                artifact_id=output.artifact_id,
                model_call_count=1,
                tool_call_count=0,
                token_usage=42,
            )
        )

        assert completed.status == "succeeded"
        assert completed.output_artifact_id == "output"
        assert (await repository.require_artifact("output")).payload == {"text": "候选回复"}
        assert tuple(item.artifact_id for item in await repository.lineage("output")) == (
            "output",
            "input",
        )
        assert (await repository.list_turn_invocations("turn-1")) == (completed,)
        with pytest.raises(ArtifactAlreadyExists):
            await repository.append_artifact(output)
    finally:
        await database.close()


async def test_repository_rejects_cross_turn_and_detects_payload_tampering(
    tmp_path,
) -> None:
    database, repository = await prepare_repository(tmp_path)
    try:
        await repository.append_artifact(artifact("turn-1-input"))
        with pytest.raises(ArtifactReferenceError, match="another turn"):
            await repository.append_artifact(
                artifact(
                    "forged-child",
                    turn_id="turn-2",
                    parents=("turn-1-input",),
                )
            )
        with pytest.raises(ArtifactReferenceError, match="another turn"):
            await repository.validate_invocation(invocation("turn-1-input", turn_id="turn-2"))

        async with database.session() as session, session.begin():
            await session.execute(
                update(AgentArtifactRecord)
                .where(AgentArtifactRecord.id == "turn-1-input")
                .values(payload_json='{"artifact_id":"tampered"}')
            )
        with pytest.raises(ArtifactIntegrityError):
            await repository.require_artifact("turn-1-input")
    finally:
        await database.close()
