"""Operational UI projection from receipts, never from generated doctor prose."""

from slim_guard.agents.core.contracts import CoreCompletion
from slim_guard.group_chat.composer import OutgoingTextMessage
from slim_guard.group_chat.contracts import ParticipantRole
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.tools.contracts import ToolMutationKind, ToolResultStatus
from slim_guard.tools.receipts import effective_executions

# Capability categories, not intent keywords or a sequence of business steps.
# Internal task/memory bookkeeping and reads do not announce themselves.
_VISIBLE_MUTATIONS = {
    ToolMutationKind.HEALTH_RECORD: "健康记录",
    ToolMutationKind.USER_PROFILE: "资料",
    ToolMutationKind.ROUTINE_SETTINGS: "提醒设置",
}


def operation_notices(
    *,
    completion: CoreCompletion,
    outcomes: tuple[ToolCallOutcome, ...],
    allowed_source_refs: tuple[str, ...],
) -> tuple[OutgoingTextMessage, ...]:
    notices = []
    if set(completion.failed_operation_refs) - set(allowed_source_refs):
        raise ValueError("Operation failure references unavailable evidence")
    for execution in effective_executions(tuple(o.execution for o in outcomes)):
        if execution.mutation_kind not in _VISIBLE_MUTATIONS:
            continue
        result = execution.result
        if (
            result.status is not ToolResultStatus.SUCCEEDED
            and result.operation is None
            and execution.tool_call_id not in completion.failed_operation_refs
        ):
            # Rejected arguments are observations, not executed operations. Core
            # selects unresolved failures after its own recovery decisions.
            continue
        label = _VISIBLE_MUTATIONS.get(execution.mutation_kind, "操作")
        if result.operation and result.operation.status in {"running", "unknown"}:
            text = f"{label}结果暂时无法确认，请勿重复提交。"
        elif (result.operation and result.operation.status == "succeeded") or (
            result.operation is None and result.status is ToolResultStatus.SUCCEEDED
        ):
            text = f"{label}已更新。"
        elif (result.operation and result.operation.status == "not_applied") or (
            result.failure and result.failure.effect_status.value == "not_applied"
        ):
            text = f"{label}未完成，未保存本次修改。"
        else:
            text = f"{label}结果暂时无法确认，请勿重复提交。"
        notices.append(
            OutgoingTextMessage(
                participant=ParticipantRole.SYSTEM_ASSISTANT,
                text=text,
                notice_text=text,
                source_refs=(execution.tool_call_id,),
            )
        )
    question = completion.operation_question
    if question:
        if set(question.source_refs) - set(allowed_source_refs):
            raise ValueError("Operation question references unavailable evidence")
        notices.append(
            OutgoingTextMessage(
                participant=ParticipantRole.SYSTEM_ASSISTANT,
                text=question.text,
                notice_text=question.text,
                requires_response=True,
                source_refs=question.source_refs,
            )
        )
    return tuple(notices)
