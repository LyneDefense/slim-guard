"""Include authorized Agent executions even when they made no model request."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.db.models import AgentInvocationRecord, AgentThreadRecord, AgentTurnRecord
from slim_guard.usage.models import ModelUsageRecord as Call


async def execution_counts(
    session: AsyncSession, *, user_id: str | None, exchange_id: str, call_filters: list[Any]
) -> dict[str, int]:
    # Offline style evaluation invocations may exist only in the call ledger.
    identities = set(
        (
            await session.execute(
                select(Call.agent_role, Call.invocation_id)
                .where(*call_filters, Call.agent_role.is_not(None), Call.invocation_id.is_not(None))
                .distinct()
            )
        ).tuples()
    )
    if user_id is not None:
        identities.update(
            (
                await session.execute(
                    select(AgentInvocationRecord.agent_role, AgentInvocationRecord.id)
                    .join(AgentTurnRecord, AgentTurnRecord.id == AgentInvocationRecord.turn_id)
                    .join(AgentThreadRecord, AgentThreadRecord.id == AgentTurnRecord.thread_id)
                    .where(AgentThreadRecord.user_id == user_id, AgentTurnRecord.id == exchange_id)
                )
            ).tuples()
        )
    counts: dict[str, int] = {}
    for role, _ in identities:
        if role is not None:
            counts[role] = counts.get(role, 0) + 1
    return counts
