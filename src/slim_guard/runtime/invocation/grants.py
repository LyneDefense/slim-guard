"""Trusted permissions for one Agent invocation."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from slim_guard.runtime.contracts import AgentInvocation, AgentRole


class InvocationAuthorizationError(RuntimeError):
    """An Agent invocation requested authority beyond its trusted grant."""


class InvocationGrant(BaseModel):
    """Trusted maximum permissions configured for an Agent invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_role: AgentRole
    allowed_tools: frozenset[str] = Field(
        default_factory=frozenset,
        validation_alias=AliasChoices("allowed_tools", "allowed_tool_names"),
        max_length=64,
    )
    privacy_scopes: frozenset[str] = Field(default_factory=frozenset, max_length=64)


def validate_invocation_grant(invocation: AgentInvocation, grant: InvocationGrant) -> None:
    """Reject model, tool or scope escalation."""

    if invocation.agent_role is not grant.agent_role:
        raise InvocationAuthorizationError(
            f"Invocation role {invocation.agent_role.value} does not match its grant"
        )
    unauthorized_tools = set(invocation.allowed_tools).difference(grant.allowed_tools)
    if unauthorized_tools:
        raise InvocationAuthorizationError(
            "Invocation requested unauthorized tools: " + ", ".join(sorted(unauthorized_tools))
        )
    unauthorized_scopes = set(invocation.privacy_scopes).difference(grant.privacy_scopes)
    if unauthorized_scopes:
        raise InvocationAuthorizationError(
            "Invocation requested unauthorized privacy scopes: "
            + ", ".join(sorted(unauthorized_scopes))
        )
