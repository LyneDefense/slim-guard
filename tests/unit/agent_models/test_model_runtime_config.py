from __future__ import annotations

import pytest
from pydantic import ValidationError

from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent.manifest import build_agent_manifest
from slim_guard.agent_models.config import ModelRuntimeConfig


@pytest.fixture
def model_values() -> dict[str, object]:
    return {"provider": "test", "text_model": "text-test", "vision_model": "vision-test"}


def test_model_config_defaults(model_values: dict[str, object]) -> None:
    config = ModelRuntimeConfig.model_validate(model_values)
    assert config.model_dump() == {
        **model_values,
        "parameters": {},
        "vision_max_output_tokens": 1024,
    }


@pytest.mark.parametrize("field", ["provider", "text_model", "vision_model"])
def test_model_choice_is_required(model_values: dict[str, object], field: str) -> None:
    del model_values[field]
    with pytest.raises(ValidationError) as error:
        ModelRuntimeConfig.model_validate(model_values)
    assert error.value.errors()[0]["loc"] == (field,)
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("field", "maximum"),
    [("provider", 128), ("text_model", 256), ("vision_model", 256)],
)
def test_model_choice_length(
    model_values: dict[str, object], field: str, maximum: int
) -> None:
    for length in (1, maximum):
        value = "x" * length
        config = ModelRuntimeConfig.model_validate({**model_values, field: value})
        assert getattr(config, field) == value
    for value, error_type in (("", "string_too_short"), ("x" * (maximum + 1), "string_too_long")):
        with pytest.raises(ValidationError) as error:
            ModelRuntimeConfig.model_validate({**model_values, field: value})
        assert error.value.errors()[0]["loc"] == (field,)
        assert error.value.errors()[0]["type"] == error_type


def test_vision_output_token_bounds(model_values: dict[str, object]) -> None:
    for value in (64, 32_768):
        config = ModelRuntimeConfig.model_validate(
            {**model_values, "vision_max_output_tokens": value}
        )
        assert config.vision_max_output_tokens == value
    for value, error_type in ((63, "greater_than_equal"), (32_769, "less_than_equal")):
        with pytest.raises(ValidationError) as error:
            ModelRuntimeConfig.model_validate({**model_values, "vision_max_output_tokens": value})
        assert error.value.errors()[0]["loc"] == ("vision_max_output_tokens",)
        assert error.value.errors()[0]["type"] == error_type


def test_model_config_preserves_request_parameters(model_values: dict[str, object]) -> None:
    parameters = {"thinking": {"type": "disabled"}, "do_sample": False, "max_output_tokens": 2048}
    config = ModelRuntimeConfig.model_validate({**model_values, "parameters": parameters})
    assert config.parameters == parameters
    assert ModelRuntimeConfig.model_validate_json(config.model_dump_json()) == config


def test_default_parameter_dictionaries_are_not_shared(model_values: dict[str, object]) -> None:
    first = ModelRuntimeConfig.model_validate(model_values)
    second = ModelRuntimeConfig.model_validate(model_values)
    first.parameters["do_sample"] = False
    assert second.parameters == {}


def test_model_config_rejects_unknown_fields(model_values: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        ModelRuntimeConfig.model_validate({**model_values, "unknown_setting": 1})
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_model_config_rejects_field_reassignment(model_values: dict[str, object]) -> None:
    config = ModelRuntimeConfig.model_validate(model_values)
    with pytest.raises(ValidationError) as error:
        config.text_model = "changed"
    assert error.value.errors()[0]["type"] == "frozen_instance"
    assert config.text_model == "text-test"


@pytest.mark.parametrize("as_mapping", [False, True], ids=["config_object", "mapping"])
def test_runtime_definition_preserves_model_config(
    model_values: dict[str, object], as_mapping: bool
) -> None:
    config = ModelRuntimeConfig.model_validate(
        {
            **model_values,
            "parameters": {"max_output_tokens": 2048, "do_sample": False},
            "vision_max_output_tokens": 4096,
        }
    )
    definition = AgentRuntimeDefinition.model_validate(
        {
            "models": config.model_dump() if as_mapping else config,
            "code_revision": "test",
        }
    )
    assert isinstance(definition.models, ModelRuntimeConfig)
    assert definition.models == config

    manifest = build_agent_manifest(definition)
    assert manifest.model_provider == config.provider
    assert manifest.text_model == config.text_model
    assert manifest.vision_model == config.vision_model
    assert manifest.model_parameters_dict() == config.parameters


def test_runtime_definition_validates_nested_model_config(model_values: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        AgentRuntimeDefinition.model_validate(
            {
                "models": {**model_values, "vision_max_output_tokens": 0},
                "code_revision": "test",
            }
        )
    assert error.value.errors()[0]["loc"] == ("models", "vision_max_output_tokens")
    assert error.value.errors()[0]["type"] == "greater_than_equal"
