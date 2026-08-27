from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.context import ContextCompiler
from slim_guard.harness.events import ItemType, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import (
    TurnInitializationRequest,
    TurnInitializer,
    TurnInput,
)
from slim_guard.harness.limits import HarnessLimits
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.runner import HarnessTurnGrants, HarnessTurnRunner
from slim_guard.harness.state_repository import HarnessStateRepository, TurnRef
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.tools.contracts import (
    ToolArguments,
    ToolContext,
    ToolEffectLevel,
    ToolExecution,
    ToolExecutionMode,
    ToolPolicyDecision,
    ToolResult,
)
from slim_guard.tools.policy import ToolAuthorization
from slim_guard.tools.registry import RegisteredTool, ToolRegistry

SYSTEM_PROMPT = "You are SlimGuard."


class RecordWeightArguments(ToolArguments):
    weight_kg: float


class NoToolRunner:
    async def execute(self, **kwargs) -> ToolCallOutcome:
        raise AssertionError("No tool call was expected")


class RecordingToolRunner:
    def __init__(self) -> None:
        self.authorizations: list[ToolAuthorization] = []
        self.source_item_ids: list[str | None] = []

    async def execute(
        self,
        *,
        call: NormalizedToolCall,
        context: ToolContext,
        authorization: ToolAuthorization,
        source_item_id: str | None,
        now: datetime,
    ) -> ToolCallOutcome:
        self.authorizations.append(authorization)
        self.source_item_ids.append(source_item_id)
        return ToolCallOutcome(
            execution=ToolExecution(
                tool_call_id=call.id,
                tool_name=call.name,
                tool_version="v1",
                canonical_arguments=call.arguments,
                idempotency_key=f"execution-{call.id}",
                policy_decision=ToolPolicyDecision.ALLOW,
                result=ToolResult.success(output={"weight_kg": call.arguments["weight_kg"]}),
            ),
            turn=TurnRef(
                id=context.turn_id,
                thread_id=context.thread_id,
                agent_version_id=context.agent_version_id,
                trigger=TurnTrigger.USER_MESSAGE,
                status=TurnStatus.RUNNING,
                deadline_at=None,
                completed_at=None,
            ),
            pending_action=None,
        )


def tool_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            RegisteredTool(
                name="record_weight",
                description="Record one body weight measurement.",
                version="v1",
                arguments_model=RecordWeightArguments,
                effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
                idempotent=True,
                requires_confirmation=False,
                timeout_seconds=3,
            ),
        )
    )


def build_manifest(
    *,
    code_revision: str = "test-revision",
    with_tools: bool = False,
) -> AgentManifest:
    return AgentManifest.build(
        model_provider="zhipu",
        text_model="glm-5.2",
        vision_model="glm-5v-turbo",
        model_parameters={"max_output_tokens": 512},
        system_prompt_version="harness-v1",
        system_prompt=SYSTEM_PROMPT,
        tool_versions={"record_weight": "v1"} if with_tools else {},
        context_policy_version="single-turn-v1",
        memory_policy_version="none-v1",
        compaction_policy_version="none-v1",
        safety_policy_version="harness-v1",
        code_revision=code_revision,
    )


async def prepare_database(tmp_path) -> tuple[Database, SlimGuardUser]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'harness-runner.sqlite3'}")
    await database.create_schema()
    now = datetime.now(UTC)
    user = SlimGuardUser(id="user-1", first_seen_at=now, last_seen_at=now)
    async with database.session() as session, session.begin():
        session.add(user)
    return database, user


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


def tool_response() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(
                    id="call-1",
                    name="record_weight",
                    arguments={"weight_kg": 77.6},
                ),
            ),
        ),
        finish_reason="tool_calls",
    )


def initialization_request(
    *,
    user_id: str,
    agent_version_id: str,
    deadline_at: datetime | None = None,
) -> TurnInitializationRequest:
    return TurnInitializationRequest(
        user_id=user_id,
        agent_version_id=agent_version_id,
        trigger=TurnTrigger.USER_MESSAGE,
        execution_mode=ToolExecutionMode.EVALUATION,
        deadline_at=deadline_at,
        inputs=(
            TurnInput.user_message(
                text="今天 77.6kg",
                source_message_id="wecom-message-1",
                channel_id="default",
            ),
        ),
    )


def build_runner(
    *,
    repository: HarnessStateRepository,
    manifest: AgentManifest,
    registry: ToolRegistry,
    model: ScriptedModelGateway,
    tool_calls,
    current_time: datetime,
) -> HarnessTurnRunner:
    recorder = PersistentHarnessRunRecorder(repository)
    return HarnessTurnRunner(
        initializer=TurnInitializer(repository),
        compiler=ContextCompiler(
            manifest=manifest,
            system_prompt=SYSTEM_PROMPT,
            tools=registry,
        ),
        model=model,
        tool_calls=tool_calls,
        recorder=recorder,
        limits=HarnessLimits(),
        clock=lambda: current_time,
    )


async def test_runner_executes_and_persists_one_complete_turn(tmp_path) -> None:
    database, user = await prepare_database(tmp_path)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    model = ScriptedModelGateway((final_response("已收到。"),))
    current_time = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    runner = build_runner(
        repository=repository,
        manifest=manifest,
        registry=ToolRegistry(()),
        model=model,
        tool_calls=NoToolRunner(),
        current_time=current_time,
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                deadline_at=current_time + timedelta(seconds=30),
            )
        )

        items = await repository.list_items(result.initialized.turn.id)
        stored_turn = await repository.get_turn(result.initialized.turn.id)

        assert result.loop.termination is HarnessTermination.FINAL_RESPONSE
        assert result.final_text == "已收到。"
        assert [item.item_type for item in items] == [
            ItemType.USER_MESSAGE,
            ItemType.CONTEXT_SNAPSHOT,
            ItemType.MODEL_MESSAGE,
            ItemType.AGENT_MESSAGE,
        ]
        snapshot = items[1].payload
        assert snapshot["compiled_at"] == current_time.isoformat()
        assert snapshot["input_item_ids"] == [items[0].id]
        assert snapshot["request"]["messages"][2]["content"] == "今天 77.6kg"
        assert snapshot["authorization"] == {
            "allowed_tool_names": [],
            "confirmed_execution_keys": [],
            "reviewed_execution_keys": [],
            "isolated_write_environment": False,
        }
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.COMPLETED
    finally:
        await database.close()


async def test_runner_uses_same_tool_subset_for_model_and_authorization(tmp_path) -> None:
    database, user = await prepare_database(tmp_path)
    manifest = build_manifest(with_tools=True)
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    model = ScriptedModelGateway((tool_response(), final_response("体重已记录。")))
    tool_calls = RecordingToolRunner()
    current_time = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    runner = build_runner(
        repository=repository,
        manifest=manifest,
        registry=tool_registry(),
        model=model,
        tool_calls=tool_calls,
        current_time=current_time,
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=manifest.version_id,
            ),
            grants=HarnessTurnGrants(
                allowed_tool_names=("record_weight",),
                isolated_write_environment=True,
            ),
        )

        assert result.compiled is not None
        assert [tool.name for tool in result.compiled.request.tools] == ["record_weight"]
        assert tool_calls.authorizations[0].allowed_tool_names == frozenset(
            {"record_weight"}
        )
        assert tool_calls.authorizations[0].isolated_write_environment is True
        assert tool_calls.source_item_ids == [result.initialized.source_item_id]
        assert result.final_text == "体重已记录。"
    finally:
        await database.close()


async def test_context_failure_is_audited_and_fails_initialized_turn(tmp_path) -> None:
    database, user = await prepare_database(tmp_path)
    compiler_manifest = build_manifest(code_revision="compiler")
    requested_manifest = build_manifest(code_revision="requested")
    versions = AgentVersionRepository(database)
    await versions.register(compiler_manifest)
    await versions.register(requested_manifest)
    repository = HarnessStateRepository(database)
    model = ScriptedModelGateway((final_response("不应被调用"),))
    runner = build_runner(
        repository=repository,
        manifest=compiler_manifest,
        registry=ToolRegistry(()),
        model=model,
        tool_calls=NoToolRunner(),
        current_time=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=requested_manifest.version_id,
            )
        )

        items = await repository.list_items(result.initialized.turn.id)
        stored_turn = await repository.get_turn(result.initialized.turn.id)

        assert result.compiled is None
        assert result.loop.termination is HarnessTermination.FATAL_ERROR
        assert result.loop.failure is not None
        assert result.loop.failure.code == "context_compilation_error"
        assert [item.item_type for item in items] == [
            ItemType.USER_MESSAGE,
            ItemType.ERROR,
        ]
        assert items[1].payload["failure"]["code"] == "context_compilation_error"
        assert "message" not in items[1].payload["failure"]
        assert stored_turn is not None
        assert stored_turn.status is TurnStatus.FAILED
        assert model.requests == []
    finally:
        await database.close()
