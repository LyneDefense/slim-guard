from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemoryNotFound,
    MemorySourceMismatch,
)
from slim_guard.memory.long_term import (
    LongTermMemoryCandidate,
    LongTermMemoryDurability,
    LongTermMemoryOperation,
    LongTermMemoryRef,
    LongTermMemoryRepository,
    LongTermMemorySensitivity,
)
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

REMEMBER_LONG_TERM_MEMORY_TOOL_NAME = "remember_long_term_memory"
LIST_LONG_TERM_MEMORIES_TOOL_NAME = "list_long_term_memories"
FORGET_LONG_TERM_MEMORY_TOOL_NAME = "forget_long_term_memory"
CLEAR_LONG_TERM_MEMORIES_TOOL_NAME = "clear_long_term_memories"
LONG_TERM_MEMORY_TOOL_VERSION = "v1"


class RememberLongTermMemoryArguments(ToolArguments):
    content_text: str = Field(min_length=1, max_length=500)
    category: str = Field(default="other", min_length=1, max_length=64)
    durability: Literal["long_term", "temporary"] = "long_term"
    expires_at: str | None = Field(default=None, max_length=64)
    sensitivity: Literal["normal", "health"] = "normal"
    evidence_excerpt: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def temporary_requires_expiry(self) -> RememberLongTermMemoryArguments:
        if self.durability == "temporary" and self.expires_at is None:
            raise ValueError("Temporary memory requires expires_at")
        return self


class ListLongTermMemoriesArguments(ToolArguments):
    limit: int = Field(default=30, ge=1, le=100)


class ForgetLongTermMemoryArguments(ToolArguments):
    memory_id: str = Field(min_length=1, max_length=128)
    evidence_excerpt: str = Field(min_length=1, max_length=512)


class ClearLongTermMemoriesArguments(ToolArguments):
    scope: Literal["conversational_long_term"]
    evidence_excerpt: str = Field(min_length=1, max_length=512)


class LongTermMemoryToolHandlers:
    def __init__(self, repository: LongTermMemoryRepository) -> None:
        self._repository = repository

    async def remember(
        self,
        context: ToolContext,
        arguments: RememberLongTermMemoryArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_long_term_memory_identity",
                message="Remembering requires a trusted current user message.",
            )
        try:
            expires_at = self._expiry(arguments.expires_at)
            result = await self._repository.apply(
                user_id=context.user_id,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                operation_prefix=context.execution_idempotency_key,
                candidates=(
                    LongTermMemoryCandidate(
                        content_text=arguments.content_text,
                        category=arguments.category,
                        durability=LongTermMemoryDurability(arguments.durability),
                        expires_at=expires_at,
                        sensitivity=LongTermMemorySensitivity(arguments.sensitivity),
                        evidence_ref=context.source_item_id,
                        evidence_excerpt=arguments.evidence_excerpt,
                        operation=LongTermMemoryOperation.CREATE,
                    ),
                ),
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="long_term_memory_evidence_mismatch",
                message="The memory must quote the current user message exactly.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="long_term_memory_source_mismatch",
                message="The memory source could not be verified.",
            )
        except MemoryCollision:
            return ToolResult.failed(
                code="long_term_memory_collision",
                message="The memory conflicted with another persisted update.",
                retryable=True,
            )
        except ValueError:
            return ToolResult.failed(
                code="invalid_long_term_memory",
                message="The memory or expiry is outside the supported schema.",
            )
        memory = result[0].memory
        return ToolResult.success(
            output={
                "action": result[0].action.value,
                "memory": self._output(memory) if memory is not None else None,
            },
            source_ids=(memory.id,) if memory is not None else (),
        )

    async def list_memories(
        self,
        context: ToolContext,
        arguments: ListLongTermMemoriesArguments,
    ) -> ToolResult:
        memories = await self._repository.active(
            context.user_id,
            limit=arguments.limit,
        )
        return ToolResult.success(
            output={"memories": [self._output(memory) for memory in memories]},
            source_ids=tuple(memory.id for memory in memories),
        )

    async def forget(
        self,
        context: ToolContext,
        arguments: ForgetLongTermMemoryArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_long_term_memory_identity",
                message="Forgetting requires a trusted current user message.",
            )
        try:
            memory, changed = await self._repository.revoke(
                user_id=context.user_id,
                memory_id=arguments.memory_id,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                evidence_excerpt=arguments.evidence_excerpt,
                operation_id=context.execution_idempotency_key,
            )
        except MemoryNotFound:
            return ToolResult.failed(
                code="long_term_memory_not_found",
                message="That memory is not visible to the current user.",
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="long_term_memory_evidence_mismatch",
                message="The forget request must quote the current user message exactly.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="long_term_memory_source_mismatch",
                message="The forget request source could not be verified.",
            )
        return ToolResult.success(
            output={"memory": self._output(memory), "changed": changed},
            source_ids=(memory.id,),
        )

    async def clear_memories(
        self,
        context: ToolContext,
        arguments: ClearLongTermMemoriesArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_long_term_memory_identity",
                message="Clearing memories requires a trusted current user message.",
            )
        try:
            result = await self._repository.revoke_all(
                user_id=context.user_id,
                source_turn_id=context.turn_id,
                source_item_id=context.source_item_id,
                evidence_excerpt=arguments.evidence_excerpt,
                operation_id=context.execution_idempotency_key,
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="long_term_memory_evidence_mismatch",
                message="The clear request must quote the current user message exactly.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="long_term_memory_source_mismatch",
                message="The clear request source could not be verified.",
            )
        return ToolResult.success(
            output={
                "scope": arguments.scope,
                "revoked_count": result.revoked_count,
                "excluded": [
                    "structured_profile_goal_constraint",
                    "weight_records",
                    "body_fat_records",
                    "meal_records",
                    "exercise_records",
                    "transcripts_and_audit",
                ],
            },
            source_ids=result.memory_ids,
        )

    @staticmethod
    def _expiry(raw: str | None) -> datetime | None:
        if raw is None:
            return None
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("Memory expiry must include a timezone")
        return value

    @staticmethod
    def _output(memory: LongTermMemoryRef) -> dict[str, Any]:
        return {
            "memory_id": memory.id,
            "content_text": memory.content_text,
            "category": memory.category,
            "durability": memory.durability.value,
            "status": memory.status,
            "expires_at": (
                memory.expires_at.isoformat() if memory.expires_at is not None else None
            ),
        }


def long_term_memory_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=REMEMBER_LONG_TERM_MEMORY_TOOL_NAME,
            description=(
                "Synchronously save an open-text conversational memory only when the current "
                "user explicitly asks to remember a cross-turn fact. Do not duplicate weight, "
                "body-fat, meal, exercise, reminder, or structured profile/goal records. "
                "evidence_excerpt must be copied exactly from the current user message."
            ),
            version=LONG_TERM_MEMORY_TOOL_VERSION,
            arguments_model=RememberLongTermMemoryArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=LIST_LONG_TERM_MEMORIES_TOOL_NAME,
            description=(
                "List active open-text conversational memories for the current user. Use "
                "when the user asks what is remembered or before forgetting an exact item."
            ),
            version=LONG_TERM_MEMORY_TOOL_VERSION,
            arguments_model=ListLongTermMemoriesArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=FORGET_LONG_TERM_MEMORY_TOOL_NAME,
            description=(
                "Revoke one exact open-text memory owned by the current user. Never guess "
                "memory_id; list memories first when needed. evidence_excerpt must quote the "
                "current user's forget request exactly."
            ),
            version=LONG_TERM_MEMORY_TOOL_VERSION,
            arguments_model=ForgetLongTermMemoryArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=CLEAR_LONG_TERM_MEMORIES_TOOL_NAME,
            description=(
                "Revoke every active open-text conversational memory for the current user "
                "after explicit confirmation. This never removes structured profile/goal "
                "facts, health records, transcripts, or audit history. scope must be "
                "conversational_long_term and evidence_excerpt must quote the user's "
                "original clear request exactly."
            ),
            version=LONG_TERM_MEMORY_TOOL_VERSION,
            arguments_model=ClearLongTermMemoriesArguments,
            effect_level=ToolEffectLevel.SENSITIVE_WRITE,
            idempotent=True,
            requires_confirmation=True,
            timeout_seconds=5,
        ),
    )


def long_term_memory_tool_executors(
    repository: LongTermMemoryRepository,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = LongTermMemoryToolHandlers(repository)
    return {
        REMEMBER_LONG_TERM_MEMORY_TOOL_NAME: ToolExecutor(
            arguments_model=RememberLongTermMemoryArguments,
            handler=handlers.remember,
        ),
        LIST_LONG_TERM_MEMORIES_TOOL_NAME: ToolExecutor(
            arguments_model=ListLongTermMemoriesArguments,
            handler=handlers.list_memories,
        ),
        FORGET_LONG_TERM_MEMORY_TOOL_NAME: ToolExecutor(
            arguments_model=ForgetLongTermMemoryArguments,
            handler=handlers.forget,
        ),
        CLEAR_LONG_TERM_MEMORIES_TOOL_NAME: ToolExecutor(
            arguments_model=ClearLongTermMemoriesArguments,
            handler=handlers.clear_memories,
        ),
    }


__all__ = [
    "CLEAR_LONG_TERM_MEMORIES_TOOL_NAME",
    "FORGET_LONG_TERM_MEMORY_TOOL_NAME",
    "LIST_LONG_TERM_MEMORIES_TOOL_NAME",
    "LONG_TERM_MEMORY_TOOL_VERSION",
    "REMEMBER_LONG_TERM_MEMORY_TOOL_NAME",
    "ClearLongTermMemoriesArguments",
    "ForgetLongTermMemoryArguments",
    "ListLongTermMemoriesArguments",
    "LongTermMemoryToolHandlers",
    "RememberLongTermMemoryArguments",
    "long_term_memory_tool_definitions",
    "long_term_memory_tool_executors",
]
