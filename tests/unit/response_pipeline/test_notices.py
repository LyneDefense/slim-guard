"""Only authoritative operational metadata can create notices and success cards."""

from types import SimpleNamespace

import pytest

from slim_guard.agents.core import CoreCompletion, OperationQuestion
from slim_guard.group_chat.composer import project_cards
from slim_guard.response_pipeline.notices import operation_notices
from slim_guard.tools.contracts import ToolExecution, ToolOperationReceipt, ToolResult


def outcome(result, *, kind="health_record", name="record_weight", call="write"):
    return SimpleNamespace(
        execution=ToolExecution(
            tool_call_id=call, tool_name=name, mutation_kind=kind, result=result
        )
    )


@pytest.mark.parametrize("kind", ["none", "user_memory", "conversation_task"])
def test_internal_context_tools_do_not_emit_system_notices(kind):
    notices = operation_notices(
        completion=CoreCompletion(),
        outcomes=(outcome(ToolResult.success(output={}), kind=kind, name="internal"),),
        allowed_source_refs=(),
    )
    assert notices == ()


@pytest.mark.parametrize(
    "status,successful", [("unknown", False), ("not_applied", False), ("succeeded", True)]
)
def test_status_query_never_masquerades_as_write_success(status, successful):
    failed = ToolResult.failed(
        code="transport", message="uncertain", effect_status="unknown"
    ).model_copy(
        update={
            "operation": ToolOperationReceipt(
                operation_id="op", tool_name="record_weight", status="unknown"
            )
        }
    )
    saved = ToolResult.success(output={"weight_kg": 77.6})
    query = ToolResult.success(
        output={
            "operation": {
                "operation_id": "op",
                "tool_name": "record_weight",
                "status": status,
                "result": saved.model_dump(mode="json") if successful else None,
            }
        }
    )
    outcomes = (
        outcome(failed),
        outcome(query, kind="none", name="get_tool_operation", call="query"),
    )
    notices = operation_notices(
        completion=CoreCompletion(), outcomes=outcomes, allowed_source_refs=()
    )
    assert len(notices) == 1
    assert ("已更新" in notices[0].text) is successful
    assert bool(project_cards(outcomes)) is successful


def test_duplicate_receipt_has_one_notice_and_card():
    result = ToolResult.success(output={"weight_kg": 77.6}).model_copy(
        update={
            "operation": ToolOperationReceipt(
                operation_id="op", tool_name="record_weight", status="succeeded"
            )
        }
    )
    outcomes = (outcome(result), outcome(result, name="resume_tool_operation", call="resume"))
    assert (
        len(
            operation_notices(
                completion=CoreCompletion(), outcomes=outcomes, allowed_source_refs=()
            )
        )
        == 1
    )
    assert len(project_cards(outcomes)) == 1


def test_operation_question_rejects_foreign_source_and_stays_persistent_when_valid():
    completion = CoreCompletion(
        operation_question=OperationQuestion(text="要改哪一条？", source_refs=("user-msg",))
    )
    with pytest.raises(ValueError, match="unavailable evidence"):
        operation_notices(completion=completion, outcomes=(), allowed_source_refs=())
    (notice,) = operation_notices(
        completion=completion, outcomes=(), allowed_source_refs=("user-msg",)
    )
    assert notice.requires_response is True
    assert notice.source_refs == ("user-msg",)
