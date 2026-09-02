from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from slim_guard.harness.events import ItemType
from slim_guard.harness.state_repository import ItemRef
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import ToolResultStatus


class HealthRiskLevel(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    EMERGENCY = "emergency"


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    level: HealthRiskLevel
    code: str
    blocks_tools: bool

    def to_context(self) -> dict[str, str | bool]:
        return {
            "level": self.level.value,
            "code": self.code,
            "blocks_tools": self.blocks_tools,
        }


@dataclass(frozen=True, slots=True)
class OutputGuardResult:
    text: str
    modified: bool
    code: str


class InputSafetyPolicy(Protocol):
    def assess(self, items: tuple[ItemRef, ...]) -> SafetyAssessment: ...


class OutputGuard(Protocol):
    def review(
        self,
        *,
        text: str,
        assessment: SafetyAssessment,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult: ...


class DefaultInputSafetyPolicy:
    """Hard gate for a small set of explicit, high-consequence health signals."""

    _SELF_HARM = ("自杀", "轻生", "不想活", "伤害自己", "结束生命")
    _MEDICAL_EMERGENCY = (
        "胸痛",
        "呼吸困难",
        "喘不上气",
        "晕倒",
        "昏厥",
        "意识不清",
        "吐血",
        "便血",
    )
    _DANGEROUS_WEIGHT_LOSS = (
        "催吐",
        "泻药减肥",
        "绝食减肥",
        "完全不吃饭",
        "一天不吃东西",
    )
    _AGE_PATTERN = re.compile(r"(?:我|本人)?\s*(?P<age>\d{1,2})\s*岁")

    def assess(self, items: tuple[ItemRef, ...]) -> SafetyAssessment:
        text = "\n".join(
            str(item.payload.get("text", ""))
            for item in items
            if item.item_type is ItemType.USER_MESSAGE
        )
        if any(signal in text for signal in self._SELF_HARM):
            return SafetyAssessment(HealthRiskLevel.EMERGENCY, "self_harm", True)
        if any(signal in text for signal in self._MEDICAL_EMERGENCY):
            return SafetyAssessment(
                HealthRiskLevel.EMERGENCY,
                "medical_emergency",
                True,
            )
        match = self._AGE_PATTERN.search(text)
        if (match is not None and int(match.group("age")) < 18) or "未成年" in text:
            return SafetyAssessment(HealthRiskLevel.HIGH, "minor", True)
        if any(signal in text for signal in self._DANGEROUS_WEIGHT_LOSS):
            return SafetyAssessment(
                HealthRiskLevel.HIGH,
                "dangerous_weight_loss",
                True,
            )
        return SafetyAssessment(HealthRiskLevel.NORMAL, "none", False)


class SlimGuardOutputGuard:
    """Enforces non-negotiable safety and failed-write truthfulness on final text."""

    _DIAGNOSIS = re.compile(r"你(?:患有|得了|就是).{0,16}(?:病|症)")
    _PRESCRIPTION = re.compile(r"(?:每天|每日).{0,20}\d+(?:\.\d+)?\s*(?:mg|毫克)", re.I)
    _DANGEROUS_ADVICE = (
        "建议催吐",
        "可以催吐",
        "服用泻药减肥",
        "通过绝食减肥",
        "停止进食来减肥",
    )
    _SUCCESS_CLAIMS = ("已记录", "记录成功", "已保存", "保存成功")
    _MEMORY_SUCCESS_CLAIMS = (
        "记住了",
        "已记住",
        "已记下",
        "已忘记",
        "不再记得",
        "已清空",
        "清空成功",
    )
    _RECORD_WRITE_TOOLS = {
        "record_weight",
        "record_body_fat",
        "record_meal",
        "record_exercise",
        "configure_checkin_schedule",
        "update_record_status",
    }

    def review(
        self,
        *,
        text: str,
        assessment: SafetyAssessment,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult:
        normalized = text.strip()
        if assessment.code == "self_harm":
            return self._replacement(
                "你现在的安全最重要。请立刻联系身边可信任的人陪着你，并联系当地急救服务或"
                "专业危机干预支持；如果有马上伤害自己的可能，请立即拨打急救电话。",
                "self_harm_escalation",
            )
        if assessment.code == "medical_emergency":
            return self._replacement(
                "你描述的情况可能需要立即处理。请停止当前减脂或运动安排，尽快联系当地急救"
                "服务或前往急诊，不要等待线上减脂建议。",
                "medical_emergency_escalation",
            )
        if assessment.code == "minor":
            return self._replacement(
                "未成年人不适合自行执行成人减脂方案。请和监护人一起咨询儿科或专业营养人员，"
                "优先保证正常生长发育。",
                "minor_safety_boundary",
            )
        if assessment.code == "dangerous_weight_loss":
            return self._replacement(
                "我不能帮助制定催吐、滥用泻药、绝食等高风险减重方案。请先停止这类做法；"
                "如果已经出现不适或无法停止，请尽快联系医生。",
                "dangerous_weight_loss_boundary",
            )
        if self._contains_prohibited_medical_advice(normalized):
            return self._replacement(
                "我不能提供疾病诊断、处方或高风险减重指导。可以继续帮你客观记录体重、饮食"
                "和运动；涉及症状或用药请咨询医生。",
                "prohibited_medical_advice",
            )
        if self._failed_record_write_claimed_success(normalized, tool_outcomes):
            return self._replacement(
                "这次记录没有确认成功，请把刚才的数据再发一次，我会重新尝试保存。",
                "failed_write_success_claim",
            )
        if self._failed_memory_write_claimed_success(normalized, tool_outcomes):
            return self._replacement(
                "这项记忆没有确认保存或撤销成功，请把你的要求再说一次，我会重新处理。",
                "failed_memory_write_success_claim",
            )
        return OutputGuardResult(text=normalized, modified=False, code="passed")

    def _contains_prohibited_medical_advice(self, text: str) -> bool:
        return bool(self._DIAGNOSIS.search(text) or self._PRESCRIPTION.search(text)) or any(
            phrase in text for phrase in self._DANGEROUS_ADVICE
        )

    @classmethod
    def _failed_record_write_claimed_success(
        cls,
        text: str,
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> bool:
        record_writes = tuple(
            outcome
            for outcome in outcomes
            if outcome.execution.tool_name in cls._RECORD_WRITE_TOOLS
        )
        all_record_writes_failed = bool(record_writes) and all(
            outcome.execution.result.status is ToolResultStatus.FAILED
            for outcome in record_writes
        )
        return all_record_writes_failed and any(
            claim in text for claim in cls._SUCCESS_CLAIMS
        )

    @classmethod
    def _failed_memory_write_claimed_success(
        cls,
        text: str,
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> bool:
        memory_writes = tuple(
            outcome
            for outcome in outcomes
            if outcome.execution.tool_name
            in {
                "set_coaching_profile",
                "set_body_profile",
                "set_exercise_profile",
                "upsert_food_preference",
                "upsert_exercise_preference",
                "set_weight_goal",
                "set_body_fat_goal",
                "set_behavior_goal",
                "record_user_constraint",
                "forget_user_memory",
                "set_conversation_handoff",
                "resolve_conversation_handoff",
                "clear_user_memories",
                "resolve_pending_user_action",
            }
        )
        all_memory_writes_failed = bool(memory_writes) and all(
            outcome.execution.result.status is ToolResultStatus.FAILED
            for outcome in memory_writes
        )
        return all_memory_writes_failed and any(
            claim in text for claim in cls._MEMORY_SUCCESS_CLAIMS
        )

    @staticmethod
    def _replacement(text: str, code: str) -> OutputGuardResult:
        return OutputGuardResult(text=text, modified=True, code=code)


class PermissiveOutputGuard:
    def review(
        self,
        *,
        text: str,
        assessment: SafetyAssessment,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult:
        return OutputGuardResult(text=text.strip(), modified=False, code="passed")
