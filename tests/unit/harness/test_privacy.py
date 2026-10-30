import hashlib
from datetime import UTC, datetime

from sqlalchemy import delete, select

from slim_guard.db.models import (
    AgentArtifactParentRecord,
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.harness.privacy import delete_user_execution_graph


async def test_account_graph_erasure_is_atomic_user_scoped_and_respects_restrict_edges(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'privacy.sqlite'}")
    now = datetime.now(UTC)
    try:
        await db.migrate()
        async with db.session() as session, session.begin():
            session.add(AgentVersionRecord(id="version", code_revision="test", manifest_json="{}"))
            await session.flush()
            for owner in ("u1", "u2"):
                session.add(SlimGuardUser(id=owner, first_seen_at=now, last_seen_at=now))
                await session.flush()
                session.add(AgentThreadRecord(id=owner, user_id=owner))
                await session.flush()
                session.add(
                    AgentTurnRecord(
                        id=owner,
                        thread_id=owner,
                        agent_version_id="version",
                        trigger_type="user_message",
                    )
                )
                await session.flush()
                for role, parent in (("core", None), ("conversation", f"{owner}-core")):
                    session.add(
                        AgentInvocationRecord(
                            id=f"{owner}-{role}",
                            trace_id="trace",
                            turn_id=owner,
                            graph_version="test",
                            agent_role=role,
                            agent_version="v1",
                            attempt=1,
                            caller="harness",
                            input_schema_version="1",
                            deadline_at=now,
                            parent_invocation_id=parent,
                        )
                    )
                    await session.flush()
                    session.add(
                        AgentArtifactRecord(
                            id=f"{owner}-{role}",
                            turn_id=owner,
                            invocation_id=f"{owner}-{role}",
                            producer_role=role,
                            artifact_type="test",
                            schema_version="1",
                            payload_json="{}",
                            payload_sha256=hashlib.sha256(b"{}").hexdigest(),
                            created_at=now,
                        )
                    )
                    await session.flush()
                session.add(
                    AgentArtifactParentRecord(
                        artifact_id=f"{owner}-conversation",
                        parent_artifact_id=f"{owner}-core",
                        turn_id=owner,
                    )
                )
                await session.flush()
        async with db.session() as session:
            await delete_user_execution_graph(session, "u1")
            await session.rollback()
            assert await session.get(AgentInvocationRecord, "u1-core") is not None
        async with db.session() as session, session.begin():
            await delete_user_execution_graph(session, "u1")
            # No RESTRICT invocation/artifact edge now obstructs the Turn.
            await session.execute(delete(AgentTurnRecord).where(AgentTurnRecord.id == "u1"))
        async with db.session() as session:
            assert set(await session.scalars(select(AgentInvocationRecord.id))) == {
                "u2-core",
                "u2-conversation",
            }
            assert set(await session.scalars(select(AgentArtifactRecord.id))) == {
                "u2-core",
                "u2-conversation",
            }
            assert await session.get(AgentArtifactParentRecord, ("u2-conversation", "u2-core"))
    finally:
        await db.close()
