from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from slim_guard.tools.contracts import (
    ToolContext,
    ToolEffectLevel,
    ToolExecutionMode,
    ToolPolicyDecision,
)
from slim_guard.tools.registry import RegisteredTool


class ToolAuthorization(BaseModel):
    """Trusted grants compiled by the Harness, never supplied by the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_tool_names: frozenset[str]
    isolated_write_environment: bool = False


class ToolPolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ToolPolicyDecision
    code: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1024)


class ToolPolicy(Protocol):
    def evaluate(
        self,
        *,
        tool: RegisteredTool,
        context: ToolContext,
        execution_key: str,
        authorization: ToolAuthorization,
    ) -> ToolPolicyResult: ...


class DefaultToolPolicy:
    """Deterministic safety policy for executing registered tools."""

    def evaluate(
        self,
        *,
        tool: RegisteredTool,
        context: ToolContext,
        execution_key: str,
        authorization: ToolAuthorization,
    ) -> ToolPolicyResult:
        if tool.name not in authorization.allowed_tool_names:
            return ToolPolicyResult(
                decision=ToolPolicyDecision.DENY,
                code="tool_not_authorized",
                reason=f"The tool '{tool.name}' is not authorized for this turn.",
            )
        if tool.effect_level is ToolEffectLevel.PROHIBITED:
            return ToolPolicyResult(
                decision=ToolPolicyDecision.DENY,
                code="tool_prohibited",
                reason=f"The tool '{tool.name}' is prohibited.",
            )
        if (
            context.execution_mode is not ToolExecutionMode.LIVE
            and tool.effect_level is not ToolEffectLevel.READ
            and not authorization.isolated_write_environment
        ):
            return ToolPolicyResult(
                decision=ToolPolicyDecision.DENY,
                code="non_live_write_not_isolated",
                reason="Write tools require an isolated environment outside live mode.",
            )
        if tool.effect_level in {ToolEffectLevel.SENSITIVE_WRITE, ToolEffectLevel.EXTERNAL_EFFECT}:
            return ToolPolicyResult(
                decision=ToolPolicyDecision.DENY,
                code="management_operation_not_available",
                reason="Sensitive management and external operations are not Agent capabilities.",
            )
        return ToolPolicyResult(
            decision=ToolPolicyDecision.ALLOW,
            code="tool_allowed",
            reason=f"The tool '{tool.name}' is allowed.",
        )
