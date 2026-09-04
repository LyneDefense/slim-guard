from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any


class ThreadStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class TurnTrigger(StrEnum):
    USER_MESSAGE = "user_message"
    USER_CONFIRMATION = "user_confirmation"
    DAILY_REMINDER = "daily_reminder"
    WEIGHT_REMINDER = "weight_reminder"
    MEAL_REMINDER = "meal_reminder"
    DAILY_REVIEW = "daily_review"
    WEEKLY_REVIEW = "weekly_review"
    HUMAN_REVIEW_COMPLETED = "human_review_completed"
    DELIVERY_FAILED = "delivery_failed"


class TurnStatus(StrEnum):
    RUNNING = "running"
    WAITING_USER_CONFIRMATION = "waiting_user_confirmation"
    WAITING_HUMAN_REVIEW = "waiting_human_review"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"


class ItemType(StrEnum):
    USER_MESSAGE = "user_message"
    IMAGE_ATTACHMENT = "image_attachment"
    CONTEXT_SNAPSHOT = "context_snapshot"
    MODEL_MESSAGE = "model_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESULT = "approval_result"
    MEMORY_COMPACTION = "memory_compaction"
    MEMORY_INGESTION = "memory_ingestion"
    MEMORY_RECALL = "memory_recall"
    OUTPUT_GUARD = "output_guard"
    AGENT_MESSAGE = "agent_message"
    INVOCATION_STARTED = "invocation_started"
    INVOCATION_RESULT = "invocation_result"
    ARTIFACT_CREATED = "artifact_created"
    WORKFLOW_TRANSITION = "workflow_transition"
    RESPONSE_ADOPTED = "response_adopted"
    RESPONSE_DEGRADED = "response_degraded"
    ERROR = "error"


class ItemStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class PendingActionType(StrEnum):
    USER_CONFIRMATION = "user_confirmation"
    HUMAN_REVIEW = "human_review"


class PendingActionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    CONSUMED = "consumed"


WORKFLOW_TRACE_EVENT_FIELDS: dict[ItemType, frozenset[str]] = {
    ItemType.INVOCATION_STARTED: frozenset(
        {
            "invocation_id",
            "agent_role",
            "agent_version",
            "attempt",
            "parent_invocation_id",
            "input_artifact_ids",
            "allowed_tool_names",
            "privacy_scopes",
            "reason_summary",
            "started_at",
        }
    ),
    ItemType.INVOCATION_RESULT: frozenset(
        {
            "invocation_id",
            "status",
            "output_artifact_id",
            "model_call_count",
            "tool_call_count",
            "total_token_count",
            "failure_code",
            "completed_at",
        }
    ),
    ItemType.ARTIFACT_CREATED: frozenset(
        {
            "artifact_id",
            "artifact_type",
            "producer_role",
            "schema_version",
            "parent_artifact_ids",
            "payload_sha256",
        }
    ),
    ItemType.WORKFLOW_TRANSITION: frozenset(
        {
            "from_node",
            "to_node",
            "transition_type",
            "reason_code",
            "attempt",
        }
    ),
    ItemType.RESPONSE_ADOPTED: frozenset({"artifact_id", "mode", "final"}),
    ItemType.RESPONSE_DEGRADED: frozenset({"artifact_id", "reason_code", "fallback_type"}),
}

WORKFLOW_TRACE_ITEM_TYPES = frozenset(WORKFLOW_TRACE_EVENT_FIELDS)

_OPTIONAL_STRING_FIELDS: dict[ItemType, frozenset[str]] = {
    ItemType.INVOCATION_STARTED: frozenset({"parent_invocation_id"}),
    ItemType.INVOCATION_RESULT: frozenset({"output_artifact_id", "failure_code"}),
    ItemType.RESPONSE_DEGRADED: frozenset({"artifact_id"}),
}
_STRING_LIST_FIELDS = frozenset(
    {
        "input_artifact_ids",
        "allowed_tool_names",
        "privacy_scopes",
        "parent_artifact_ids",
    }
)
_POSITIVE_INTEGER_FIELDS = frozenset({"attempt"})
_NONNEGATIVE_INTEGER_FIELDS = frozenset(
    {"model_call_count", "tool_call_count", "total_token_count"}
)
_TIMESTAMP_FIELDS = frozenset({"started_at", "completed_at"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class WorkflowTraceEvent:
    """A privacy-minimized, JSON-safe event for the multi-agent workflow.

    Workflow event payloads deliberately use an allowlist. Raw prompts, messages,
    artifact payloads, model output and chain-of-thought-like fields are never copied
    into the persisted event, even if a caller includes them in its input mapping.
    """

    item_type: ItemType
    status: ItemStatus
    payload: dict[str, Any]

    @classmethod
    def build(
        cls,
        *,
        event_type: ItemType | str,
        payload: Mapping[str, Any],
        status: ItemStatus | str = ItemStatus.COMPLETED,
    ) -> WorkflowTraceEvent:
        item_type = ItemType(event_type)
        if item_type not in WORKFLOW_TRACE_ITEM_TYPES:
            raise ValueError(f"Not a workflow trace event type: {item_type.value}")
        return cls(
            item_type=item_type,
            status=ItemStatus(status),
            payload=_normalize_workflow_trace_payload(item_type, payload),
        )


def _normalize_workflow_trace_payload(
    item_type: ItemType,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    allowed_fields = WORKFLOW_TRACE_EVENT_FIELDS[item_type]
    optional_fields = _OPTIONAL_STRING_FIELDS.get(item_type, frozenset())
    missing_fields = allowed_fields - optional_fields - payload.keys()
    if missing_fields:
        joined = ", ".join(sorted(missing_fields))
        raise ValueError(f"Missing {item_type.value} trace fields: {joined}")

    normalized: dict[str, Any] = {}
    for field in allowed_fields:
        value = payload.get(field)
        if field in optional_fields and value is None:
            normalized[field] = None
        elif field in _STRING_LIST_FIELDS:
            normalized[field] = _string_list(field, value)
        elif field in _POSITIVE_INTEGER_FIELDS:
            normalized[field] = _integer(field, value, minimum=1)
        elif field in _NONNEGATIVE_INTEGER_FIELDS:
            normalized[field] = _integer(field, value, minimum=0)
        elif field == "final":
            if not isinstance(value, bool):
                raise ValueError("Workflow trace field final must be a boolean")
            normalized[field] = value
        elif field in _TIMESTAMP_FIELDS:
            normalized[field] = _timestamp(field, value)
        else:
            normalized[field] = _string(field, value)

    digest = normalized.get("payload_sha256")
    if isinstance(digest, str) and _SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError("Workflow trace field payload_sha256 must be lowercase SHA-256 hex")
    return normalized


def _string(field: str, value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        raise ValueError(f"Workflow trace field {field} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"Workflow trace field {field} cannot be blank")
    limit = 280 if field == "reason_summary" else 180
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _string_list(field: str, value: Any) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"Workflow trace field {field} must be a sequence of strings")
    return [_string(field, item) for item in value]


def _integer(field: str, value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"Workflow trace field {field} must be an integer greater than or equal to {minimum}"
        )
    return int(value)


def _timestamp(field: str, value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    rendered = _string(field, value)
    try:
        datetime.fromisoformat(rendered)
    except ValueError as error:
        raise ValueError(f"Workflow trace field {field} must be an ISO timestamp") from error
    return rendered
