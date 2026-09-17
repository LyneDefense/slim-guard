from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.agent_models.errors import ModelGatewayError
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ToolChoice,
    ToolDefinition,
)
from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    MemoryExtractionJobRecord,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.memory.errors import MemoryError
from slim_guard.memory.long_term import (
    LONG_TERM_MEMORY_POLICY_VERSION,
    LongTermMemoryCandidate,
    LongTermMemoryRepository,
    LongTermMemoryWriteAction,
)

logger = logging.getLogger(__name__)

MEMORY_EXTRACTION_POLICY_VERSION = "post-turn-open-text-extraction-v1"
_PROPOSE_TOOL_NAME = "propose_long_term_memories"
_EXTRACTION_INSTRUCTIONS = """
你是 SlimGuard 的异步长期记忆提取器，不负责回复用户。
只从 current_user_message 中提取未来对话仍有帮助、且由用户明确表达的自然语言事实。

保存条件：
1. 事实预计跨轮仍有价值，而不是一次性寒暄、当前问题或助手的说法；
2. 不得把模型推断、专业结论或 final_response 写成用户事实；
3. 保留用户原有的不确定性，不升级为医学诊断；
4. 单次体重、体脂、饮食、运动、提醒以及结构化档案/目标不在这里保存；
5. 临时情况必须使用 temporary 并提供明确的 expires_at；
6. 与 active_memories 语义相同则不返回；发生变化时使用 supersede 并指定旧 ID；
7. evidence_ref 必须等于当前用户消息 ID，evidence_excerpt 必须逐字摘自当前用户原话；
8. category 是开放标签，可以使用 other，不能因为没有预设类别而遗漏重要事实；
9. 极敏感凭证、身份证件、支付信息、精确住址等内容使用 restricted，系统不会自动保存；
10. 没有值得保存的内容时不要调用工具，输出 NO_MEMORY。

completed_actions 和 final_response 只帮助理解本轮发生了什么，不是记忆事实证据。
调用工具最多返回 8 条候选，不要输出思维过程。
""".strip()


class _ProposalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memories: tuple[LongTermMemoryCandidate, ...] = Field(default=(), max_length=8)


@dataclass(frozen=True, slots=True)
class MemoryExtractionJob:
    id: str
    user_id: str
    turn_id: str
    source_item_id: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class MemoryExtractionPayload:
    job: MemoryExtractionJob
    current_user_message: str
    final_response: str | None
    completed_actions: tuple[dict[str, str], ...]


class MemoryExtractionScheduler(Protocol):
    async def enqueue(
        self,
        *,
        user_id: str,
        turn_id: str,
        source_item_id: str,
    ) -> str: ...


class MemoryExtractionJobRepository:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._clock = clock or utc_now

    async def enqueue(
        self,
        *,
        user_id: str,
        turn_id: str,
        source_item_id: str,
    ) -> str:
        now = self._now()
        async with self._database.session() as session, session.begin():
            existing = await session.scalar(
                select(MemoryExtractionJobRecord).where(
                    MemoryExtractionJobRecord.turn_id == turn_id
                )
            )
            if existing is not None:
                return existing.id
            turn = await session.get(AgentTurnRecord, turn_id)
            item = await session.get(AgentItemRecord, source_item_id)
            thread = (
                await session.get(AgentThreadRecord, turn.thread_id) if turn is not None else None
            )
            if (
                turn is None
                or turn.status != "completed"
                or thread is None
                or thread.user_id != user_id
                or item is None
                or item.turn_id != turn_id
                or item.item_type != "user_message"
                or item.status != "completed"
            ):
                raise ValueError(
                    "Memory extraction requires a completed user-message Turn owned by the user"
                )
            row = MemoryExtractionJobRecord(
                user_id=user_id,
                turn_id=turn_id,
                source_item_id=source_item_id,
                status="queued",
                policy_version=MEMORY_EXTRACTION_POLICY_VERSION,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
            return row.id

    async def claim(
        self,
        *,
        limit: int,
        lease: timedelta,
    ) -> tuple[MemoryExtractionJob, ...]:
        now = self._now()
        claimed: list[MemoryExtractionJob] = []
        async with self._database.session() as session, session.begin():
            rows = tuple(
                await session.scalars(
                    select(MemoryExtractionJobRecord)
                    .where(
                        or_(
                            (
                                (MemoryExtractionJobRecord.status == "queued")
                                & (MemoryExtractionJobRecord.available_at <= now)
                            ),
                            (
                                (MemoryExtractionJobRecord.status == "running")
                                & (MemoryExtractionJobRecord.lease_until <= now)
                            ),
                        )
                    )
                    .order_by(
                        MemoryExtractionJobRecord.available_at,
                        MemoryExtractionJobRecord.created_at,
                    )
                    .limit(limit)
                )
            )
            for row in rows:
                result = await session.execute(
                    update(MemoryExtractionJobRecord)
                    .where(
                        MemoryExtractionJobRecord.id == row.id,
                        MemoryExtractionJobRecord.status == row.status,
                    )
                    .values(
                        status="running",
                        attempt_count=row.attempt_count + 1,
                        lease_until=now + lease,
                        error_code=None,
                        error_detail=None,
                        updated_at=now,
                    )
                )
                if cast(CursorResult[Any], result).rowcount != 1:
                    continue
                claimed.append(
                    MemoryExtractionJob(
                        id=row.id,
                        user_id=row.user_id,
                        turn_id=row.turn_id,
                        source_item_id=row.source_item_id,
                        attempt_count=row.attempt_count + 1,
                    )
                )
        return tuple(claimed)

    async def payload(self, job: MemoryExtractionJob) -> MemoryExtractionPayload:
        async with self._database.session() as session:
            items = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .where(AgentItemRecord.turn_id == job.turn_id)
                    .order_by(AgentItemRecord.sequence)
                )
            )
        source_text: str | None = None
        final_response: str | None = None
        actions: list[dict[str, str]] = []
        for item in items:
            try:
                payload = json.loads(item.payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if item.id == job.source_item_id:
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    source_text = text.strip()
            elif item.item_type == "agent_message":
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    final_response = text.strip()
            elif item.item_type == "tool_result":
                action = self._completed_action(payload)
                if action is not None:
                    actions.append(action)
        if source_text is None:
            raise ValueError("Memory extraction source message is unavailable")
        return MemoryExtractionPayload(
            job=job,
            current_user_message=source_text,
            final_response=final_response,
            completed_actions=tuple(actions),
        )

    async def complete(self, job_id: str, *, extracted_count: int) -> None:
        now = self._now()
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(MemoryExtractionJobRecord)
                .where(
                    MemoryExtractionJobRecord.id == job_id,
                    MemoryExtractionJobRecord.status == "running",
                )
                .values(
                    status="completed",
                    extracted_count=extracted_count,
                    lease_until=None,
                    completed_at=now,
                    updated_at=now,
                )
            )

    async def fail(
        self,
        job: MemoryExtractionJob,
        *,
        error: Exception,
        max_attempts: int,
    ) -> None:
        now = self._now()
        terminal = job.attempt_count >= max_attempts
        retry_seconds = min(300, 2 ** min(job.attempt_count, 8))
        async with self._database.session() as session, session.begin():
            await session.execute(
                update(MemoryExtractionJobRecord)
                .where(
                    MemoryExtractionJobRecord.id == job.id,
                    MemoryExtractionJobRecord.status == "running",
                )
                .values(
                    status="failed" if terminal else "queued",
                    available_at=now + timedelta(seconds=retry_seconds),
                    lease_until=None,
                    error_code=type(error).__name__,
                    error_detail=str(error)[:1000],
                    updated_at=now,
                )
            )

    @staticmethod
    def _completed_action(payload: dict[str, Any]) -> dict[str, str] | None:
        execution = payload.get("execution")
        if not isinstance(execution, dict) or execution.get("status") != "succeeded":
            return None
        tool_name = execution.get("tool_name") or payload.get("tool_name")
        if not isinstance(tool_name, str):
            return None
        return {"tool_name": tool_name, "status": "succeeded"}

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Memory extraction clock must be timezone-aware")
        return value.astimezone(UTC)


class MemoryExtractionService:
    """Processes post-response extraction without delaying the user-visible reply."""

    def __init__(
        self,
        *,
        jobs: MemoryExtractionJobRepository,
        memories: LongTermMemoryRepository,
        model: ModelGateway,
        model_name: str,
        interval_seconds: int = 2,
        batch_size: int = 10,
        lease_seconds: int = 120,
        max_attempts: int = 5,
        max_output_tokens: int = 1200,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if min(interval_seconds, batch_size, lease_seconds, max_attempts) < 1:
            raise ValueError("Memory extraction worker settings must be positive")
        self._jobs = jobs
        self._memories = memories
        self._model = model
        self._model_name = model_name
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._lease = timedelta(seconds=lease_seconds)
        self._max_attempts = max_attempts
        self._max_output_tokens = max_output_tokens
        self._clock = clock or utc_now

    async def process_once(self) -> int:
        jobs = await self._jobs.claim(limit=self._batch_size, lease=self._lease)
        for job in jobs:
            try:
                count = await self._process(job)
            except (ModelGatewayError, MemoryError, ValidationError, ValueError, TypeError) as exc:
                logger.warning(
                    "memory_extraction_failed",
                    extra={
                        "job_id": job.id,
                        "attempt_count": job.attempt_count,
                        "error_type": type(exc).__name__,
                    },
                )
                await self._jobs.fail(job, error=exc, max_attempts=self._max_attempts)
            else:
                await self._jobs.complete(job.id, extracted_count=count)
        return len(jobs)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = await self.process_once()
            if processed >= self._batch_size:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _process(self, job: MemoryExtractionJob) -> int:
        payload = await self._jobs.payload(job)
        active = await self._memories.active(job.user_id, limit=100)
        response = await self._model.complete(self._request(payload, active=active))
        candidates = self._candidates(response.message.tool_calls)
        if not candidates:
            return 0
        results = await self._memories.apply(
            user_id=job.user_id,
            source_turn_id=job.turn_id,
            source_item_id=job.source_item_id,
            operation_prefix=job.id,
            candidates=candidates,
        )
        return sum(
            result.action
            in {
                LongTermMemoryWriteAction.CREATED,
                LongTermMemoryWriteAction.SUPERSEDED,
            }
            for result in results
        )

    def _request(
        self,
        payload: MemoryExtractionPayload,
        *,
        active: tuple[Any, ...],
    ) -> ModelRequest:
        request_payload = {
            "current_time": self._now().isoformat(),
            "current_user_message": {
                "evidence_ref": payload.job.source_item_id,
                "content": payload.current_user_message,
            },
            "completed_actions": payload.completed_actions,
            "final_response": payload.final_response,
            "active_memories": [
                {
                    "memory_id": memory.id,
                    "content_text": memory.content_text,
                    "category": memory.category,
                    "durability": memory.durability.value,
                    "expires_at": (
                        memory.expires_at.isoformat() if memory.expires_at is not None else None
                    ),
                }
                for memory in active
            ],
        }
        return ModelRequest(
            purpose=ModelPurpose.MEMORY_INGESTION,
            model=self._model_name,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, content=_EXTRACTION_INSTRUCTIONS),
                ModelMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        request_payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            ),
            tools=(self._proposal_tool(),),
            tool_choice=ToolChoice.AUTO,
            max_output_tokens=self._max_output_tokens,
            temperature=0,
            metadata={
                "memory_policy_version": MEMORY_EXTRACTION_POLICY_VERSION,
                "authority_policy_version": LONG_TERM_MEMORY_POLICY_VERSION,
                "user_id": hashlib.sha256(payload.job.user_id.encode()).hexdigest(),
                "job_id": payload.job.id,
            },
        )

    @staticmethod
    def _candidates(tool_calls: tuple[Any, ...]) -> tuple[LongTermMemoryCandidate, ...]:
        proposals = tuple(call for call in tool_calls if call.name == _PROPOSE_TOOL_NAME)
        if not proposals:
            return ()
        if len(proposals) != 1:
            raise ValueError("Memory extractor must emit at most one proposal tool call")
        return _ProposalEnvelope.model_validate(proposals[0].arguments).memories

    @staticmethod
    def _proposal_tool() -> ToolDefinition:
        return ToolDefinition(
            name=_PROPOSE_TOOL_NAME,
            description="提交经过证据绑定的开放文本长期记忆候选。",
            parameters_json_schema=_ProposalEnvelope.model_json_schema(),
            version=MEMORY_EXTRACTION_POLICY_VERSION,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Memory extraction clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "MEMORY_EXTRACTION_POLICY_VERSION",
    "MemoryExtractionJob",
    "MemoryExtractionJobRepository",
    "MemoryExtractionPayload",
    "MemoryExtractionScheduler",
    "MemoryExtractionService",
]
