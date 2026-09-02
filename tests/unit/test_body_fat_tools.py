from __future__ import annotations

from datetime import UTC, datetime

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.domain.body_fat.repository import BodyFatRepository
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tools.body_fat import (
    GET_RECENT_BODY_FAT_TREND_TOOL_NAME,
    RECORD_BODY_FAT_TOOL_NAME,
    body_fat_tool_definitions,
    body_fat_tool_executors,
)
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode, ToolResultStatus
from slim_guard.tools.execution_repository import ToolExecutionRepository
from slim_guard.tools.gateway import ToolGateway
from slim_guard.tools.policy import DefaultToolPolicy, ToolAuthorization
from slim_guard.tools.registry import ToolRegistry

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


async def prepare(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'body-fat-tools.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        session.add(SlimGuardUser(id="user-1", first_seen_at=NOW, last_seen_at=NOW))
    definitions = body_fat_tool_definitions()
    manifest = AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={},
        system_prompt_version="test-v1",
        system_prompt="test",
        tool_versions={tool.name: tool.version for tool in definitions},
        context_policy_version="test-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="test-v1",
        code_revision="test",
    )
    await AgentVersionRepository(database).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(database)).initialize(
        TurnInitializationRequest(
            user_id="user-1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text="体脂31"),),
        )
    )
    repository = BodyFatRepository(database)
    gateway = ToolGateway(
        registry=ToolRegistry(definitions),
        executors=body_fat_tool_executors(repository, clock=lambda: NOW),
        execution_store=ToolExecutionRepository(database),
        policy=DefaultToolPolicy(),
    )
    context = ToolContext(
        thread_id=initialized.thread.id,
        turn_id=initialized.turn.id,
        tool_call_id="call-body-fat",
        user_id="user-1",
        agent_version_id=manifest.version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
        source_item_id=initialized.source_item_id,
    )
    authorization = ToolAuthorization(
        allowed_tool_names=frozenset(
            {RECORD_BODY_FAT_TOOL_NAME, GET_RECENT_BODY_FAT_TREND_TOOL_NAME}
        ),
        isolated_write_environment=True,
    )
    return database, repository, gateway, context, authorization


async def test_body_fat_record_defaults_to_percent_and_is_idempotent(tmp_path) -> None:
    database, repository, gateway, context, authorization = await prepare(tmp_path)
    call = NormalizedToolCall(
        id=context.tool_call_id,
        name=RECORD_BODY_FAT_TOOL_NAME,
        arguments={"value": 31},
    )
    try:
        first = await gateway.execute(
            call=call,
            context=context,
            authorization=authorization,
        )
        replay = await gateway.execute(
            call=call,
            context=context,
            authorization=authorization,
        )
        trend = await repository.recent_trend("user-1")

        assert first.result.status is ToolResultStatus.SUCCEEDED
        assert first.result.output["body_fat_percent"] == "31"
        assert first.result == replay.result
        assert len(trend.records) == 1
        assert trend.current is not None
        assert trend.current.basis_points == 3100
    finally:
        await database.close()


async def test_body_fat_tool_rejects_out_of_range_measurement(tmp_path) -> None:
    database, repository, gateway, context, authorization = await prepare(tmp_path)
    try:
        result = await gateway.execute(
            call=NormalizedToolCall(
                id=context.tool_call_id,
                name=RECORD_BODY_FAT_TOOL_NAME,
                arguments={"value": 90},
            ),
            context=context,
            authorization=authorization,
        )

        assert result.result.status is ToolResultStatus.FAILED
        assert result.result.failure is not None
        assert result.result.failure.code == "invalid_body_fat_measurement"
        assert (await repository.recent_trend("user-1")).records == ()
    finally:
        await database.close()
