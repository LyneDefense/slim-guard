import pytest
from pydantic import ValidationError

from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.tasks.config import TaskRuntimeConfig


def test_runtime_definition_uses_independent_task_configs() -> None:
    models = ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision")
    first = AgentRuntimeDefinition(models=models, code_revision="test")
    second = AgentRuntimeDefinition(models=models, code_revision="test")
    assert first.tasks == TaskRuntimeConfig()
    assert first.tasks is not second.tasks


def test_runtime_definition_preserves_nested_execution_settings() -> None:
    values = {
        "models": {"provider": "test", "text_model": "text", "vision_model": "vision"},
        "code_revision": "test",
        "tasks": {"clarification_ttl_days": 21},
    }
    definition = AgentRuntimeDefinition.model_validate(values)
    assert definition.tasks.model_dump() == values["tasks"]
    assert AgentRuntimeDefinition.model_validate_json(definition.model_dump_json()) == definition


@pytest.mark.parametrize(
    ("section", "field"),
    [("tasks", "clarification_ttl_days")],
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
