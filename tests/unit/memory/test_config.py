from __future__ import annotations

import pytest
from pydantic import ValidationError

from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.memory.config import MemoryRuntimeConfig


def test_memory_config_defaults() -> None:
    assert MemoryRuntimeConfig().model_dump() == {
        "preload_max_facts": 30,
        "health_review_days": 180,
        "recent_turn_count": 3,
        "recent_dialogue_max_chars": 1500,
        "recall_search_limit": 12,
        "recall_max_selected": 8,
    }


@pytest.mark.parametrize(
    ("field", "lower", "upper"),
    [
        ("preload_max_facts", 1, 100),
        ("health_review_days", 30, 730),
        ("recent_turn_count", 1, 10),
        ("recent_dialogue_max_chars", 100, 10_000),
        ("recall_search_limit", 1, 100),
        ("recall_max_selected", 1, 20),
    ],
)
def test_memory_config_bounds(field: str, lower: int, upper: int) -> None:
    for value in (lower, upper):
        config = MemoryRuntimeConfig.model_validate({field: value})
        assert getattr(config, field) == value

    for value, error_type in (
        (lower - 1, "greater_than_equal"),
        (upper + 1, "less_than_equal"),
    ):
        with pytest.raises(ValidationError) as error:
            MemoryRuntimeConfig.model_validate({field: value})
        assert error.value.errors()[0]["loc"] == (field,)
        assert error.value.errors()[0]["type"] == error_type


def test_memory_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError) as error:
        MemoryRuntimeConfig.model_validate({"unknown_setting": 1})
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_memory_config_is_immutable() -> None:
    config = MemoryRuntimeConfig()
    with pytest.raises(ValidationError) as error:
        config.recent_turn_count = 4
    assert error.value.errors()[0]["type"] == "frozen_instance"
    assert config.recent_turn_count == 3


def test_runtime_definition_uses_default_memory_config() -> None:
    definition = AgentRuntimeDefinition(
        models=ModelRuntimeConfig(
            provider="test",
            text_model="test",
            vision_model="test",
        ),
        code_revision="test",
    )
    assert definition.memory == MemoryRuntimeConfig()


@pytest.mark.parametrize("as_mapping", [False, True], ids=["config_object", "mapping"])
def test_runtime_definition_preserves_custom_memory_config(as_mapping: bool) -> None:
    config = MemoryRuntimeConfig(
        preload_max_facts=40,
        health_review_days=90,
        recent_turn_count=6,
        recent_dialogue_max_chars=3000,
        recall_search_limit=24,
        recall_max_selected=10,
    )
    definition = AgentRuntimeDefinition.model_validate(
        {
            "models": {"provider": "test", "text_model": "test", "vision_model": "test"},
            "code_revision": "test",
            "memory": config.model_dump() if as_mapping else config,
        }
    )
    assert isinstance(definition.memory, MemoryRuntimeConfig)
    assert definition.memory == config


def test_runtime_definition_validates_nested_memory_config() -> None:
    with pytest.raises(ValidationError) as error:
        AgentRuntimeDefinition.model_validate(
            {
                "models": {"provider": "test", "text_model": "test", "vision_model": "test"},
                "code_revision": "test",
                "memory": {"recent_turn_count": 0},
            }
        )
    assert error.value.errors()[0]["loc"] == ("memory", "recent_turn_count")
    assert error.value.errors()[0]["type"] == "greater_than_equal"
