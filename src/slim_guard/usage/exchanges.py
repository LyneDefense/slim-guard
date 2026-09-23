"""Read-only exchange index. A Turn's multiple outgoing blocks are one reply."""

from sqlalchemy import and_, exists, func, literal, select, union_all
from sqlalchemy.sql.selectable import Subquery

from slim_guard.db.models import AgentThreadRecord, AgentTurnRecord, MobileAgentRequestRecord
from slim_guard.usage.models import ModelUsageRecord as Call


def exchange_index() -> Subquery:
    turns = select(
        AgentTurnRecord.id.label("id"),
        AgentThreadRecord.user_id.label("user_id"),
        AgentTurnRecord.created_at.label("started_at"),
        AgentTurnRecord.status.label("status"),
        AgentTurnRecord.trigger_type.label("category"),
    ).join(AgentThreadRecord, AgentThreadRecord.id == AgentTurnRecord.thread_id)
    # A request can fail before a Turn is created; keep it visible without inventing usage.
    unstarted = select(
        MobileAgentRequestRecord.id,
        MobileAgentRequestRecord.user_id,
        MobileAgentRequestRecord.created_at,
        MobileAgentRequestRecord.status,
        literal("user_message"),
    ).where(
        MobileAgentRequestRecord.turn_id.is_(None),
        ~exists(select(Call.id).where(Call.request_id == MobileAgentRequestRecord.id)),
    )
    key = func.coalesce(Call.turn_id, Call.request_id, Call.job_id, Call.id)
    standalone = (
        select(
            key.label("id"),
            Call.user_id,
            func.min(Call.started_at),
            literal("see_calls"),
            func.min(Call.category),
        )
        .where(
            ~exists(
                select(AgentTurnRecord.id).where(
                    and_(
                        AgentTurnRecord.id == Call.turn_id,
                        AgentThreadRecord.id == AgentTurnRecord.thread_id,
                        AgentThreadRecord.user_id == Call.user_id,
                    )
                )
            )
        )
        .group_by(key, Call.user_id)
    )
    return union_all(turns, unstarted, standalone).subquery()
