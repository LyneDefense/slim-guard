"""Application-level composition and entry points for the SlimGuard agent."""

from slim_guard.agent.composition import (
    AgentRuntimeDefinition,
    build_agent_runtime,
)
from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION
from slim_guard.agent.runtime import (
    AgentRuntime,
    AgentRuntimeProtocol,
    AgentRuntimeRequest,
    AgentRuntimeResult,
)

__all__ = [
    "AgentRuntime",
    "AgentRuntimeDefinition",
    "AgentRuntimeProtocol",
    "AgentRuntimeRequest",
    "AgentRuntimeResult",
    "SLIM_GUARD_HARNESS_PROMPT",
    "SLIM_GUARD_PROMPT_VERSION",
    "build_agent_runtime",
]
