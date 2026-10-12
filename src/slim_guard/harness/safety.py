from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import ToolMutationKind, ToolResultStatus

SAFETY_POLICY_VERSION = "health-output-guard-v3"


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
    """Review user-visible output against medical and mutation-result constraints."""

    _DIAGNOSIS = re.compile(r"你(?:患有|得了|就是).{0,16}(?:病|症)")
    _PRESCRIPTION = re.compile(r"(?:每天|每日).{0,20}\d+(?:\.\d+)?\s*(?:mg|毫克)", re.I)
    _DANGEROUS_ADVICE = (
        "建议催吐",
        "可以催吐",
        "服用泻药减肥",
        "通过绝食减肥",
        "停止进食来减肥",
    )
    _MEMORY_SUCCESS_CLAIMS = (
        "已记下",
        "已忘记",
        "不再记得",
    )
    _RAW_TOOL_CALL_MARKERS = ("<tool_call>", "</tool_call>", "tool_calls=")
    _RECORD_CLAIMS = (
        "已记录",
        "记录成功",
        "记录好了",
        "已录入记录",
        "已写入",
        "写入成功",
        "已入库",
        "入库成功",
        "更正成功",
        "记上了",
        "保存成功",
        "都记好了",
        "全部记录",
        "全部保存",
    )
    _MUTATION_CLAIMS = {
        ToolMutationKind.HEALTH_RECORD: _RECORD_CLAIMS,
        ToolMutationKind.USER_PROFILE: (
            "资料已更新",
            "档案已更新",
            "偏好已保存",
            "已记住",
            "记住了",
            "记好了",
        ),
        ToolMutationKind.USER_MEMORY: _MEMORY_SUCCESS_CLAIMS,
        ToolMutationKind.CONVERSATION_TASK: ("待办已保存", "任务已保存"),
        ToolMutationKind.ROUTINE_SETTINGS: ("提醒已设置", "提醒时间已更新"),
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
        mutation_review = self._review_mutation_claims(normalized, tool_outcomes)
        if mutation_review is not None:
            return mutation_review
        return OutputGuardResult(text=normalized, modified=False, code="passed")

    def _contains_prohibited_medical_advice(self, text: str) -> bool:
        return bool(self._DIAGNOSIS.search(text) or self._PRESCRIPTION.search(text)) or any(
            phrase in text for phrase in self._DANGEROUS_ADVICE
        )

    @classmethod
    def _review_mutation_claims(
        cls,
        text: str,
        outcomes: tuple[ToolCallOutcome, ...],
    ) -> OutputGuardResult | None:
        for mutation_kind, claims in cls._MUTATION_CLAIMS.items():
            if not cls._contains_success_claim(text, claims):
                continue
            attempts = tuple(
                outcome
                for outcome in outcomes
                if outcome.execution.mutation_kind is mutation_kind
            )
            successful = tuple(
                outcome
                for outcome in attempts
                if outcome.execution.result.status is ToolResultStatus.SUCCEEDED
            )
            failed = tuple(
                outcome
                for outcome in attempts
                if outcome.execution.result.status is ToolResultStatus.FAILED
            )
            resolved_failures = tuple(
                outcome
                for outcome in failed
                if cls._operation_status_later(outcome, outcomes) == "succeeded"
            )
            effective_successful = (*successful, *resolved_failures)
            if not attempts:
                label = {
                    ToolMutationKind.HEALTH_RECORD: "记录写入",
                    ToolMutationKind.CONVERSATION_TASK: "待办更改",
                    ToolMutationKind.USER_PROFILE: "资料更改",
                    ToolMutationKind.USER_MEMORY: "记忆更改",
                    ToolMutationKind.ROUTINE_SETTINGS: "提醒设置",
                }[mutation_kind]
                code_prefix = {
                    ToolMutationKind.HEALTH_RECORD: "record_write",
                    ToolMutationKind.CONVERSATION_TASK: "task_write",
                    ToolMutationKind.USER_PROFILE: "profile_write",
                    ToolMutationKind.USER_MEMORY: "memory_write",
                    ToolMutationKind.ROUTINE_SETTINGS: "routine_write",
                }[mutation_kind]
                return cls._replacement(
                    f"我没有找到本轮{label}成功的回执，因此不能确认这项更改已经完成。",
                    f"unverified_{code_prefix}_claim",
                )

            unresolved = tuple(
                outcome
                for outcome in failed
                if cls._effect_is_unknown(outcome)
                and cls._operation_status_later(outcome, outcomes)
                not in {"succeeded", "not_applied"}
            )
            if unresolved:
                if mutation_kind is ToolMutationKind.HEALTH_RECORD:
                    label = "记录"
                    code = "unknown_record_write_claim"
                elif mutation_kind is ToolMutationKind.CONVERSATION_TASK:
                    label = "待办"
                    code = "unknown_task_write_claim"
                elif mutation_kind is ToolMutationKind.USER_PROFILE:
                    label = "资料"
                    code = "unknown_profile_write_claim"
                elif mutation_kind is ToolMutationKind.USER_MEMORY:
                    label = "记忆"
                    code = "unknown_memory_write_claim"
                else:
                    label = "提醒设置"
                    code = "unknown_routine_write_claim"
                message = (
                    f"部分{label}操作已成功；另有操作结果暂时无法确认，"
                    "请先不要重复提交，原操作结果仍需核实。"
                    if effective_successful
                    else f"这次{label}操作结果暂时无法确认，请先不要重复提交；原操作结果仍需核实。"
                )
                return cls._replacement(message, code)

            unapplied = tuple(outcome for outcome in failed if outcome not in resolved_failures)
            if unapplied and effective_successful:
                if mutation_kind is ToolMutationKind.HEALTH_RECORD:
                    return cls._replacement(
                        "这次只有部分记录操作完成，其他操作没有确认成功。",
                        "partial_record_write_claim",
                    )
                return cls._replacement(
                    "这次只有部分更改完成，其他操作没有确认成功。",
                    "partial_mutation_claim",
                )

            if unapplied and not effective_successful:
                if mutation_kind is ToolMutationKind.HEALTH_RECORD:
                    return cls._replacement(
                        "这次记录没有保存成功。",
                        "failed_record_write_claim",
                    )
                return cls._replacement(
                    "这项更改没有确认成功。",
                    "failed_mutation_claim",
                )

        return None

    @classmethod
    def _review_unknown_mutations(
        cls, outcomes: tuple[ToolCallOutcome, ...]
    ) -> OutputGuardResult | None:
        for outcome in outcomes:
            mutation_kind = outcome.execution.mutation_kind
            if (
                mutation_kind not in cls._MUTATION_CLAIMS
                or outcome.execution.result.status is not ToolResultStatus.FAILED
                or not cls._effect_is_unknown(outcome)
                or cls._operation_status_later(outcome, outcomes)
                in {"succeeded", "not_applied"}
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
    def _contains_success_claim(text: str, claims: tuple[str, ...]) -> bool:
        negation = re.compile(r"(?:没有|没能|没|未能|未|不能|不曾|无法|不)\s*$")
        for claim in claims:
            start = 0
            while (index := text.find(claim, start)) >= 0:
                prefix = text[max(0, index - 10) : index]
                if not negation.search(prefix):
                    return True
                start = index + len(claim)
        return False

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
