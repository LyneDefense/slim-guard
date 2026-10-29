from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import ToolMutationKind, ToolResultStatus

SAFETY_POLICY_VERSION = "receipt-bound-output-guard-v4"


@dataclass(frozen=True, slots=True)
class OutputGuardResult:
    text: str
    modified: bool
    code: str


class OutputGuard(Protocol):
    def review(
        self,
        *,
        text: str,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult: ...


class SlimGuardOutputGuard:
    """Deterministic medical/unknown-effect checks, not business-text classification.

    A ResponseProcessor using this guard must also install a semantic claim reviewer.
    """

    requires_claim_review = True

    _DIAGNOSIS = re.compile(r"你(?:患有|得了|就是).{0,16}(?:病|症)")
    _PRESCRIPTION = re.compile(r"(?:每天|每日).{0,20}\d+(?:\.\d+)?\s*(?:mg|毫克)", re.I)
    _DANGEROUS_ADVICE = (
        "建议催吐",
        "可以催吐",
        "服用泻药减肥",
        "通过绝食减肥",
        "停止进食来减肥",
    )
    _RAW_TOOL_CALL_MARKERS = ("<tool_call>", "</tool_call>", "tool_calls=")
    _MUTATION_KINDS = {
        ToolMutationKind.HEALTH_RECORD,
        ToolMutationKind.USER_PROFILE,
        ToolMutationKind.USER_MEMORY,
        ToolMutationKind.CONVERSATION_TASK,
        ToolMutationKind.ROUTINE_SETTINGS,
    }

    def review(
        self,
        *,
        text: str,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult:
        normalized = text.strip()
        if self._contains_prohibited_medical_advice(normalized):
            return self._replacement(
                "我不能提供疾病诊断、处方或高风险减重指导。可以继续帮你客观记录体重、饮食"
                "和运动；涉及症状或用药请咨询医生。",
                "prohibited_medical_advice",
            )
        if any(marker in normalized for marker in self._RAW_TOOL_CALL_MARKERS):
            return self._replacement(
                "这次操作还没有完成，请根据提示补充信息后再试。",
                "raw_tool_call_leak",
            )
        unknown_review = self._review_unknown_mutations(tool_outcomes)
        if unknown_review is not None:
            return unknown_review
        return OutputGuardResult(text=normalized, modified=False, code="passed")

    def _contains_prohibited_medical_advice(self, text: str) -> bool:
        return bool(self._DIAGNOSIS.search(text) or self._PRESCRIPTION.search(text)) or any(
            phrase in text for phrase in self._DANGEROUS_ADVICE
        )

    @classmethod
    def _review_unknown_mutations(
        cls, outcomes: tuple[ToolCallOutcome, ...]
    ) -> OutputGuardResult | None:
        for outcome in outcomes:
            mutation_kind = outcome.execution.mutation_kind
            if (
                mutation_kind not in cls._MUTATION_KINDS
                or outcome.execution.result.status is not ToolResultStatus.FAILED
                or not cls._effect_is_unknown(outcome)
                or cls._operation_status_later(outcome, outcomes) in {"succeeded", "not_applied"}
            ):
                continue
            label = {
                ToolMutationKind.HEALTH_RECORD: "记录",
                ToolMutationKind.CONVERSATION_TASK: "待办",
                ToolMutationKind.USER_PROFILE: "资料",
                ToolMutationKind.USER_MEMORY: "记忆",
                ToolMutationKind.ROUTINE_SETTINGS: "提醒设置",
            }[mutation_kind]
            code = {
                ToolMutationKind.HEALTH_RECORD: "unknown_record_write_claim",
                ToolMutationKind.CONVERSATION_TASK: "unknown_task_write_claim",
                ToolMutationKind.USER_PROFILE: "unknown_profile_write_claim",
                ToolMutationKind.USER_MEMORY: "unknown_memory_write_claim",
                ToolMutationKind.ROUTINE_SETTINGS: "unknown_routine_write_claim",
            }[mutation_kind]
            another_succeeded = any(
                other.execution.mutation_kind is mutation_kind
                and other.execution.result.status is ToolResultStatus.SUCCEEDED
                for other in outcomes
            )
            message = (
                f"部分{label}操作已成功；另有操作结果暂时无法确认，"
                "请先不要重复提交，原操作结果仍需核实。"
                if another_succeeded
                else f"这次{label}操作结果暂时无法确认，请先不要重复提交；原操作结果仍需核实。"
            )
            return cls._replacement(message, code)
        return None

    @staticmethod
    def _effect_is_unknown(outcome: ToolCallOutcome) -> bool:
        failure = outcome.execution.result.failure
        return failure is None or failure.effect_status.value == "unknown"

    @staticmethod
    def _operation_status_later(
        outcome: ToolCallOutcome,
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> str | None:
        result = outcome.execution.result
        operation_id = result.operation.operation_id if result.operation is not None else None
        if operation_id is None:
            return None
        resolved_status = None
        for later in outcomes:
            if later.execution.tool_call_id == outcome.execution.tool_call_id:
                continue
            later_result = later.execution.result
            if (
                later_result.operation is not None
                and later_result.operation.operation_id == operation_id
            ):
                resolved_status = later_result.operation.status
            operation_view = later_result.output.get("operation")
            if (
                isinstance(operation_view, dict)
                and operation_view.get("operation_id") == operation_id
            ):
                resolved_status = operation_view.get("status")
        return resolved_status

    @staticmethod
    def _replacement(text: str, code: str) -> OutputGuardResult:
        return OutputGuardResult(text=text, modified=True, code=code)


class PermissiveOutputGuard:
    def review(
        self,
        *,
        text: str,
        tool_outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult:
        return OutputGuardResult(text=text.strip(), modified=False, code="passed")
