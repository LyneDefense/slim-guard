from __future__ import annotations

from enum import StrEnum


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
    OUTPUT_GUARD = "output_guard"
    AGENT_MESSAGE = "agent_message"
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
