from datetime import UTC, datetime
from unittest.mock import patch

from slim_guard.agent import composition
from slim_guard.agent.assembly import business, nutrition, responses, tools
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agents.nutrition import CONSULT_NUTRITION_TOOL_NAME
from slim_guard.db.session import Database
from slim_guard.memory.config import MemoryRuntimeConfig
from slim_guard.tasks.config import TaskRuntimeConfig


async def test_runtime_assembly_shares_registry_context_and_conversation_tasks() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    model = ScriptedModelGateway([])
    definition = AgentRuntimeDefinition(
        models=ModelRuntimeConfig(provider="test", text_model="text", vision_model="vision"),
        memory=MemoryRuntimeConfig(
            preload_max_facts=17,
            recent_turn_count=7,
            recent_dialogue_max_chars=2300,
        ),
        code_revision="assembly-test",
        tasks=TaskRuntimeConfig(clarification_ttl_days=42),
    )
    now = datetime(2026, 9, 20, tzinfo=UTC)
    try:
        built_tasks = []
        build_tasks = composition.build_task_components
        built_tool_calls = []
        build_tool_calls = tools.ToolCallCoordinator

        def capture_task_components(**kwargs):
            components = build_tasks(**kwargs)
            built_tasks.append(components)
            return components

        def capture_tool_calls(**kwargs):
            coordinator = build_tool_calls(**kwargs)
            built_tool_calls.append(coordinator)
            return coordinator

        with (
            patch.object(tools, "OperationGateway", wraps=tools.OperationGateway) as gateway,
            patch.object(tools, "ToolCallCoordinator", side_effect=capture_tool_calls),
            patch.object(
                composition,
                "build_task_components",
                side_effect=capture_task_components,
            ),
            patch.object(
                business,
                "AuthoritativeContextDataProvider",
                wraps=business.AuthoritativeContextDataProvider,
            ) as context,
            patch.object(
                composition, "ContextCompiler", wraps=composition.ContextCompiler
            ) as compiler,
            patch.object(composition, "TurnHarness", wraps=composition.TurnHarness) as harness,
            patch.object(composition, "CoreAgent", wraps=composition.CoreAgent) as core_agent,
            patch.object(
                composition, "CoreInvocationRunner", wraps=composition.CoreInvocationRunner
            ) as core_execution,
            patch.object(
                composition, "ResponseProcessor", wraps=composition.ResponseProcessor
            ) as processor,
            patch.object(
                nutrition,
                "NutritionSpecialist",
                wraps=nutrition.NutritionSpecialist,
            ) as specialist,
            patch.object(
                responses,
                "AgentResponseFinalizer",
                wraps=responses.AgentResponseFinalizer,
            ) as finalizer,
        ):
            runtime = composition.build_agent_runtime(
                database=database,
                model=model,
                definition=definition,
                clock=lambda: now,
            )

        registry = gateway.call_args.kwargs["registry"]
        assert CONSULT_NUTRITION_TOOL_NAME in registry.names
        assert set(gateway.call_args.kwargs["executors"]).issubset(set(registry.names))
        assert compiler.call_args.kwargs["tools"] is registry
        assert compiler.call_args.kwargs["manifest"] is runtime.manifest
        assert dict(runtime.manifest.tool_versions) == registry.versions

        assert len(built_tasks) == 1
        assert built_tasks[0].store is context.call_args.kwargs["tasks"]
        assert len(built_tool_calls) == 1
        assert core_agent.call_args.kwargs["tool_calls"] is built_tool_calls[0]
        assert core_execution.call_args.kwargs["graph_version"] == "core-primary-v1"
        assert specialist.call_args.kwargs["graph_version"] == "core-primary-v1"

        assert context.call_args.kwargs["memory_limit"] == 17
        assert context.call_args.kwargs["dialogue_turn_limit"] == 7
        assert context.call_args.kwargs["dialogue_char_limit"] == 2300
        assert context.call_args.kwargs["preload_domain_history"] is False
        assert finalizer.call_count == 1
        assert processor.call_args.kwargs["finalizer"] is not None
        assert set(harness.call_args.kwargs) == {
            "initializer",
            "preparation",
            "core",
            "responses",
            "recorder",
            "clock",
        }
        assert finalizer.call_args.kwargs["graph_version"] == "core-primary-v1"
        assert finalizer.call_args.kwargs["style_agent"] is not None
        assert finalizer.call_args.kwargs["reviewer_agent"] is not None
        assert finalizer.call_args.kwargs["conversation_agent"] is not None
        assert model.requests == []
    finally:
        await database.close()
