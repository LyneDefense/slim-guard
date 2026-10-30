"""Account-erasure boundary for Harness-owned execution graphs."""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import (
    AgentArtifactParentRecord,
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentThreadRecord,
    AgentTurnRecord,
)


async def delete_user_execution_graph(session: AsyncSession, user_id: str) -> None:
    """Join ownership here; the caller owns the surrounding account transaction.

    RESTRICT lineage edges must be removed before cascading their Turn. A bulk
    Turn delete cannot rely on a database choosing a favorable cascade order.
    """
    turns = (
        select(AgentTurnRecord.id)
        .join(AgentThreadRecord, AgentThreadRecord.id == AgentTurnRecord.thread_id)
        .where(AgentThreadRecord.user_id == user_id)
    )
    await session.execute(
        delete(AgentArtifactParentRecord).where(AgentArtifactParentRecord.turn_id.in_(turns))
    )
    await session.execute(delete(AgentArtifactRecord).where(AgentArtifactRecord.turn_id.in_(turns)))
    await session.execute(
        update(AgentInvocationRecord)
        .where(AgentInvocationRecord.turn_id.in_(turns))
        .values(parent_invocation_id=None)
    )
    await session.execute(
        delete(AgentInvocationRecord).where(AgentInvocationRecord.turn_id.in_(turns))
    )
