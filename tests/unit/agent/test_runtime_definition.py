import pytest
from pydantic import ValidationError

from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.harness.pending_config import PendingActionConfig


def test_runtime_definition_uses_independent_pending_configs() -> None:
    models = ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision")
    first = AgentRuntimeDefinition(models=models, code_revision="test")
    second = AgentRuntimeDefinition(models=models, code_revision="test")
    assert first.pending_actions == PendingActionConfig()
    assert first.pending_actions is not second.pending_actions


def test_runtime_definition_preserves_nested_execution_settings() -> None:
    values = {
        "models": {"provider": "test", "text_model": "text", "vision_model": "vision"},
        "code_revision": "test",
        "pending_actions": {"confirmation_ttl_seconds": 120, "review_ttl_seconds": 240},
    }
    definition = AgentRuntimeDefinition.model_validate(values)
    assert definition.pending_actions.model_dump() == values["pending_actions"]
    assert AgentRuntimeDefinition.model_validate_json(definition.model_dump_json()) == definition


@pytest.mark.parametrize(
    ("section", "field"),
    [("pending_actions", "confirmation_ttl_seconds")],
)
def test_runtime_definition_reports_nested_validation_error(section: str, field: str) -> None:
    with pytest.raises(ValidationError) as error:
        AgentRuntimeDefinition.model_validate(
            {
                "models": {"provider": "test", "text_model": "text", "vision_model": "vision"},
                "code_revision": "test",
                section: {field: 0},
            }
        )
    assert error.value.errors()[0]["loc"] == (section, field)
