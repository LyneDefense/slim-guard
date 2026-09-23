"""Describe the assembled Core runtime for version recording."""

from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.prompt import SLIM_GUARD_HARNESS_PROMPT, SLIM_GUARD_PROMPT_VERSION
from slim_guard.agent.tool_catalog import build_core_tool_registry
from slim_guard.harness.context_data import CONTEXT_POLICY_VERSION
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.safety import SAFETY_POLICY_VERSION
from slim_guard.memory.repository import MEMORY_POLICY_VERSION
from slim_guard.memory.working import COMPACTION_POLICY_VERSION
from slim_guard.tools.registry import ToolRegistry


def build_agent_manifest(
    definition: AgentRuntimeDefinition,
    *,
    registry: ToolRegistry | None = None,
) -> AgentManifest:
    """Describe the supplied runtime registry, or build the shared Core catalog."""

    if registry is None:
        registry = build_core_tool_registry()
    return AgentManifest.build(
        model_provider=definition.models.provider,
        text_model=definition.models.text_model,
        vision_model=definition.models.vision_model,
        model_parameters=definition.models.parameters,
        system_prompt_version=SLIM_GUARD_PROMPT_VERSION,
        system_prompt=SLIM_GUARD_HARNESS_PROMPT,
        tool_versions=registry.versions,
        context_policy_version=CONTEXT_POLICY_VERSION,
        memory_policy_version=MEMORY_POLICY_VERSION,
        compaction_policy_version=COMPACTION_POLICY_VERSION,
        safety_policy_version=SAFETY_POLICY_VERSION,
        code_revision=definition.code_revision,
    )
