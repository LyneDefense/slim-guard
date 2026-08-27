from __future__ import annotations

import asyncio

import pytest

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolExecutionMode,
    ToolResult,
    ToolResultStatus,
)
from slim_guard.tools.errors import (
    ToolContextMismatchError,
    ToolGatewayConfigurationError,
)
from slim_guard.tools.gateway import ToolExecutor, ToolGateway
from slim_guard.tools.registry import RegisteredTool, ToolRegistry


class WeightArguments(ToolArguments):
    weight_kg: float


class EmptyArguments(ToolArguments):
    pass


def registered_tool(
    *,
    arguments_model: type[ToolArguments] = WeightArguments,
    timeout_seconds: float = 1,
) -> RegisteredTool:
    return RegisteredTool(
        name="record_weight",
        description="Record a weight explicitly provided by the current user.",
        version="v1",
        arguments_model=arguments_model,
        effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
        idempotent=True,
        requires_confirmation=False,
        timeout_seconds=timeout_seconds,
    )


def tool_context(*, tool_call_id: str = "call-1") -> ToolContext:
    return ToolContext(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_call_id=tool_call_id,
        user_id="user-1",
        agent_version_id="agent-version-1",
        execution_mode=ToolExecutionMode.EVALUATION,
    )


def tool_call(
    *,
    name: str = "record_weight",
    arguments: dict[str, object] | None = None,
) -> NormalizedToolCall:
    return NormalizedToolCall(
        id="call-1",
        name=name,
        arguments=arguments if arguments is not None else {"weight_kg": 77.6},
    )


async def test_gateway_validates_arguments_executes_handler_and_returns_audit_data() -> None:
    received: list[tuple[ToolContext, WeightArguments]] = []

    async def record_weight(
        context: ToolContext,
        arguments: WeightArguments,
    ) -> ToolResult:
        received.append((context, arguments))
        return ToolResult.success(
            output={"weight_kg": arguments.weight_kg},
            source_ids=("weight-1",),
        )

    tool = registered_tool()
    gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=record_weight,
            )
        },
    )

    execution = await gateway.execute(call=tool_call(), context=tool_context())

    assert received[0][1] == WeightArguments(weight_kg=77.6)
    assert execution.result.status is ToolResultStatus.SUCCEEDED
    assert execution.result.source_ids == ("weight-1",)
    assert execution.canonical_arguments == {"weight_kg": 77.6}
    assert execution.idempotency_key is not None
    assert execution.idempotency_key.startswith("tool-")


async def test_idempotency_key_is_stable_for_canonically_equal_arguments() -> None:
    async def record_weight(_: ToolContext, arguments: WeightArguments) -> ToolResult:
        return ToolResult.success(output={"weight_kg": arguments.weight_kg})

    tool = registered_tool()
    gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=record_weight,
            )
        },
    )

    first = await gateway.execute(call=tool_call(), context=tool_context())
    second = await gateway.execute(call=tool_call(), context=tool_context())

    assert first.idempotency_key == second.idempotency_key


async def test_gateway_returns_structured_unknown_tool_and_validation_failures() -> None:
    async def record_weight(_: ToolContext, arguments: WeightArguments) -> ToolResult:
        return ToolResult.success(output={"weight_kg": arguments.weight_kg})

    tool = registered_tool()
    gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=record_weight,
            )
        },
    )

    unknown = await gateway.execute(
        call=tool_call(name="delete_everything"),
        context=tool_context(),
    )
    invalid = await gateway.execute(
        call=tool_call(arguments={"weight_kg": "77.6", "user_id": "other"}),
        context=tool_context(),
    )

    assert unknown.result.failure is not None
    assert unknown.result.failure.code == "unknown_tool"
    assert unknown.tool_version is None
    assert invalid.result.failure is not None
    assert invalid.result.failure.code == "invalid_arguments"
    assert "weight_kg" in invalid.result.failure.message
    assert "user_id" in invalid.result.failure.message


async def test_gateway_converts_timeout_and_handler_exception_to_safe_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def slow(_: ToolContext, __: WeightArguments) -> ToolResult:
        await asyncio.sleep(0.02)
        return ToolResult.success(output={})

    async def broken(_: ToolContext, __: WeightArguments) -> ToolResult:
        raise RuntimeError("database password must not be exposed")

    tool = registered_tool(timeout_seconds=0.001)
    timeout_gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(arguments_model=WeightArguments, handler=slow)
        },
    )
    timeout = await timeout_gateway.execute(call=tool_call(), context=tool_context())

    broken_tool = registered_tool()
    broken_gateway = ToolGateway(
        registry=ToolRegistry((broken_tool,)),
        executors={
            broken_tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=broken,
            )
        },
    )
    failure = await broken_gateway.execute(call=tool_call(), context=tool_context())

    assert timeout.result.failure is not None
    assert timeout.result.failure.code == "tool_timeout"
    assert timeout.result.failure.retryable is True
    assert failure.result.failure is not None
    assert failure.result.failure.code == "tool_execution_failed"
    assert "password" not in failure.result.failure.message
    assert "password" not in caplog.text


def test_gateway_rejects_missing_and_mismatched_executors() -> None:
    tool = registered_tool()
    with pytest.raises(ToolGatewayConfigurationError, match="missing executors"):
        ToolGateway(registry=ToolRegistry((tool,)), executors={})

    async def wrong_handler(_: ToolContext, __: EmptyArguments) -> ToolResult:
        return ToolResult.success(output={})

    with pytest.raises(ToolGatewayConfigurationError, match="does not match"):
        ToolGateway(
            registry=ToolRegistry((tool,)),
            executors={
                tool.name: ToolExecutor(
                    arguments_model=EmptyArguments,
                    handler=wrong_handler,
                )
            },
        )


async def test_gateway_rejects_mismatched_call_context() -> None:
    async def record_weight(_: ToolContext, arguments: WeightArguments) -> ToolResult:
        return ToolResult.success(output={"weight_kg": arguments.weight_kg})

    tool = registered_tool()
    gateway = ToolGateway(
        registry=ToolRegistry((tool,)),
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=record_weight,
            )
        },
    )

    with pytest.raises(ToolContextMismatchError):
        await gateway.execute(
            call=tool_call(),
            context=tool_context(tool_call_id="different-call"),
        )
