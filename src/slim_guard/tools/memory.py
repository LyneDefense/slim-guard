from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator

from slim_guard.memory.contracts import (
    MemoryBulkRevokeCommand,
    MemoryFactInput,
    MemoryFactRef,
    MemoryKey,
    MemoryRevokeCommand,
    MemoryWriteCommand,
)
from slim_guard.memory.errors import (
    MemoryCollision,
    MemoryEvidenceMismatch,
    MemoryNotFound,
    MemorySourceMismatch,
    MemoryStaleEvidence,
)
from slim_guard.memory.handoff import (
    HandoffRef,
    HandoffRepository,
    HandoffResolveCommand,
    HandoffUpsertCommand,
)
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolArguments, ToolContext, ToolEffectLevel, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.registry import RegisteredTool

SET_COACHING_PROFILE_TOOL_NAME = "set_coaching_profile"
SET_BODY_PROFILE_TOOL_NAME = "set_body_profile"
SET_EXERCISE_PROFILE_TOOL_NAME = "set_exercise_profile"
UPSERT_FOOD_PREFERENCE_TOOL_NAME = "upsert_food_preference"
UPSERT_EXERCISE_PREFERENCE_TOOL_NAME = "upsert_exercise_preference"
LIST_USER_MEMORIES_TOOL_NAME = "list_user_memories"
FORGET_USER_MEMORY_TOOL_NAME = "forget_user_memory"
SET_WEIGHT_GOAL_TOOL_NAME = "set_weight_goal"
SET_BODY_FAT_GOAL_TOOL_NAME = "set_body_fat_goal"
SET_BEHAVIOR_GOAL_TOOL_NAME = "set_behavior_goal"
RECORD_USER_CONSTRAINT_TOOL_NAME = "record_user_constraint"
SET_CONVERSATION_HANDOFF_TOOL_NAME = "set_conversation_handoff"
RESOLVE_CONVERSATION_HANDOFF_TOOL_NAME = "resolve_conversation_handoff"
CLEAR_USER_MEMORIES_TOOL_NAME = "clear_user_memories"
MEMORY_TOOL_VERSION = "v7"

_GRAMS_PER_UNIT = {
    "kg": Decimal("1000"),
    "jin": Decimal("500"),
    "lb": Decimal("453.59237"),
}
_UNIT_EVIDENCE = {
    "kg": ("kg", "公斤", "千克"),
    "jin": ("斤",),
    "lb": ("lb", "磅"),
}
_MILLIMETERS_PER_HEIGHT_UNIT = {
    "cm": Decimal("10"),
    "m": Decimal("1000"),
    "in": Decimal("25.4"),
}
_HEIGHT_UNIT_EVIDENCE = {
    "cm": ("cm", "厘米", "公分"),
    "m": ("m", "米"),
    "in": ("in", "英寸", "inch", "inches"),
}


def _number_in_evidence(value: str, evidence: str) -> bool:
    value_pattern = rf"(?<![\d.]){re.escape(value)}(?![\d.])"
    return re.search(value_pattern, evidence) is not None


def _unit_in_evidence(
    *,
    selected_unit: str,
    default_unit: str,
    aliases: Mapping[str, tuple[str, ...]],
    evidence: str,
) -> bool:
    lowered = evidence.lower()
    candidates = sorted(
        (
            (unit, alias.lower())
            for unit, unit_aliases in aliases.items()
            for alias in unit_aliases
        ),
        key=lambda candidate: len(candidate[1]),
        reverse=True,
    )
    occupied: set[int] = set()
    matched_units: set[str] = set()
    for unit, alias in candidates:
        for start, end in _unit_alias_spans(alias, lowered):
            span = set(range(start, end))
            if occupied.isdisjoint(span):
                matched_units.add(unit)
                occupied.update(span)
    return selected_unit in matched_units if matched_units else selected_unit == default_unit


def _unit_alias_spans(alias: str, lowered: str) -> tuple[tuple[int, int], ...]:
    pattern = (
        rf"(?<![a-z]){re.escape(alias)}(?![a-z])"
        if alias.isascii()
        else re.escape(alias)
    )
    return tuple((match.start(), match.end()) for match in re.finditer(pattern, lowered))


class MemoryEvidenceArguments(ToolArguments):
    evidence_excerpt: str = Field(min_length=1, max_length=512)
    evidence_ref: str | None = Field(default=None, min_length=1, max_length=128)


class SetCoachingProfileArguments(MemoryEvidenceArguments):
    preferred_name: str | None = Field(default=None, min_length=1, max_length=64)
    response_style: Literal["concise", "detailed", "gentle", "direct"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> SetCoachingProfileArguments:
        if self.preferred_name is None and self.response_style is None:
            raise ValueError("Coaching profile update contains no changes")
        return self


class SetBodyProfileArguments(MemoryEvidenceArguments):
    height_value: float = Field(gt=0)
    height_unit: Literal["cm", "m", "in"] = "cm"


class SetExerciseProfileArguments(MemoryEvidenceArguments):
    habit_summary: str = Field(min_length=1, max_length=300)


class UpsertFoodPreferenceArguments(MemoryEvidenceArguments):
    item: str = Field(min_length=1, max_length=128)
    stance: Literal["like", "dislike", "avoid"]


class UpsertExercisePreferenceArguments(MemoryEvidenceArguments):
    activity: str = Field(min_length=1, max_length=128)
    stance: Literal["like", "dislike", "avoid"]


class ListUserMemoriesArguments(ToolArguments):
    key: Literal[
        "identity.preferred_name",
        "profile.height",
        "profile.exercise_habit",
        "coaching.response_style",
        "food.preference",
        "exercise.preference",
        "goal.target_weight",
        "goal.target_body_fat",
        "goal.behavior",
        "constraint.dietary",
        "constraint.exercise",
        "constraint.health_context",
    ] | None = None
    limit: int = Field(default=30, ge=1, le=100)


class ForgetUserMemoryArguments(ToolArguments):
    memory_id: str = Field(min_length=1, max_length=128)


class SetWeightGoalArguments(MemoryEvidenceArguments):
    value: float = Field(gt=0)
    unit: Literal["kg", "jin", "lb"] = "kg"
    target_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SetBodyFatGoalArguments(MemoryEvidenceArguments):
    value: float = Field(gt=0)


class SetBehaviorGoalArguments(MemoryEvidenceArguments):
    kind: Literal[
        "weekly_exercise_sessions",
        "daily_steps",
        "daily_meal_checkins",
    ]
    target: int = Field(ge=1, le=200_000)


class RecordUserConstraintArguments(MemoryEvidenceArguments):
    category: Literal["dietary", "exercise", "health_context"]
    subject: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=500)


class SetConversationHandoffArguments(ToolArguments):
    objective: str = Field(min_length=1, max_length=300)
    unresolved: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1,
        max_length=5,
    )
    evidence_excerpt: str = Field(min_length=1, max_length=512)


class ResolveConversationHandoffArguments(ToolArguments):
    handoff_id: str = Field(min_length=1, max_length=128)


class ClearUserMemoriesArguments(ToolArguments):
    scope: Literal["profile_goal_constraint"]
    evidence_excerpt: str = Field(min_length=1, max_length=512)


class MemoryToolHandlers:
    def __init__(
        self,
        repository: MemoryRepository,
        handoffs: HandoffRepository | None = None,
    ) -> None:
        self._repository = repository
        self._handoffs = handoffs

    async def set_coaching_profile(
        self,
        context: ToolContext,
        arguments: SetCoachingProfileArguments,
    ) -> ToolResult:
        facts: list[MemoryFactInput] = []
        if arguments.preferred_name is not None:
            if arguments.preferred_name.strip() not in arguments.evidence_excerpt:
                return self._value_not_in_evidence()
            facts.append(
                MemoryFactInput(
                    key=MemoryKey.PREFERRED_NAME,
                    value={"name": arguments.preferred_name},
                )
            )
        if arguments.response_style is not None:
            facts.append(
                MemoryFactInput(
                    key=MemoryKey.RESPONSE_STYLE,
                    value={"style": arguments.response_style},
                )
            )
        return await self._write(
            context,
            facts=tuple(facts),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def upsert_food_preference(
        self,
        context: ToolContext,
        arguments: UpsertFoodPreferenceArguments,
    ) -> ToolResult:
        if arguments.item.strip() not in arguments.evidence_excerpt:
            return self._value_not_in_evidence()
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.FOOD_PREFERENCE,
                    value={"item": arguments.item, "stance": arguments.stance},
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def set_body_profile(
        self,
        context: ToolContext,
        arguments: SetBodyProfileArguments,
    ) -> ToolResult:
        value_text = format(Decimal(str(arguments.height_value)).normalize(), "f")
        if not _number_in_evidence(value_text, arguments.evidence_excerpt) or not (
            _unit_in_evidence(
                selected_unit=arguments.height_unit,
                default_unit="cm",
                aliases=_HEIGHT_UNIT_EVIDENCE,
                evidence=arguments.evidence_excerpt,
            )
        ):
            return self._value_not_in_evidence()
        millimeters = int(
            (
                Decimal(str(arguments.height_value))
                * _MILLIMETERS_PER_HEIGHT_UNIT[arguments.height_unit]
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.HEIGHT,
                    value={"millimeters": millimeters},
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def set_exercise_profile(
        self,
        context: ToolContext,
        arguments: SetExerciseProfileArguments,
    ) -> ToolResult:
        if arguments.habit_summary.strip() not in arguments.evidence_excerpt:
            return self._value_not_in_evidence()
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.EXERCISE_HABIT,
                    value={"statement": arguments.habit_summary},
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def upsert_exercise_preference(
        self,
        context: ToolContext,
        arguments: UpsertExercisePreferenceArguments,
    ) -> ToolResult:
        if arguments.activity.strip() not in arguments.evidence_excerpt:
            return self._value_not_in_evidence()
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.EXERCISE_PREFERENCE,
                    value={
                        "activity": arguments.activity,
                        "stance": arguments.stance,
                    },
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def set_weight_goal(
        self,
        context: ToolContext,
        arguments: SetWeightGoalArguments,
    ) -> ToolResult:
        value_text = format(Decimal(str(arguments.value)).normalize(), "f")
        if not _number_in_evidence(value_text, arguments.evidence_excerpt) or not (
            _unit_in_evidence(
                selected_unit=arguments.unit,
                default_unit="kg",
                aliases=_UNIT_EVIDENCE,
                evidence=arguments.evidence_excerpt,
            )
        ):
            return self._value_not_in_evidence()
        grams = int(
            (Decimal(str(arguments.value)) * _GRAMS_PER_UNIT[arguments.unit]).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.TARGET_WEIGHT,
                    value={"grams": grams, "target_date": arguments.target_date},
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def set_body_fat_goal(
        self,
        context: ToolContext,
        arguments: SetBodyFatGoalArguments,
    ) -> ToolResult:
        value_text = format(Decimal(str(arguments.value)).normalize(), "f")
        if not _number_in_evidence(value_text, arguments.evidence_excerpt):
            return self._value_not_in_evidence()
        basis_points = int(
            (Decimal(str(arguments.value)) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.TARGET_BODY_FAT,
                    value={"basis_points": basis_points},
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def set_behavior_goal(
        self,
        context: ToolContext,
        arguments: SetBehaviorGoalArguments,
    ) -> ToolResult:
        if str(arguments.target) not in arguments.evidence_excerpt:
            return self._value_not_in_evidence()
        period = "week" if arguments.kind == "weekly_exercise_sessions" else "day"
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=MemoryKey.BEHAVIOR_GOAL,
                    value={
                        "kind": arguments.kind,
                        "target": arguments.target,
                        "period": period,
                    },
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def record_user_constraint(
        self,
        context: ToolContext,
        arguments: RecordUserConstraintArguments,
    ) -> ToolResult:
        if (
            arguments.subject.strip() not in arguments.statement
            or arguments.statement.strip() not in arguments.evidence_excerpt
        ):
            return self._value_not_in_evidence()
        key = {
            "dietary": MemoryKey.DIETARY_CONSTRAINT,
            "exercise": MemoryKey.EXERCISE_CONSTRAINT,
            "health_context": MemoryKey.HEALTH_CONTEXT,
        }[arguments.category]
        return await self._write(
            context,
            facts=(
                MemoryFactInput(
                    key=key,
                    value={
                        "subject": arguments.subject,
                        "statement": arguments.statement,
                    },
                ),
            ),
            evidence_excerpt=arguments.evidence_excerpt,
            evidence_ref=arguments.evidence_ref,
        )

    async def list_user_memories(
        self,
        context: ToolContext,
        arguments: ListUserMemoriesArguments,
    ) -> ToolResult:
        key = MemoryKey(arguments.key) if arguments.key is not None else None
        facts = await self._repository.active(context.user_id, key=key, limit=arguments.limit)
        return ToolResult.success(
            output={"memories": [self._output(fact) for fact in facts]},
            source_ids=tuple(fact.id for fact in facts),
        )

    async def forget_user_memory(
        self,
        context: ToolContext,
        arguments: ForgetUserMemoryArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_memory_execution_identity",
                message="Forgetting a memory requires a trusted current user message.",
            )
        try:
            result = await self._repository.revoke(
                MemoryRevokeCommand(
                    user_id=context.user_id,
                    memory_id=arguments.memory_id,
                    operation_id=context.execution_idempotency_key,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    source_tool_call_id=context.tool_call_id,
                )
            )
        except MemoryNotFound:
            return ToolResult.failed(
                code="memory_not_found",
                message="That memory is not visible to the current user.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="memory_source_mismatch",
                message="The memory change source could not be verified.",
            )
        return ToolResult.success(
            output={"memory": self._output(result.fact), "changed": result.changed},
            source_ids=(result.fact.id,),
        )

    async def set_conversation_handoff(
        self,
        context: ToolContext,
        arguments: SetConversationHandoffArguments,
    ) -> ToolResult:
        if self._handoffs is None:
            return self._handoff_unavailable()
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_handoff_execution_identity",
                message="Saving a handoff requires a trusted current user message.",
            )
        try:
            handoff = await self._handoffs.upsert(
                HandoffUpsertCommand(
                    user_id=context.user_id,
                    thread_id=context.thread_id,
                    objective=arguments.objective,
                    unresolved=tuple(arguments.unresolved),
                    evidence_excerpt=arguments.evidence_excerpt,
                    operation_id=context.execution_idempotency_key,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    source_tool_call_id=context.tool_call_id,
                )
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="handoff_evidence_mismatch",
                message="The handoff must be grounded in this user message.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="handoff_source_mismatch",
                message="The handoff source could not be verified.",
            )
        except MemoryCollision:
            return ToolResult.failed(
                code="handoff_collision",
                message="The handoff conflicted with another persisted update.",
                retryable=True,
            )
        except ValidationError:
            return ToolResult.failed(
                code="invalid_handoff_value",
                message="The handoff summary is outside its supported schema.",
            )
        return ToolResult.success(
            output={"handoff": self._handoff_output(handoff)},
            source_ids=(handoff.id,),
        )

    async def resolve_conversation_handoff(
        self,
        context: ToolContext,
        arguments: ResolveConversationHandoffArguments,
    ) -> ToolResult:
        if self._handoffs is None:
            return self._handoff_unavailable()
        if context.source_item_id is None:
            return ToolResult.failed(
                code="missing_handoff_execution_identity",
                message="Resolving a handoff requires a trusted current user message.",
            )
        try:
            handoff, changed = await self._handoffs.resolve(
                HandoffResolveCommand(
                    user_id=context.user_id,
                    handoff_id=arguments.handoff_id,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                )
            )
        except MemoryNotFound:
            return ToolResult.failed(
                code="handoff_not_found",
                message="That handoff is not visible to the current user.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="handoff_source_mismatch",
                message="The handoff source could not be verified.",
            )
        return ToolResult.success(
            output={"handoff": self._handoff_output(handoff), "changed": changed},
            source_ids=(handoff.id,),
        )

    async def clear_user_memories(
        self,
        context: ToolContext,
        arguments: ClearUserMemoriesArguments,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_memory_execution_identity",
                message="Clearing memories requires a trusted current user message.",
            )
        try:
            result = await self._repository.revoke_all(
                MemoryBulkRevokeCommand(
                    user_id=context.user_id,
                    scope=arguments.scope,
                    evidence_excerpt=arguments.evidence_excerpt,
                    operation_id=context.execution_idempotency_key,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    source_tool_call_id=context.tool_call_id,
                )
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="memory_evidence_mismatch",
                message="The clear request must be grounded in this user message.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="memory_source_mismatch",
                message="The memory clear source could not be verified.",
            )
        except MemoryCollision:
            return ToolResult.failed(
                code="memory_collision",
                message="The memory clear conflicted with another persisted update.",
                retryable=True,
            )
        return ToolResult.success(
            output={
                "scope": result.scope,
                "revoked_count": result.revoked_count,
                "excluded": [
                    "weight_records",
                    "meal_records",
                    "exercise_records",
                    "message_idempotency_metadata",
                ],
            },
            source_ids=result.memory_ids,
        )

    async def _write(
        self,
        context: ToolContext,
        *,
        facts: tuple[MemoryFactInput, ...],
        evidence_excerpt: str,
        evidence_ref: str | None,
    ) -> ToolResult:
        if context.execution_idempotency_key is None or context.source_item_id is None:
            return ToolResult.failed(
                code="missing_memory_execution_identity",
                message="Saving a memory requires a trusted current user message.",
            )
        if (
            evidence_ref is not None
            and evidence_ref not in context.trusted_evidence_item_ids
        ):
            return ToolResult.failed(
                code="memory_evidence_not_visible",
                message=(
                    "Historical memory evidence must come from a user message visible in "
                    "this turn's working memory."
                ),
            )
        try:
            result = await self._repository.write(
                MemoryWriteCommand(
                    user_id=context.user_id,
                    facts=facts,
                    evidence_excerpt=evidence_excerpt,
                    operation_id=context.execution_idempotency_key,
                    source_turn_id=context.turn_id,
                    source_item_id=context.source_item_id,
                    evidence_item_id=evidence_ref,
                    source_tool_call_id=context.tool_call_id,
                )
            )
        except MemoryEvidenceMismatch:
            return ToolResult.failed(
                code="memory_evidence_mismatch",
                message="The memory must be grounded in an exact excerpt of this user message.",
            )
        except MemorySourceMismatch:
            return ToolResult.failed(
                code="memory_source_mismatch",
                message="The memory source could not be verified.",
            )
        except MemoryStaleEvidence:
            return ToolResult.failed(
                code="memory_stale_evidence",
                message=(
                    "That historical statement conflicts with a newer saved value. Ask the "
                    "user which value is current."
                ),
            )
        except MemoryCollision:
            return ToolResult.failed(
                code="memory_collision",
                message="The memory conflicted with another persisted update.",
                retryable=True,
            )
        except ValidationError:
            return ToolResult.failed(
                code="invalid_memory_value",
                message="The memory value is outside its supported schema or safe range.",
            )
        return ToolResult.success(
            output={
                "memories": [self._output(fact) for fact in result.facts],
                "created_count": result.created_count,
            },
            source_ids=tuple(fact.id for fact in result.facts),
        )

    @staticmethod
    def _output(fact: MemoryFactRef) -> dict[str, Any]:
        return {
            "memory_id": fact.id,
            "key": fact.key.value,
            "value": fact.value,
            "status": fact.status.value,
            "assertion": fact.assertion.value,
        }

    @staticmethod
    def _handoff_output(handoff: HandoffRef) -> dict[str, Any]:
        return {
            "handoff_id": handoff.id,
            "status": handoff.status,
            "objective": handoff.objective,
            "unresolved": list(handoff.unresolved),
            "expires_at": handoff.expires_at.isoformat(),
        }

    @staticmethod
    def _handoff_unavailable() -> ToolResult:
        return ToolResult.failed(
            code="handoff_repository_unavailable",
            message="Conversation handoff storage is unavailable.",
        )

    @staticmethod
    def _value_not_in_evidence() -> ToolResult:
        return ToolResult.failed(
            code="memory_value_not_in_evidence",
            message="The remembered entity must appear exactly in the evidence excerpt.",
        )


def memory_tool_definitions() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            name=SET_COACHING_PROFILE_TOOL_NAME,
            description=(
                "Save the user's explicitly stated preferred name or response style. Copy "
                "evidence_excerpt exactly from a user message. For historical evidence, pass "
                "its evidence_ref from working memory. Never infer profile attributes."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetCoachingProfileArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_BODY_PROFILE_TOOL_NAME,
            description=(
                "Save the user's explicitly stated height as durable profile data. If the "
                "current user refers unambiguously to a historical user message, copy its "
                "exact evidence_excerpt and evidence_ref from working memory; do not ask them "
                "to repeat it. If no unit was stated, use cm. Never use assistant text or "
                "images as evidence."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetBodyProfileArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_EXERCISE_PROFILE_TOOL_NAME,
            description=(
                "Save the user's explicitly stated usual exercise habit or baseline. "
                "Use this for statements such as currently not exercising; do not turn that "
                "into a medical exercise constraint. For historical user evidence, pass its "
                "exact evidence_excerpt and evidence_ref from working memory."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetExerciseProfileArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=UPSERT_FOOD_PREFERENCE_TOOL_NAME,
            description=(
                "Save one food preference explicitly stated by the user. Use avoid "
                "only when the user explicitly says they avoid the item; do not turn a meal "
                "record into a preference. Historical user evidence requires its exact "
                "evidence_excerpt and evidence_ref from working memory."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=UpsertFoodPreferenceArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=UPSERT_EXERCISE_PREFERENCE_TOOL_NAME,
            description=(
                "Save one exercise preference explicitly stated by the user. Do not infer a "
                "durable preference from one activity record. Historical user evidence "
                "requires its exact evidence_excerpt and evidence_ref from working memory."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=UpsertExercisePreferenceArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_WEIGHT_GOAL_TOOL_NAME,
            description=(
                "Save a target weight explicitly stated by the user. This stores a "
                "self-reported goal, not a measurement or medical endorsement. If the user "
                "omits a unit, use kg; preserve an explicit unit. Historical user evidence "
                "requires its exact evidence_excerpt and evidence_ref from working memory."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetWeightGoalArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_BODY_FAT_GOAL_TOOL_NAME,
            description=(
                "Save a body-fat percentage goal explicitly stated by the user. The model "
                "decides which number is the target. Historical user evidence requires its "
                "exact evidence_excerpt and evidence_ref from working memory. This does not "
                "endorse the goal as medically appropriate."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetBodyFatGoalArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_BEHAVIOR_GOAL_TOOL_NAME,
            description=(
                "Save one explicit behavior goal for exercise sessions, daily steps, or meal "
                "check-ins. Historical user evidence requires its exact evidence_excerpt and "
                "evidence_ref from working memory."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetBehaviorGoalArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=RECORD_USER_CONSTRAINT_TOOL_NAME,
            description=(
                "Save a dietary, exercise, or health constraint explicitly reported by the "
                "user. statement must be copied exactly from user-authored evidence. For "
                "historical evidence pass evidence_ref from working memory. Store it as "
                "user-reported context, never as a diagnosis."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=RecordUserConstraintArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=LIST_USER_MEMORIES_TOOL_NAME,
            description=(
                "List the current user's active profile memories. Use when the user asks what "
                "is remembered or before forgetting a memory whose exact ID is unknown."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=ListUserMemoriesArguments,
            effect_level=ToolEffectLevel.READ,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=FORGET_USER_MEMORY_TOOL_NAME,
            description=(
                "Revoke one exact active profile memory owned by the current user. First list "
                "memories when the intended memory ID is not already present in context or a "
                "tool result. Never guess an ID."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=ForgetUserMemoryArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=SET_CONVERSATION_HANDOFF_TOOL_NAME,
            description=(
                "Save one unfinished conversation handoff only when the current user "
                "explicitly asks to continue it in a later turn. objective and unresolved "
                "are concise summaries, while evidence_excerpt must be copied exactly from "
                "the current user message. Do not store domain facts here."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=SetConversationHandoffArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=RESOLVE_CONVERSATION_HANDOFF_TOOL_NAME,
            description=(
                "Resolve the exact active conversation handoff after its work is completed "
                "or when the current user cancels it. Use only the handoff_id supplied in "
                "trusted working_memory context; never guess an ID."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=ResolveConversationHandoffArguments,
            effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
            idempotent=True,
            requires_confirmation=False,
            timeout_seconds=3,
        ),
        RegisteredTool(
            name=CLEAR_USER_MEMORIES_TOOL_NAME,
            description=(
                "Clear all active profile, goal, and constraint memories for the current "
                "user after explicit user confirmation. This does not delete weight, meal, "
                "exercise, transcript, or message-idempotency records. scope must be "
                "profile_goal_constraint and evidence_excerpt must be copied exactly from "
                "the user's original clear request."
            ),
            version=MEMORY_TOOL_VERSION,
            arguments_model=ClearUserMemoriesArguments,
            effect_level=ToolEffectLevel.SENSITIVE_WRITE,
            idempotent=True,
            requires_confirmation=True,
            timeout_seconds=5,
        ),
    )


def memory_tool_executors(
    repository: MemoryRepository,
    handoffs: HandoffRepository,
) -> Mapping[str, ToolExecutor[Any]]:
    handlers = MemoryToolHandlers(repository, handoffs)
    return {
        SET_COACHING_PROFILE_TOOL_NAME: ToolExecutor(
            arguments_model=SetCoachingProfileArguments,
            handler=handlers.set_coaching_profile,
        ),
        SET_BODY_PROFILE_TOOL_NAME: ToolExecutor(
            arguments_model=SetBodyProfileArguments,
            handler=handlers.set_body_profile,
        ),
        SET_EXERCISE_PROFILE_TOOL_NAME: ToolExecutor(
            arguments_model=SetExerciseProfileArguments,
            handler=handlers.set_exercise_profile,
        ),
        UPSERT_FOOD_PREFERENCE_TOOL_NAME: ToolExecutor(
            arguments_model=UpsertFoodPreferenceArguments,
            handler=handlers.upsert_food_preference,
        ),
        UPSERT_EXERCISE_PREFERENCE_TOOL_NAME: ToolExecutor(
            arguments_model=UpsertExercisePreferenceArguments,
            handler=handlers.upsert_exercise_preference,
        ),
        SET_WEIGHT_GOAL_TOOL_NAME: ToolExecutor(
            arguments_model=SetWeightGoalArguments,
            handler=handlers.set_weight_goal,
        ),
        SET_BODY_FAT_GOAL_TOOL_NAME: ToolExecutor(
            arguments_model=SetBodyFatGoalArguments,
            handler=handlers.set_body_fat_goal,
        ),
        SET_BEHAVIOR_GOAL_TOOL_NAME: ToolExecutor(
            arguments_model=SetBehaviorGoalArguments,
            handler=handlers.set_behavior_goal,
        ),
        RECORD_USER_CONSTRAINT_TOOL_NAME: ToolExecutor(
            arguments_model=RecordUserConstraintArguments,
            handler=handlers.record_user_constraint,
        ),
        LIST_USER_MEMORIES_TOOL_NAME: ToolExecutor(
            arguments_model=ListUserMemoriesArguments,
            handler=handlers.list_user_memories,
        ),
        FORGET_USER_MEMORY_TOOL_NAME: ToolExecutor(
            arguments_model=ForgetUserMemoryArguments,
            handler=handlers.forget_user_memory,
        ),
        SET_CONVERSATION_HANDOFF_TOOL_NAME: ToolExecutor(
            arguments_model=SetConversationHandoffArguments,
            handler=handlers.set_conversation_handoff,
        ),
        RESOLVE_CONVERSATION_HANDOFF_TOOL_NAME: ToolExecutor(
            arguments_model=ResolveConversationHandoffArguments,
            handler=handlers.resolve_conversation_handoff,
        ),
        CLEAR_USER_MEMORIES_TOOL_NAME: ToolExecutor(
            arguments_model=ClearUserMemoriesArguments,
            handler=handlers.clear_user_memories,
        ),
    }
