from __future__ import annotations

import pytest

from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolExecutionMode,
)
from slim_guard.tools.policy import (
    DefaultToolPolicy,
    ToolAuthorization,
    ToolPolicyDecision,
)
from slim_guard.tools.registry import RegisteredTool


class EmptyArguments(ToolArguments):
    pass


def tool(
    effect_level: ToolEffectLevel,
    *,
    requires_confirmation: bool = False,
) -> RegisteredTool:
    return RegisteredTool(
        name="test_tool",
        description="A test capability.",
        version="v1",
        arguments_model=EmptyArguments,
        effect_level=effect_level,
        idempotent=True,
        requires_confirmation=requires_confirmation,
        timeout_seconds=1,
    )


def context(mode: ToolExecutionMode = ToolExecutionMode.LIVE) -> ToolContext:
    return ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        user_id="user-1",
        agent_version_id="agent-v1",
        execution_mode=mode,
    )


def authorization(
    *,
    allowed: bool = True,
    confirmed: bool = False,
    reviewed: bool = False,
    isolated: bool = False,
) -> ToolAuthorization:
    return ToolAuthorization(
        allowed_tool_names=frozenset({"test_tool"} if allowed else {"another_tool"}),
        confirmed_execution_keys=frozenset({"execution-key-1"} if confirmed else set()),
        reviewed_execution_keys=frozenset({"execution-key-1"} if reviewed else set()),
        isolated_write_environment=isolated,
    )


@pytest.mark.parametrize(
    ("effect_level", "expected"),
    [
        (ToolEffectLevel.READ, ToolPolicyDecision.ALLOW),
        (ToolEffectLevel.REVERSIBLE_WRITE, ToolPolicyDecision.ALLOW),
    ],
)
def test_policy_allows_authorized_low_risk_live_tools(
    effect_level: ToolEffectLevel,
    expected: ToolPolicyDecision,
) -> None:
    result = DefaultToolPolicy().evaluate(
        tool=tool(effect_level),
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(),
    )

    assert result.decision is expected


def test_policy_denies_unscoped_and_prohibited_tools() -> None:
    unscoped = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.READ),
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(allowed=False),
    )
    prohibited = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.PROHIBITED),
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(),
    )

    assert unscoped.decision is ToolPolicyDecision.DENY
    assert unscoped.code == "tool_not_authorized"
    assert prohibited.decision is ToolPolicyDecision.DENY
    assert prohibited.code == "tool_prohibited"


def test_policy_requires_isolation_for_non_live_writes() -> None:
    denied = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.REVERSIBLE_WRITE),
        context=context(ToolExecutionMode.EVALUATION),
        execution_key="execution-key-1",
        authorization=authorization(),
    )
    allowed = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.REVERSIBLE_WRITE),
        context=context(ToolExecutionMode.EVALUATION),
        execution_key="execution-key-1",
        authorization=authorization(isolated=True),
    )

    assert denied.code == "non_live_write_not_isolated"
    assert allowed.decision is ToolPolicyDecision.ALLOW


def test_policy_requires_call_bound_confirmation_for_sensitive_writes() -> None:
    pending = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.SENSITIVE_WRITE),
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(),
    )
    confirmed = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.SENSITIVE_WRITE),
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(confirmed=True),
    )
    changed_arguments = DefaultToolPolicy().evaluate(
        tool=tool(ToolEffectLevel.SENSITIVE_WRITE),
        context=context(),
        execution_key="execution-key-for-changed-arguments",
        authorization=authorization(confirmed=True),
    )

    assert pending.decision is ToolPolicyDecision.CONFIRM
    assert pending.code == "tool_confirmation_required"
    assert confirmed.decision is ToolPolicyDecision.ALLOW
    assert changed_arguments.decision is ToolPolicyDecision.CONFIRM


def test_policy_requires_review_then_confirmation_when_both_apply() -> None:
    external_tool = tool(
        ToolEffectLevel.EXTERNAL_EFFECT,
        requires_confirmation=True,
    )
    pending_review = DefaultToolPolicy().evaluate(
        tool=external_tool,
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(),
    )
    pending_confirmation = DefaultToolPolicy().evaluate(
        tool=external_tool,
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(reviewed=True),
    )
    allowed = DefaultToolPolicy().evaluate(
        tool=external_tool,
        context=context(),
        execution_key="execution-key-1",
        authorization=authorization(reviewed=True, confirmed=True),
    )

    assert pending_review.decision is ToolPolicyDecision.REVIEW
    assert pending_confirmation.decision is ToolPolicyDecision.CONFIRM
    assert allowed.decision is ToolPolicyDecision.ALLOW
