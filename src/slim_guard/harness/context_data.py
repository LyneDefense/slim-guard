from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select

from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.exercise.repository import ExerciseRepository
from slim_guard.domain.meal.repository import MealRepository
from slim_guard.domain.routine.repository import RoutinePreferenceRepository
from slim_guard.domain.routine.status import DailyCheckinStatusRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import ItemType, TurnTrigger
from slim_guard.harness.pending_actions import PendingActionRepository
from slim_guard.harness.state_repository import ItemRef
from slim_guard.memory.contracts import MemoryFactRef, MemoryKey
from slim_guard.memory.handoff import HandoffRepository
from slim_guard.memory.repository import MemoryRepository
from slim_guard.memory.working import ConversationWindowRepository


class ContextDataProvider(Protocol):
    """Loads bounded, trusted facts for one user before a model call."""

    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
        trigger: TurnTrigger | None = None,
        input_items: tuple[ItemRef, ...] = (),
    ) -> Mapping[str, Any]: ...


class EmptyContextDataProvider:
    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
        trigger: TurnTrigger | None = None,
        input_items: tuple[ItemRef, ...] = (),
    ) -> Mapping[str, Any]:
        return {}


class AuthoritativeContextDataProvider:
    """Builds compact cross-Turn context from authoritative domain records."""

    def __init__(
        self,
        *,
        database: Database,
        weights: WeightRepository,
        meals: MealRepository,
        exercise: ExerciseRepository,
        routines: RoutinePreferenceRepository | None = None,
        checkins: DailyCheckinStatusRepository | None = None,
        memories: MemoryRepository | None = None,
        conversation: ConversationWindowRepository | None = None,
        handoffs: HandoffRepository | None = None,
        pending_actions: PendingActionRepository | None = None,
        weight_limit: int = 7,
        meal_limit: int = 10,
        exercise_limit: int = 10,
        memory_limit: int = 30,
        dialogue_turn_limit: int = 3,
        dialogue_char_limit: int = 1500,
        recent_image_limit: int = 3,
    ) -> None:
        self._database = database
        self._weights = weights
        self._meals = meals
        self._exercise = exercise
        self._routines = routines
        self._checkins = checkins
        self._memories = memories
        self._conversation = conversation
        self._handoffs = handoffs
        self._pending_actions = pending_actions
        self._weight_limit = weight_limit
        self._meal_limit = meal_limit
        self._exercise_limit = exercise_limit
        self._memory_limit = memory_limit
        self._dialogue_turn_limit = dialogue_turn_limit
        self._dialogue_char_limit = dialogue_char_limit
        self._recent_image_limit = recent_image_limit

    async def load(
        self,
        *,
        user_id: str,
        current_time: datetime,
        trigger: TurnTrigger | None = None,
        input_items: tuple[ItemRef, ...] = (),
    ) -> Mapping[str, Any]:
        if current_time.utcoffset() is None:
            raise ValueError("Context data time must be timezone-aware")
        profile, weight_trend, meals, exercise = await asyncio.gather(
            self._profile(user_id),
            self._weights.recent_trend(user_id, limit=self._weight_limit),
            self._meals.recent(user_id, limit=self._meal_limit),
            self._exercise.recent(user_id, limit=self._exercise_limit),
        )
        context: dict[str, Any] = {
            "recent_weights": [
                {
                    "weight_kg": self._decimal_text(record.weight_kg),
                    "measured_at": record.measured_at.isoformat(),
                    "condition": record.condition.value,
                }
                for record in weight_trend.records
            ],
            "recent_meals": [
                {
                    "meal_type": record.meal_type.value,
                    "foods": [
                        {
                            "name": food.name,
                            **({"portion": food.portion} if food.portion else {}),
                        }
                        for food in record.foods
                    ],
                    "occurred_at": record.occurred_at.isoformat(),
                    **({"note": record.note} if record.note else {}),
                }
                for record in meals
            ],
            "recent_exercise": [
                {
                    "activity_name": record.activity_name,
                    "occurred_at": record.occurred_at.isoformat(),
                    **(
                        {"duration_minutes": record.duration_minutes}
                        if record.duration_minutes is not None
                        else {}
                    ),
                    **({"steps": record.steps} if record.steps is not None else {}),
                    **(
                        {"distance_meters": record.distance_meters}
                        if record.distance_meters is not None
                        else {}
                    ),
                    **(
                        {"reported_energy_kcal": record.reported_energy_kcal}
                        if record.reported_energy_kcal is not None
                        else {}
                    ),
                    **({"note": record.note} if record.note else {}),
                }
                for record in exercise
            ],
        }
        if profile is not None:
            context["profile"] = profile
        if self._memories is not None:
            memories = await self._memories.active(
                user_id,
                limit=self._memory_limit,
            )
            memories = self._relevant_memories(
                memories,
                trigger=trigger,
                input_items=input_items,
            )
            if memories:
                context["profile_memory"] = [
                    {
                        "memory_id": memory.id,
                        "kind": memory.kind.value,
                        "key": memory.key.value,
                        "value": memory.value,
                        "assertion": memory.assertion.value,
                        "sensitivity": memory.sensitivity.value,
                        "stale": (
                            memory.review_after is not None
                            and memory.review_after <= current_time
                        ),
                    }
                    for memory in memories
                ]
        working_memory: dict[str, Any] = {}
        if self._conversation is not None:
            dialogue, recent_images = await asyncio.gather(
                self._conversation.recent(
                    user_id,
                    turn_limit=self._dialogue_turn_limit,
                    char_limit=self._dialogue_char_limit,
                ),
                self._conversation.recent_images(
                    user_id,
                    at=current_time,
                    limit=self._recent_image_limit,
                ),
            )
            if dialogue:
                working_memory["recent_dialogue"] = [
                    {
                        "messages": [
                            {"role": message.role, "content": message.content}
                            for message in turn.messages
                        ],
                    }
                    for turn in dialogue
                ]
            if recent_images:
                working_memory["recent_images"] = [
                    {
                        "asset_id": image.asset_id,
                        "mime_type": image.mime_type,
                        "created_at": image.created_at.isoformat(),
                        "expires_at": image.expires_at.isoformat(),
                        **(
                            {"observation": image.observation}
                            if image.observation is not None
                            else {}
                        ),
                        **(
                            {
                                "requires_user_confirmation": (
                                    image.requires_user_confirmation
                                )
                            }
                            if image.requires_user_confirmation is not None
                            else {}
                        ),
                    }
                    for image in recent_images
                ]
        if self._handoffs is not None:
            handoff = await self._handoffs.active(user_id, at=current_time)
            if handoff is not None:
                working_memory["active_handoff"] = {
                    "handoff_id": handoff.id,
                    "objective": handoff.objective,
                    "unresolved": list(handoff.unresolved),
                    "created_at": handoff.created_at.isoformat(),
                    "expires_at": handoff.expires_at.isoformat(),
                }
        if self._pending_actions is not None:
            pending = await self._pending_actions.list_open_for_user(
                user_id=user_id,
                at=current_time,
            )
            if pending:
                working_memory["pending_user_confirmations"] = [
                    {
                        "action_id": action.id,
                        "tool_name": action.tool_name,
                        "reason": action.reason,
                        "expires_at": action.expires_at.isoformat(),
                    }
                    for action in pending
                ]
        if working_memory:
            context["working_memory"] = working_memory
        if self._routines is not None:
            routine = await self._routines.get(user_id)
            if routine is not None:
                context["checkin_schedule"] = {
                    "timezone": routine.timezone,
                    "weight_reminder_time": routine.weight_reminder_time,
                    "meal_reminder_time": routine.meal_reminder_time,
                    "daily_review_time": routine.daily_review_time,
                }
                if self._checkins is not None:
                    local_date = current_time.astimezone(
                        ZoneInfo(routine.timezone)
                    ).date()
                    status = await self._checkins.get(
                        user_id=user_id,
                        local_date=local_date,
                        timezone=routine.timezone,
                    )
                    context["today_checkin_status"] = {
                        "local_date": local_date.isoformat(),
                        "timezone": routine.timezone,
                        "weight_count": status.weight_count,
                        "meal_count": status.meal_count,
                        "exercise_count": status.exercise_count,
                    }
        return context

    @staticmethod
    def _relevant_memories(
        memories: tuple[MemoryFactRef, ...],
        *,
        trigger: TurnTrigger | None,
        input_items: tuple[ItemRef, ...],
    ) -> tuple[MemoryFactRef, ...]:
        if trigger is None or trigger is TurnTrigger.DAILY_REVIEW:
            return memories
        selected = {
            MemoryKey.PREFERRED_NAME,
            MemoryKey.RESPONSE_STYLE,
        }
        if trigger is TurnTrigger.WEIGHT_REMINDER:
            selected.update({MemoryKey.TARGET_WEIGHT, MemoryKey.HEALTH_CONTEXT})
        elif trigger is TurnTrigger.MEAL_REMINDER:
            selected.update(
                {
                    MemoryKey.FOOD_PREFERENCE,
                    MemoryKey.DIETARY_CONSTRAINT,
                    MemoryKey.HEALTH_CONTEXT,
                }
            )
        text = "\n".join(
            str(item.payload.get("text", ""))
            for item in input_items
            if item.item_type is ItemType.USER_MESSAGE
        )
        if any(word in text for word in ("记得什么", "记住什么", "哪些记忆")):
            return memories
        if any(word in text.lower() for word in ("体重", "称重", "目标", "kg", "公斤", "斤", "磅")):
            selected.update({MemoryKey.TARGET_WEIGHT, MemoryKey.HEALTH_CONTEXT})
        if any(word in text for word in ("吃", "饮食", "餐", "饭", "食物", "过敏", "忌口")):
            selected.update(
                {
                    MemoryKey.FOOD_PREFERENCE,
                    MemoryKey.DIETARY_CONSTRAINT,
                    MemoryKey.HEALTH_CONTEXT,
                }
            )
        if any(word in text for word in ("运动", "锻炼", "步数", "跑步", "游泳", "健身", "膝")):
            selected.update(
                {
                    MemoryKey.EXERCISE_PREFERENCE,
                    MemoryKey.EXERCISE_CONSTRAINT,
                    MemoryKey.BEHAVIOR_GOAL,
                    MemoryKey.HEALTH_CONTEXT,
                }
            )
        if any(word in text for word in ("打卡", "习惯", "计划")):
            selected.add(MemoryKey.BEHAVIOR_GOAL)
        return tuple(memory for memory in memories if memory.key in selected)

    async def _profile(self, user_id: str) -> dict[str, Any] | None:
        async with self._database.session() as session:
            row = await session.scalar(
                select(SlimGuardUser).where(SlimGuardUser.id == user_id)
            )
            if row is None:
                return None
            return {
                **({"nickname": row.nickname} if row.nickname else {}),
                "first_seen_at": self._as_aware(row.first_seen_at).isoformat(),
            }

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        return format(value.normalize(), "f")

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        return value if value.utcoffset() is not None else value.replace(tzinfo=UTC)
