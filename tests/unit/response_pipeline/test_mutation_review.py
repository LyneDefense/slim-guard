"""Model identifies assertions; only backend receipts can prove their state."""

import json
from types import SimpleNamespace
from typing import cast

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import TurnRef
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.response_pipeline.mutation_review import (
    ModelMutationClaimReviewer,
    operation_evidence,
)
from slim_guard.tools.contracts import (
    ToolEffectStatus,
    ToolExecution,
    ToolMutationKind,
    ToolOperationReceipt,
    ToolResult,
)


def receipt(identity="op1", *, kind="user_profile", status="succeeded", output=None):
    result = (
        ToolResult.success(output=output or {})
        if status == "succeeded"
        else ToolResult.failed(
            code="rejected", message="rejected", effect_status=ToolEffectStatus(status)
        )
    )
    result = result.model_copy(
        update={
            "operation": ToolOperationReceipt(
                operation_id=identity, tool_name="arbitrary_registered_tool", status=status
            )
        }
    )
    return ToolCallOutcome(
        ToolExecution(
            tool_call_id=f"call-{identity}",
            tool_name="arbitrary_registered_tool",
            mutation_kind=ToolMutationKind(kind),
            result=result,
        ),
        cast(TurnRef, SimpleNamespace()),
    )


def reviewer(text, *, kind="user_profile", status="succeeded", ids=("op1",), quote=None):
    model = ScriptedModelGateway(
        [
            ModelResponse(
                message=ModelMessage(
                    role="assistant",
                    content=json.dumps(
                        {
                            "claims": [
                                {
                                    "quote": quote or text,
                                    "mutation_kind": kind,
                                    "status": status,
                                    "operation_ids": ids,
                                }
                            ]
                        }
                    ),
                )
            )
        ]
    )
    return ModelMutationClaimReviewer(model, "test"), model


async def test_recorded_preference_uses_profile_receipt_not_health_keyword_classification():
    text = "已记录你的口味限制：不能吃太辣。"
    outcomes = (receipt(),)
    assert SlimGuardOutputGuard().review(text=text, tool_outcomes=outcomes).modified is False
    guard, model = reviewer(text)
    result = await guard.review(text=text, tool_outcomes=outcomes)
    assert result.modified is False
    payload = json.loads(model.requests[0].messages[-1].content)
    assert payload["receipts"][0]["mutation_kind"] == "user_profile"
    assert payload["output_schema"]["properties"]["claims"]


@pytest.mark.parametrize("kind", ["user_profile", "user_memory", "health_record"])
async def test_claims_require_a_successful_receipt_of_the_same_registered_mutation_kind(kind):
    text = "这项操作已经保存成功。"
    guard, _ = reviewer(text, kind=kind)
    assert not (await guard.review(text=text, tool_outcomes=(receipt(kind=kind),))).modified
    guard, _ = reviewer(text, kind=kind)
    other = "health_record" if kind != "health_record" else "user_profile"
    assert (
        await guard.review(text=text, tool_outcomes=(receipt(kind=other),))
    ).code == "mutation_receipt_mismatch"


async def test_successful_corrected_write_is_not_poisoned_by_earlier_rejected_attempts():
    text = "口味限制已记下。"
    outcomes = (receipt("failed1", status="not_applied"), receipt())
    guard, _ = reviewer(text)
    assert not (await guard.review(text=text, tool_outcomes=outcomes)).modified


@pytest.mark.parametrize(
    "ids,quote,expected",
    [
        ((), None, "unverified_mutation_claim"),
        (("invented",), None, "mutation_receipt_mismatch"),
        (("op1",), "不在候选中的话", "unverified_mutation_claim"),
    ],
)
async def test_invented_or_missing_proofs_and_fabricated_claim_spans_fail_closed(
    ids, quote, expected
):
    text = "口味限制已记下。"
    guard, _ = reviewer(text, ids=ids, quote=quote)
    assert (await guard.review(text=text, tool_outcomes=(receipt(),))).code == expected


async def test_unapplied_and_unknown_writes_never_support_a_success_assertion():
    for status in ("not_applied", "unknown"):
        guard, _ = reviewer("保存成功")
        result = await guard.review(text="保存成功", tool_outcomes=(receipt(status=status),))
        assert result.code == "mutation_status_mismatch"


async def test_a_successful_receipt_cannot_be_misreported_as_not_saved():
    text = "这项更改没有保存成功。"
    guard, _ = reviewer(text, status="not_applied")
    result = await guard.review(text=text, tool_outcomes=(receipt(),))
    assert result.code == "mutation_status_mismatch"
    assert "已经完成" in result.text


async def test_partial_success_is_not_presented_as_complete_success():
    guard, _ = reviewer("全部保存成功", ids=("op1", "op2"))
    result = await guard.review(
        text="全部保存成功", tool_outcomes=(receipt(), receipt("op2", status="not_applied"))
    )
    assert result.modified
    assert "部分更改完成" in result.text


async def test_review_failure_or_invalid_json_cannot_bypass_receipt_validation():
    model = ScriptedModelGateway(
        [ModelResponse(message=ModelMessage(role="assistant", content="not JSON"))]
    )
    result = await ModelMutationClaimReviewer(model, "test").review(
        text="保存成功", tool_outcomes=(receipt(),)
    )
    assert result.code == "mutation_review_unavailable"


async def test_no_operation_assertion_needs_no_receipts_but_still_requires_valid_semantic_review():
    model = ScriptedModelGateway(
        [ModelResponse(message=ModelMessage(role="assistant", content='{"claims":[]}'))]
    )
    result = await ModelMutationClaimReviewer(model, "test").review(
        text="今天过得怎么样？", tool_outcomes=()
    )
    assert not result.modified


def test_only_a_successful_lookup_can_resolve_an_unknown_operation():
    unknown = receipt(status="unknown")
    lookup = receipt(
        "lookup",
        kind="system_operation",
        output={"operation": {"operation_id": "op1", "status": "succeeded"}},
    )
    assert operation_evidence((unknown, lookup))[0]["status"] == "succeeded"
    failed_lookup = receipt("lookup", kind="system_operation", status="not_applied")
    assert operation_evidence((unknown, failed_lookup))[0]["status"] == "unknown"
