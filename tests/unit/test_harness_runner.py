from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

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
from slim_guard.harness.safety import SlimGuardOutputGuard
from slim_guard.harness.state_repository import HarnessStateRepository, TurnRef
from slim_guard.harness.termination import HarnessTermination
from slim_guard.harness.tool_calls import ToolCallOutcome
from slim_guard.harness.trace import PersistentHarnessRunRecorder
from slim_guard.orchestration.coordinator import (
    AgentWorkflowCoordinator,
    direct_shadow_directive,
)
from slim_guard.orchestration.repository import OrchestrationRepository
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
    shadow_workflow: AgentWorkflowCoordinator | None = None,
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
        shadow_workflow=shadow_workflow,
        shadow_enabled_for=lambda _user_id: shadow_workflow is not None,
        workflow_mode="shadow" if shadow_workflow is not None else "off",
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


async def test_shadow_workflow_is_audited_without_replacing_legacy_reply(tmp_path) -> None:
    database, user = await prepare_database(tmp_path)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    directive = direct_shadow_directive("影子候选回复，不应发送。")
    model = ScriptedModelGateway(
        (
            final_response("当前 Harness 的真实回复。"),
            final_response(directive.model_dump_json()),
            final_response(
                json.dumps(
                    {
                        "schema_version": "1",
                        "text": "影子候选回复，不应发送。",
                        "used_block_ids": ["direct-response"],
                        "used_claim_ids": [],
                        "used_action_ids": [],
                        "preserved_risk_flags": [],
                        "preserved_citation_refs": [],
                        "style_profile_version": "slimguard_default_v1",
                    },
                    ensure_ascii=False,
                )
            ),
        )
    )
    current_time = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    recorder = PersistentHarnessRunRecorder(repository)
    shadow = AgentWorkflowCoordinator(
        model=model,
        recorder=recorder,
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        persistence=OrchestrationRepository(database),
        clock=lambda: current_time,
    )
    runner = build_runner(
        repository=repository,
        manifest=manifest,
        registry=ToolRegistry(()),
        model=model,
        tool_calls=NoToolRunner(),
        current_time=current_time,
        shadow_workflow=shadow,
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                deadline_at=current_time + timedelta(seconds=30),
            )
        )

        assert result.final_text == "当前 Harness 的真实回复。"
        assert result.shadow_workflow is not None
        assert result.shadow_workflow.shadow_candidate == "影子候选回复，不应发送。"
        assert result.shadow_workflow.delivered is False
        assert result.shadow_workflow.business_write_count == 0

        invocations = await OrchestrationRepository(database).list_turn_invocations(
            result.initialized.turn.id
        )
        artifacts = await OrchestrationRepository(database).list_artifacts(
            result.initialized.turn.id
        )
        items = await repository.list_items(result.initialized.turn.id)

        assert sorted((item.agent_role, item.status) for item in invocations) == [
            ("orchestrator", "succeeded"),
            ("response_style", "succeeded"),
        ]
        assert {item.artifact_type for item in artifacts} == {
            "directive",
            "response_plan",
            "style_resolution",
            "styled_response",
        }
        assert sum(item.item_type is ItemType.AGENT_MESSAGE for item in items) == 1
        adopted = [item for item in items if item.item_type is ItemType.RESPONSE_ADOPTED]
        styled_artifact = next(
            item for item in artifacts if item.artifact_type == "styled_response"
        )
        assert len(adopted) == 1
        assert adopted[0].payload == {
            "artifact_id": styled_artifact.artifact_id,
            "mode": "shadow",
            "final": False,
        }
    finally:
        await database.close()


@pytest.mark.parametrize(
    "mode,selected,reject",
    [
        ("on", True, False),
        ("canary", True, False),
        ("canary", False, False),
        ("off", True, False),
        ("on", True, True),
    ],
)
async def test_live_workflow_adoption_preserves_single_tool_execution_and_final_message(
    tmp_path,
    mode,
    selected,
    reject,
) -> None:
    database, user = await prepare_database(tmp_path)
    manifest = build_manifest(with_tools=True)
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    recorder = PersistentHarnessRunRecorder(repository)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    baseline = "已记录今天的体重 77.6kg。"
    calls = [
        ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(
                    NormalizedToolCall(
                        id="weight-once",
                        name="record_weight",
                        arguments={"weight_kg": 77.6},
                    ),
                ),
            )
        ),
        final_response(baseline),
    ]
    executes = mode in {"on", "canary"} and selected
    if executes:
        calls.extend(
            [
                final_response(direct_shadow_directive("确认本轮记录").model_dump_json()),
                final_response(
                    json.dumps(
                        {
                            "text": "已记录今天的体重 77.6kg！",
                            "used_block_ids": ["verified-harness-response"],
                            "style_profile_version": "slimguard_default_v1",
                        }
                    )
                ),
                final_response(
                    json.dumps(
                        {
                            "verdict": "reject",
                            "issue_type": "unsupported_claim",
                            "reason_summary": "需进一步确认",
                        }
                        if reject
                        else {"verdict": "pass"}
                    )
                ),
            ]
        )
    model = ScriptedModelGateway(calls)
    tools = RecordingToolRunner()
    workflow = AgentWorkflowCoordinator(
        model=model,
        recorder=recorder,
        model_name="test",
        graph_version="live-test",
        persistence=OrchestrationRepository(database),
        reviewer_enabled=True,
        clock=lambda: now,
    )
    runner = HarnessTurnRunner(
        initializer=TurnInitializer(repository),
        compiler=ContextCompiler(
            manifest=manifest, system_prompt=SYSTEM_PROMPT, tools=tool_registry()
        ),
        model=model,
        tool_calls=tools,
        recorder=recorder,
        limits=HarnessLimits(),
        output_guard=SlimGuardOutputGuard(),
        shadow_workflow=workflow,
        workflow_mode=mode,
        workflow_adopts_for=lambda _: selected,
        clock=lambda: now,
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                deadline_at=now + timedelta(seconds=30),
            )
        )
        expected = "已记录今天的体重 77.6kg！" if executes and not reject else baseline
        assert result.final_text == expected
        assert len(tools.authorizations) == 1
        model.assert_exhausted()
        items = await repository.list_items(result.initialized.turn.id)
        finals = [item for item in items if item.item_type is ItemType.AGENT_MESSAGE]
        assert len(finals) == 1 and finals[0].payload["text"] == expected
        adopted = [
            item
            for item in items
            if item.item_type is ItemType.RESPONSE_ADOPTED and item.payload.get("final")
        ]
        assert len(adopted) == int(executes and not reject)
        if executes:
            assert result.shadow_workflow.legacy_response == baseline
            assert not result.shadow_workflow.delivered
    finally:
        await database.close()


async def test_style_failure_uses_neutral_candidate_and_legacy_reply_continues(tmp_path) -> None:
    database, user = await prepare_database(tmp_path)
    manifest = build_manifest()
    await AgentVersionRepository(database).register(manifest)
    repository = HarnessStateRepository(database)
    directive = direct_shadow_directive("只保留这条既定内容。")
    model = ScriptedModelGateway(
        (
            final_response("线上回复仍正常。"),
            final_response(directive.model_dump_json()),
            final_response("{}"),
            final_response("{}"),
        )
    )
    current_time = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
    shadow = AgentWorkflowCoordinator(
        model=model,
        recorder=PersistentHarnessRunRecorder(repository),
        model_name="glm-5.2",
        graph_version="typed-supervisor-v1",
        persistence=OrchestrationRepository(database),
        clock=lambda: current_time,
    )
    runner = build_runner(
        repository=repository,
        manifest=manifest,
        registry=ToolRegistry(()),
        model=model,
        tool_calls=NoToolRunner(),
        current_time=current_time,
        shadow_workflow=shadow,
    )
    try:
        result = await runner.run(
            request=initialization_request(
                user_id=user.id,
                agent_version_id=manifest.version_id,
                deadline_at=current_time + timedelta(seconds=30),
            )
        )

        assert result.final_text == "线上回复仍正常。"
        assert result.shadow_workflow is not None
        assert result.shadow_workflow.status.value == "degraded"
        assert result.shadow_workflow.shadow_candidate == "只保留这条既定内容。"
        assert result.shadow_workflow.failure_code == "structured_output_invalid"
        assert "neutral_fallback" in result.shadow_workflow.actual_nodes
        invocations = await OrchestrationRepository(database).list_turn_invocations(
            result.initialized.turn.id
        )
        assert sorted((item.agent_role, item.status) for item in invocations) == [
            ("orchestrator", "succeeded"),
            ("response_style", "degraded"),
        ]
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
        assert tool_calls.authorizations[0].allowed_tool_names == frozenset({"record_weight"})
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
