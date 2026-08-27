from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from slim_guard.tools.contracts import (
    ToolArguments,
    ToolEffectLevel,
    ToolResult,
    ToolResultStatus,
)
from slim_guard.tools.errors import DuplicateToolError, UnknownToolError
from slim_guard.tools.registry import RegisteredTool, ToolRegistry


class WeightArguments(ToolArguments):
    weight_kg: float


class EmptyArguments(ToolArguments):
    pass


def record_weight_tool() -> RegisteredTool:
    return RegisteredTool(
        name="record_weight",
        description="Record a weight explicitly provided by the current user.",
        version="v1",
        arguments_model=WeightArguments,
        effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
        idempotent=True,
        requires_confirmation=False,
        timeout_seconds=3,
    )


def profile_tool() -> RegisteredTool:
    return RegisteredTool(
        name="get_user_profile",
        description="Read the current user's profile.",
        version="v2",
        arguments_model=EmptyArguments,
        effect_level=ToolEffectLevel.READ,
        idempotent=True,
        requires_confirmation=False,
        timeout_seconds=1,
    )


def test_registry_preserves_order_and_exports_manifest_versions() -> None:
    registry = ToolRegistry((profile_tool(), record_weight_tool()))

    assert registry.names == ("get_user_profile", "record_weight")
    assert registry.versions == {"get_user_profile": "v2", "record_weight": "v1"}
    assert registry.resolve("record_weight").effect_level is ToolEffectLevel.REVERSIBLE_WRITE


def test_registry_exposes_only_selected_model_definitions_in_requested_order() -> None:
    registry = ToolRegistry((profile_tool(), record_weight_tool()))

    definitions = registry.model_definitions(("record_weight",))

    assert tuple(definition.name for definition in definitions) == ("record_weight",)
    assert definitions[0].version == "v1"
    assert definitions[0].parameters_json_schema["additionalProperties"] is False
    assert definitions[0].parameters_json_schema["required"] == ["weight_kg"]


def test_tool_arguments_are_strict_and_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WeightArguments.model_validate({"weight_kg": "77.6"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        WeightArguments.model_validate({"weight_kg": 77.6, "user_id": "someone-else"})


def test_registry_rejects_duplicate_and_unknown_tools() -> None:
    tool = record_weight_tool()
    with pytest.raises(DuplicateToolError, match="record_weight"):
        ToolRegistry((tool, tool))

    registry = ToolRegistry((tool,))
    with pytest.raises(UnknownToolError, match="missing_tool"):
        registry.resolve("missing_tool")


def test_tool_result_is_a_canonical_model_observation() -> None:
    result = ToolResult.success(
        output={"weight_kg": 77.6, "recorded": True},
        source_ids=("weight-record-1",),
    )

    assert json.loads(result.to_model_content()) == {
        "failure": None,
        "output": {"recorded": True, "weight_kg": 77.6},
        "source_ids": ["weight-record-1"],
        "status": "succeeded",
    }


def test_failed_tool_result_requires_structured_failure() -> None:
    with pytest.raises(ValidationError, match="require a failure"):
        ToolResult(status=ToolResultStatus.FAILED)

    failure = ToolResult.failed(
        code="temporary_unavailable",
        message="Try again later.",
        retryable=True,
    )
    assert failure.failure is not None
    assert failure.failure.retryable is True
