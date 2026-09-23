from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.manifest import build_agent_manifest
from slim_guard.agent.tool_catalog import build_core_tool_registry
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agents.nutrition import (
    CONSULT_NUTRITION_TOOL_NAME,
    nutrition_agent_tool_definitions,
)
from slim_guard.tools.registry import ToolRegistry
from slim_guard.tools.weight import weight_tool_definitions


def test_catalog_matches_current_manifest() -> None:
    definition = AgentRuntimeDefinition(
        models=ModelRuntimeConfig(
            provider="test",
            text_model="test",
            vision_model="test",
        ),
        code_revision="test",
    )
    registry = build_core_tool_registry()
    assert CONSULT_NUTRITION_TOOL_NAME in registry.names
    assert registry.versions == dict(build_agent_manifest(definition).tool_versions)
    assert tuple(tool.name for tool in registry.model_definitions()) == registry.names


def test_catalog_preserves_business_and_nutrition_contracts() -> None:
    registry = build_core_tool_registry()
    for tool in (*weight_tool_definitions(), *nutrition_agent_tool_definitions()):
        assert registry.resolve(tool.name) == tool
        assert registry.resolve(tool.name).model_definition() == tool.model_definition()


def test_manifest_uses_supplied_registry_without_rebuilding_catalog() -> None:
    definition = AgentRuntimeDefinition(
        models=ModelRuntimeConfig(
            provider="test",
            text_model="test",
            vision_model="test",
        ),
        code_revision="test",
    )
    registry = ToolRegistry(weight_tool_definitions())

    manifest = build_agent_manifest(definition, registry=registry)

    assert dict(manifest.tool_versions) == registry.versions
