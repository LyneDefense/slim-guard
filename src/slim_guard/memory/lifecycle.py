from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import exists, select, update
from sqlalchemy.engine import CursorResult

from slim_guard.db.models import (
    AgentItemRecord,
    AgentItemRedactionRecord,
    AgentTurnRecord,
    MemoryHandoffRecord,
    OutboundMessage,
    PendingActionRecord,
    ProactiveMessageRecord,
    ToolExecutionRecord,
    UserMemoryFactRecord,
)
from slim_guard.db.session import Database
from slim_guard.tools.contracts import ToolFailure, ToolResult, ToolResultStatus

TRANSCRIPT_REDACTION_POLICY_VERSION = "agent-item-body-redaction-v1"
_TERMINAL_TURNS = ("completed", "failed", "suspended")
_REDACTED_ITEM_TYPES = (
    "user_message",
    "image_attachment",
    "context_snapshot",
    "model_message",
    "tool_call",
    "tool_result",
    "agent_message",
)
_TERMINAL_EXECUTIONS = ("succeeded", "failed")
_TERMINAL_ACTIONS = ("rejected", "expired", "cancelled", "consumed")


@dataclass(frozen=True, slots=True)
class TranscriptScrubResult:
    item_count: int
    tool_execution_count: int
    pending_action_count: int
    outbound_message_count: int
    proactive_message_count: int


@dataclass(frozen=True, slots=True)
class MemoryLifecycleResult:
    transcript: TranscriptScrubResult
    revoked_value_count: int
    expired_fact_count: int
    expired_handoff_count: int


class MemoryLifecycleRepository:
    """Irreversibly removes retained bodies while preserving hashes and audit IDs."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def scrub_transcript_bodies(
        self,
        *,
        before: datetime,
        redacted_at: datetime,
        limit: int = 500,
    ) -> TranscriptScrubResult:
        cutoff = self._aware(before)
        changed_at = self._aware(redacted_at)
        if not 1 <= limit <= 5000:
            raise ValueError("Transcript scrub limit must be between 1 and 5000")
        async with self._database.session() as session, session.begin():
            items = tuple(
                await session.scalars(
                    select(AgentItemRecord)
                    .join(AgentTurnRecord, AgentTurnRecord.id == AgentItemRecord.turn_id)
                    .where(
                        AgentItemRecord.created_at <= cutoff,
                        AgentItemRecord.item_type.in_(_REDACTED_ITEM_TYPES),
                        AgentTurnRecord.status.in_(_TERMINAL_TURNS),
                        ~exists().where(AgentItemRedactionRecord.item_id == AgentItemRecord.id),
                    )
                    .order_by(AgentItemRecord.created_at, AgentItemRecord.id)
                    .limit(limit)
                )
            )
            for item in items:
                original = item.payload_json
                digest = self._sha256(original)
                item.payload_json = self._redacted_item_payload(item.item_type, original)
                session.add(
                    AgentItemRedactionRecord(
                        item_id=item.id,
                        original_payload_sha256=digest,
                        policy_version=TRANSCRIPT_REDACTION_POLICY_VERSION,
                        redacted_at=changed_at,
                    )
                )

            executions = tuple(
                await session.scalars(
                    select(ToolExecutionRecord)
                    .join(AgentTurnRecord, AgentTurnRecord.id == ToolExecutionRecord.turn_id)
                    .where(
                        ToolExecutionRecord.completed_at <= cutoff,
                        ToolExecutionRecord.status.in_(_TERMINAL_EXECUTIONS),
                        AgentTurnRecord.status.in_(_TERMINAL_TURNS),
                        ~ToolExecutionRecord.canonical_arguments_json.like('%"_redacted":true%'),
                    )
                    .order_by(ToolExecutionRecord.completed_at, ToolExecutionRecord.idempotency_key)
                    .limit(limit)
                )
            )
            for execution in executions:
                execution.canonical_arguments_json = self.redacted_json(
                    execution.canonical_arguments_json
                )
                if execution.result_json is not None:
                    execution.result_json = self._redacted_tool_result(execution.result_json)

            actions = tuple(
                await session.scalars(
                    select(PendingActionRecord)
                    .join(AgentTurnRecord, AgentTurnRecord.id == PendingActionRecord.turn_id)
                    .where(
                        PendingActionRecord.created_at <= cutoff,
                        PendingActionRecord.status.in_(_TERMINAL_ACTIONS),
                        AgentTurnRecord.status.in_(_TERMINAL_TURNS),
                        ~PendingActionRecord.canonical_arguments_json.like('%"_redacted":true%'),
                    )
                    .order_by(PendingActionRecord.created_at, PendingActionRecord.id)
                    .limit(limit)
                )
            )
            for action in actions:
                action.canonical_arguments_json = self.redacted_json(
                    action.canonical_arguments_json
                )
            outbound_messages = tuple(
                await session.scalars(
                    select(OutboundMessage)
                    .where(
                        OutboundMessage.completed_at <= cutoff,
                        OutboundMessage.status.in_(
                            ("accepted", "failed", "unknown", "deferred_external_session")
                        ),
                        ~OutboundMessage.content.like("[redacted:sha256=%"),
                    )
                    .order_by(OutboundMessage.completed_at, OutboundMessage.idempotency_key)
                    .limit(limit)
                )
            )
            for message in outbound_messages:
                message.content = self.redacted_text(message.content)
            proactive_messages = tuple(
                await session.scalars(
                    select(ProactiveMessageRecord)
                    .where(
                        ProactiveMessageRecord.completed_at <= cutoff,
                        ProactiveMessageRecord.status.in_(("accepted", "failed", "unknown")),
                        ~ProactiveMessageRecord.content.like("[redacted:sha256=%"),
                    )
                    .order_by(ProactiveMessageRecord.completed_at, ProactiveMessageRecord.job_id)
                    .limit(limit)
                )
            )
            for proactive_message in proactive_messages:
                proactive_message.content = self.redacted_text(proactive_message.content)
            await session.flush()
            return TranscriptScrubResult(
                item_count=len(items),
                tool_execution_count=len(executions),
                pending_action_count=len(actions),
                outbound_message_count=len(outbound_messages),
                proactive_message_count=len(proactive_messages),
            )

    async def purge_revoked_values(self, *, before: datetime) -> int:
        cutoff = self._aware(before)
        async with self._database.session() as session, session.begin():
            result = await session.execute(
                update(UserMemoryFactRecord)
                .where(
                    UserMemoryFactRecord.status == "revoked",
                    UserMemoryFactRecord.ended_at <= cutoff,
                    UserMemoryFactRecord.value_json.is_not(None),
                )
                .values(value_json=None)
            )
            return cast(CursorResult[Any], result).rowcount

    async def expire_due(self, *, at: datetime) -> tuple[int, int]:
        now = self._aware(at)
        async with self._database.session() as session, session.begin():
            fact_result = await session.execute(
                update(UserMemoryFactRecord)
                .where(
                    UserMemoryFactRecord.status == "active",
                    UserMemoryFactRecord.expires_at.is_not(None),
                    UserMemoryFactRecord.expires_at <= now,
                )
                .values(status="expired", ended_at=now)
            )
            handoff_result = await session.execute(
                update(MemoryHandoffRecord)
                .where(
                    MemoryHandoffRecord.status == "active",
                    MemoryHandoffRecord.expires_at <= now,
                )
                .values(status="expired", resolved_at=now)
            )
            return (
                cast(CursorResult[Any], fact_result).rowcount,
                cast(CursorResult[Any], handoff_result).rowcount,
            )

    @classmethod
    def _redacted_item_payload(cls, item_type: str, payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        output: dict[str, Any] = {"redacted": True, "item_type": item_type}
        if item_type in {"user_message", "image_attachment"}:
            for key in ("channel_id", "occurred_at"):
                if isinstance(payload.get(key), str):
                    output[key] = payload[key]
            cls._copy_hash(payload, output, "source_message_id")
            cls._copy_hash(payload, output, "text")
            cls._copy_hash(payload, output, "asset_id")
            if isinstance(payload.get("mime_type"), str):
                output["mime_type"] = payload["mime_type"]
        elif item_type == "agent_message":
            cls._copy_hash(payload, output, "text")
        elif item_type == "context_snapshot":
            for key in ("compiled_at", "input_item_ids", "allowed_tool_names"):
                if key in payload:
                    output[key] = payload[key]
            request = payload.get("request")
            if isinstance(request, dict):
                output["request"] = {
                    key: request[key] for key in ("model", "purpose", "metadata") if key in request
                }
        elif item_type == "model_message":
            for key in (
                "call_index",
                "model",
                "purpose",
                "finish_reason",
                "usage",
                "provider_request_id",
            ):
                if key in payload:
                    output[key] = payload[key]
            message = payload.get("message")
            if isinstance(message, dict):
                output["message"] = cls._redacted_model_message(message)
        elif item_type == "tool_call":
            for key in ("call_index", "tool_call_id", "tool_name"):
                if key in payload:
                    output[key] = payload[key]
            if "arguments" in payload:
                output["arguments_sha256"] = cls._value_sha256(payload["arguments"])
        elif item_type == "tool_result":
            for key in ("tool_call_id", "tool_name", "pending_action_id"):
                if key in payload:
                    output[key] = payload[key]
            execution = payload.get("execution")
            if isinstance(execution, dict):
                result = execution.get("result")
                output["execution"] = {
                    key: execution[key]
                    for key in (
                        "tool_call_id",
                        "tool_name",
                        "tool_version",
                        "idempotency_key",
                        "policy_decision",
                    )
                    if key in execution
                }
                if isinstance(result, dict):
                    failure = result.get("failure")
                    output["execution"]["result"] = {
                        "status": result.get("status"),
                        "source_ids": result.get("source_ids", []),
                        "failure_code": (
                            failure.get("code") if isinstance(failure, dict) else None
                        ),
                        "payload_sha256": cls._value_sha256(result),
                    }
        output["original_payload_sha256"] = cls._sha256(payload_json)
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def _redacted_model_message(cls, message: dict[str, Any]) -> dict[str, Any]:
        output: dict[str, Any] = {"role": message.get("role")}
        if isinstance(message.get("content"), str):
            output["content_sha256"] = cls._sha256(message["content"])
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            output["tool_calls"] = [
                {
                    "id": call.get("id"),
                    "name": call.get("name"),
                    "arguments_sha256": cls._value_sha256(call.get("arguments")),
                }
                for call in calls
                if isinstance(call, dict)
            ]
        return output

    @classmethod
    def _redacted_tool_result(cls, result_json: str) -> str:
        result = ToolResult.model_validate_json(result_json)
        digest = cls._sha256(result_json)
        output = {"_redacted": True, "_redacted_sha256": digest}
        if result.status is ToolResultStatus.SUCCEEDED:
            redacted = ToolResult.success(output=output, source_ids=result.source_ids)
        else:
            assert result.failure is not None
            redacted = ToolResult(
                status=ToolResultStatus.FAILED,
                output=output,
                source_ids=result.source_ids,
                failure=ToolFailure(
                    code=result.failure.code,
                    message="Tool failure detail redacted after retention.",
                    retryable=result.failure.retryable,
                ),
            )
        return redacted.to_model_content()

    @classmethod
    def redacted_json(cls, value: str) -> str:
        return json.dumps(
            {"_redacted": True, "_redacted_sha256": cls._sha256(value)},
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def redacted_text(cls, value: str) -> str:
        return f"[redacted:sha256={cls._sha256(value)}]"

    @classmethod
    def matches_redacted_json(cls, stored_json: str, expected_json: str) -> bool:
        try:
            payload = json.loads(stored_json)
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("_redacted") is True
            and payload.get("_redacted_sha256") == cls._sha256(expected_json)
        )

    @classmethod
    def _copy_hash(
        cls,
        source: dict[str, Any],
        target: dict[str, Any],
        key: str,
    ) -> None:
        if isinstance(source.get(key), str):
            target[f"{key}_sha256"] = cls._sha256(source[key])

    @classmethod
    def _value_sha256(cls, value: Any) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return cls._sha256(canonical)

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Memory lifecycle time must be timezone-aware")
        return value.astimezone(UTC)
