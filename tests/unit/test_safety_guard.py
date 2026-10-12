from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from slim_guard.features.health_records.tools import health_record_tool_definitions
from slim_guard.harness.safety import (
    SlimGuardOutputGuard,
)
from slim_guard.harness.state_repository import TurnRef
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import (
    ToolEffectLevel,
    ToolEffectStatus,
    ToolExecution,
    ToolMutationKind,
    ToolOperationReceipt,
    ToolPolicyDecision,
    ToolResult,
)


def outcome(
    *,
    status: str,
    tool_name: str = "renamed_health_record_writer",
    effect_status: ToolEffectStatus | None = None,
    operation_id: str | None = None,
    output: dict[str, object] | None = None,
) -> ToolCallOutcome:
    if status == "succeeded":
        result = ToolResult.success(output=output or {})
    else:
        result = ToolResult.failed(
            code="write_failed",
            message="write failed",
            effect_status=effect_status or ToolEffectStatus.UNKNOWN,
        )
        if operation_id is not None:
            result = result.model_copy(
                update={
                    "operation": ToolOperationReceipt(
                        operation_id=operation_id,
                        tool_name=tool_name,
                        status=(
                            "unknown"
                            if effect_status is ToolEffectStatus.UNKNOWN
                            else "not_applied"
                        ),
                    )
                }
            )
    execution = ToolExecution(
        tool_call_id=f"call-{tool_name}-{status}-{operation_id or 'plain'}",
        tool_name=tool_name,
        mutation_kind=ToolMutationKind.HEALTH_RECORD,
        policy_decision=ToolPolicyDecision.ALLOW,
        result=result,
    )
    return ToolCallOutcome(execution=execution, turn=cast(TurnRef, SimpleNamespace()))


def test_output_guard_replaces_diagnosis_and_dangerous_advice() -> None:
    guard = SlimGuardOutputGuard()
    diagnosis = guard.review(
        text="你患有甲状腺疾病，建议每天服用20mg药物。",
        tool_outcomes=(),
    )

    assert diagnosis.modified is True
    assert diagnosis.code == "prohibited_medical_advice"
    assert "不能提供疾病诊断" in diagnosis.text


def test_output_guard_rejects_unverified_record_success() -> None:
    result = SlimGuardOutputGuard().review(
        text="记上了，午餐是米饭和鸡蛋。",
        tool_outcomes=(),
    )

    assert result.modified is True
    assert result.code == "unverified_record_write_claim"
    assert "没有找到本轮记录写入成功的回执" in result.text


def test_output_guard_does_not_mistake_failure_statement_for_success_claim() -> None:
    result = SlimGuardOutputGuard().review(
        text="这条记录没有保存成功。",
        tool_outcomes=(),
    )

    assert result.modified is False


def test_output_guard_trusts_registered_write_category_not_tool_name() -> None:
    result = SlimGuardOutputGuard().review(
        text="午餐已记录。",
        tool_outcomes=(outcome(status="succeeded"),),
    )

    assert result.modified is False


def test_all_health_record_update_tools_declare_record_mutation_category() -> None:
    updates = {
        tool.name: tool
        for tool in health_record_tool_definitions()
        if tool.name.startswith("update_")
        and tool.effect_level is ToolEffectLevel.REVERSIBLE_WRITE
    }

    assert {
        "update_meal_record",
        "update_exercise_record",
        "update_weight_record",
        "update_body_fat_record",
    } <= updates.keys()
    assert all(
        tool.mutation_kind is ToolMutationKind.HEALTH_RECORD for tool in updates.values()
    )


def test_output_guard_distinguishes_definite_non_application_from_unknown() -> None:
    guard = SlimGuardOutputGuard()
    not_applied = guard.review(
        text="午餐已经记上了。",
        tool_outcomes=(
            outcome(status="failed", effect_status=ToolEffectStatus.NOT_APPLIED),
        ),
    )
    unknown = guard.review(
        text="午餐已经记上了。",
        tool_outcomes=(
            outcome(
                status="failed",
                effect_status=ToolEffectStatus.UNKNOWN,
                operation_id="operation-1",
            ),
        ),
    )

    assert not_applied.code == "failed_record_write_claim"
    assert "没有保存成功" in not_applied.text
    assert "重发" not in not_applied.text
    assert unknown.code == "unknown_record_write_claim"
    assert "暂勿" not in unknown.text
    assert "不要重复提交" in unknown.text


def test_output_guard_reports_partial_success() -> None:
    result = SlimGuardOutputGuard().review(
        text="饮食和运动都记录好了。",
        tool_outcomes=(
            outcome(status="succeeded", tool_name="record_meal"),
            outcome(
                status="failed",
                tool_name="record_exercise",
                effect_status=ToolEffectStatus.NOT_APPLIED,
            ),
        ),
    )

    assert result.code == "partial_record_write_claim"
    assert "部分记录操作完成" in result.text


def test_output_guard_does_not_ask_to_repeat_unknown_write_even_if_another_succeeded() -> None:
    result = SlimGuardOutputGuard().review(
        text="午餐和运动都记好了。",
        tool_outcomes=(
            outcome(status="succeeded", tool_name="record_meal"),
            outcome(
                status="failed",
                tool_name="record_exercise",
                effect_status=ToolEffectStatus.UNKNOWN,
                operation_id="operation-2",
            ),
        ),
    )

    assert result.code == "unknown_record_write_claim"
    assert "部分记录操作已成功" in result.text
    assert "不要重复提交" in result.text


def test_output_guard_blocks_resending_unknown_operation_even_without_success_claim() -> None:
    result = SlimGuardOutputGuard().review(
        text="刚才没成功，请把午餐再发一次。",
        tool_outcomes=(
            outcome(
                status="failed",
                effect_status=ToolEffectStatus.UNKNOWN,
                operation_id="operation-unknown",
            ),
        ),
    )

    assert result.code == "unknown_record_write_claim"
    assert "不要重复提交" in result.text


def test_output_guard_treats_inspected_not_applied_operation_as_definite_failure() -> None:
    unresolved = outcome(
        status="failed",
        effect_status=ToolEffectStatus.UNKNOWN,
        operation_id="operation-not-applied",
    )
    inspected = outcome(
        status="succeeded",
        tool_name="get_tool_operation",
        output={
            "operation": {"operation_id": "operation-not-applied", "status": "not_applied"}
        },
    ).execution.model_copy(update={"mutation_kind": ToolMutationKind.SYSTEM_OPERATION})
    result = SlimGuardOutputGuard().review(
        text="这条记录已经保存成功。",
        tool_outcomes=(unresolved, ToolCallOutcome(execution=inspected, turn=unresolved.turn)),
    )

    assert result.code == "failed_record_write_claim"
    assert "没有保存成功" in result.text
    assert "不要重复提交" not in result.text


def test_output_guard_accepts_unknown_write_after_same_operation_is_verified() -> None:
    unresolved = outcome(
        status="failed",
        effect_status=ToolEffectStatus.UNKNOWN,
        operation_id="operation-3",
    )
    verified = outcome(
        status="succeeded",
        tool_name="get_tool_operation",
        output={
            "operation": {"operation_id": "operation-3", "status": "succeeded"}
        },
    ).execution.model_copy(update={"mutation_kind": ToolMutationKind.SYSTEM_OPERATION})
    result = SlimGuardOutputGuard().review(
        text="这条记录已经保存成功。",
        tool_outcomes=(unresolved, ToolCallOutcome(execution=verified, turn=unresolved.turn)),
    )

    assert result.modified is False


def test_output_guard_replaces_raw_tool_call_markup() -> None:
    result = SlimGuardOutputGuard().review(
        text='<tool_call>record_meal({"meal_type":"lunch"})</tool_call>',
        tool_outcomes=(),
    )

    assert result.modified is True
    assert result.code == "raw_tool_call_leak"
    assert "操作还没有完成" in result.text
