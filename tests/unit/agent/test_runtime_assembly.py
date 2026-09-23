from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from slim_guard.agent import composition
from slim_guard.agent.assembly import business, nutrition, responses, tools
from slim_guard.agent.definition import AgentRuntimeDefinition
from slim_guard.agent_models.config import ModelRuntimeConfig
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agents.nutrition import CONSULT_NUTRITION_TOOL_NAME
from slim_guard.db.session import Database
from slim_guard.harness.pending_config import PendingActionConfig
from slim_guard.memory.config import MemoryRuntimeConfig


async def test_runtime_assembly_shares_registry_context_and_confirmation_state() -> None:
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
        pending_actions=PendingActionConfig(confirmation_ttl_seconds=42, review_ttl_seconds=84),
    )
    now = datetime(2026, 9, 20, tzinfo=UTC)
    try:
        with (
            patch.object(tools, "ToolGateway", wraps=tools.ToolGateway) as gateway,
            patch.object(tools, "ToolCallCoordinator", wraps=tools.ToolCallCoordinator) as calls,
            patch.object(
                tools,
                "PendingActionResumeCoordinator",
                wraps=tools.PendingActionResumeCoordinator,
            ) as resume,
            patch.object(
                business,
                "AuthoritativeContextDataProvider",
                wraps=business.AuthoritativeContextDataProvider,
            ) as context,
            patch.object(
                composition, "ContextCompiler", wraps=composition.ContextCompiler
            ) as compiler,
            patch.object(composition, "TurnHarness", wraps=composition.TurnHarness) as harness,
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
        assert set(gateway.call_args.kwargs["executors"]) == set(registry.names)
        assert compiler.call_args.kwargs["tools"] is registry
        assert compiler.call_args.kwargs["manifest"] is runtime.manifest
        assert dict(runtime.manifest.tool_versions) == registry.versions

        pending = calls.call_args.kwargs["pending_actions"]
        assert context.call_args.kwargs["pending_actions"] is pending
        assert resume.call_args.kwargs["pending_actions"] is pending
        assert resume.call_args.kwargs["turn_state"] is calls.call_args.kwargs["turn_state"]
        assert resume.call_args.kwargs["tool_calls"] is harness.call_args.kwargs["tool_calls"]
        assert calls.call_args.kwargs["confirmation_ttl"] == timedelta(seconds=42)
        assert calls.call_args.kwargs["review_ttl"] == timedelta(seconds=84)
        assert harness.call_args.kwargs["graph_version"] == "core-primary-v1"
        assert specialist.call_args.kwargs["graph_version"] == "core-primary-v1"

        assert context.call_args.kwargs["memory_limit"] == 17
        assert context.call_args.kwargs["dialogue_turn_limit"] == 7
        assert context.call_args.kwargs["dialogue_char_limit"] == 2300
        assert context.call_args.kwargs["preload_domain_history"] is False
        assert finalizer.call_count == 1
        assert harness.call_args.kwargs["response_finalizer"] is not None
        assert finalizer.call_args.kwargs["graph_version"] == "core-primary-v1"
        assert finalizer.call_args.kwargs["style_agent"] is not None
        assert finalizer.call_args.kwargs["reviewer_agent"] is not None
        assert finalizer.call_args.kwargs["conversation_agent"] is not None
        assert model.requests == []
    finally:
        await database.close()
