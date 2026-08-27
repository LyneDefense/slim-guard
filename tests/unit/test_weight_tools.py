from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.contracts import (
    ToolContext,
    ToolExecutionMode,
    ToolResultStatus,
)
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolGateway
from slim_guard.tools.policy import DefaultToolPolicy, ToolAuthorization
from slim_guard.tools.registry import ToolRegistry
from slim_guard.tools.weight import (
    GET_RECENT_WEIGHT_TREND_TOOL_NAME,
    RECORD_WEIGHT_TOOL_NAME,
    weight_tool_definitions,
    weight_tool_executors,
)

FIXED_NOW = datetime(2026, 8, 27, 7, 30, tzinfo=UTC)


async def prepare_gateway(
    tmp_path: Path,
) -> tuple[Database, ToolGateway, WeightRepository, ToolContext]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'weight-tools.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user-1",
                first_seen_at=FIXED_NOW,
                last_seen_at=FIXED_NOW,
            )
        )
    definitions = weight_tool_definitions()
    manifest = AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"max_output_tokens": 512},
        system_prompt_version="test-v1",
        system_prompt="You are SlimGuard.",
        tool_versions={tool.name: tool.version for tool in definitions},
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test-revision",
    )
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="今天空腹 77.6kg"),),
        )
    )
    assert initialized.source_item_id is not None
    repository = WeightRepository(database)
    gateway = ToolGateway(
        registry=ToolRegistry(definitions),
        executors=weight_tool_executors(repository, clock=lambda: FIXED_NOW),
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    context = ToolContext(
        thread_id=initialized.thread.id,
        turn_id=initialized.turn.id,
        tool_call_id="call-record-1",
        user_id=initialized.thread.user_id,
        agent_version_id=manifest.version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
        source_item_id=initialized.source_item_id,
    )
    return database, gateway, repository, context


def authorization() -> ToolAuthorization:
    return ToolAuthorization(
        allowed_tool_names=frozenset(
            {RECORD_WEIGHT_TOOL_NAME, GET_RECENT_WEIGHT_TREND_TOOL_NAME}
        ),
        isolated_write_environment=True,
    )


async def test_record_and_read_weight_tools_use_authoritative_user_context(
    tmp_path: Path,
) -> None:
    database, gateway, repository, context = await prepare_gateway(tmp_path)
    try:
        recorded = await gateway.execute(
            call=NormalizedToolCall(
                id=context.tool_call_id,
                name=RECORD_WEIGHT_TOOL_NAME,
                arguments={
                    "value": 155.2,
                    "unit": "jin",
                    "condition": "fasting",
                    "measured_at": "2026-08-27T07:30:00+08:00",
                },
            ),
            context=context,
            authorization=authorization(),
        )
        assert recorded.result.status is ToolResultStatus.SUCCEEDED
        assert recorded.result.output["weight_kg"] == "77.6"
        assert recorded.result.output["created"] is True
        assert recorded.idempotency_key is not None

        stored = await repository.recent_trend("user-1")
        assert len(stored.records) == 1
        assert stored.current is not None
        assert stored.current.idempotency_key == recorded.idempotency_key
        assert stored.current.source_item_id == context.source_item_id
        assert stored.current.source_tool_call_id == context.tool_call_id

        trend_context = context.model_copy(update={"tool_call_id": "call-trend-1"})
        trend = await gateway.execute(
            call=NormalizedToolCall(
                id=trend_context.tool_call_id,
                name=GET_RECENT_WEIGHT_TREND_TOOL_NAME,
                arguments={"limit": 7},
            ),
            context=trend_context,
            authorization=authorization(),
        )
        assert trend.result.status is ToolResultStatus.SUCCEEDED
        assert trend.result.output["direction"] == "unknown"
        assert trend.result.output["records"][0]["weight_kg"] == "77.6"
        assert trend.result.source_ids == (stored.current.id,)
    finally:
        await database.close()


async def test_replayed_record_call_does_not_duplicate_weight(tmp_path: Path) -> None:
    database, gateway, repository, context = await prepare_gateway(tmp_path)
    call = NormalizedToolCall(
        id=context.tool_call_id,
        name=RECORD_WEIGHT_TOOL_NAME,
        arguments={"value": 77.6, "unit": "kg"},
    )
    try:
        first = await gateway.execute(
            call=call,
            context=context,
            authorization=authorization(),
        )
        replayed = await gateway.execute(
            call=call,
            context=context,
            authorization=authorization(),
        )

        trend = await repository.recent_trend("user-1")
        assert first.idempotency_key == replayed.idempotency_key
        assert first.result == replayed.result
        assert len(trend.records) == 1
    finally:
        await database.close()


async def test_model_cannot_override_weight_owner_or_execution_identity(
    tmp_path: Path,
) -> None:
    database, gateway, repository, context = await prepare_gateway(tmp_path)
    try:
        execution = await gateway.execute(
            call=NormalizedToolCall(
                id=context.tool_call_id,
                name=RECORD_WEIGHT_TOOL_NAME,
                arguments={
                    "value": 77.6,
                    "user_id": "user-2",
                    "execution_idempotency_key": "forged-key",
                },
            ),
            context=context,
            authorization=authorization(),
        )

        assert execution.result.status is ToolResultStatus.FAILED
        assert execution.result.failure is not None
        assert execution.result.failure.code == "invalid_arguments"
        assert not (await repository.recent_trend("user-1")).records
    finally:
        await database.close()


async def test_invalid_measurement_time_returns_safe_tool_failure(tmp_path: Path) -> None:
    database, gateway, repository, context = await prepare_gateway(tmp_path)
    try:
        execution = await gateway.execute(
            call=NormalizedToolCall(
                id=context.tool_call_id,
                name=RECORD_WEIGHT_TOOL_NAME,
                arguments={"value": 77.6, "measured_at": "this morning"},
            ),
            context=context,
            authorization=authorization(),
        )

        assert execution.result.status is ToolResultStatus.FAILED
        assert execution.result.failure is not None
        assert execution.result.failure.code == "invalid_measurement_time"
        assert not (await repository.recent_trend("user-1")).records
    finally:
        await database.close()
