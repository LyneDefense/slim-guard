from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.gateway import MessageRole, ToolChoice
from slim_guard.harness.context import ContextCompiler
from slim_guard.harness.errors import ContextCompilationError
from slim_guard.harness.events import ItemStatus, ItemType, ThreadStatus, TurnStatus, TurnTrigger
from slim_guard.harness.initialization import InitializedTurn
from slim_guard.harness.loop import HarnessTurnContext
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.state_repository import ItemRef, ThreadRef, TurnRef
from slim_guard.tools.contracts import ToolArguments, ToolEffectLevel, ToolExecutionMode
from slim_guard.tools.registry import RegisteredTool, ToolRegistry

SYSTEM_PROMPT = "You are SlimGuard."


class RecordWeightArguments(ToolArguments):
    weight_kg: float


class InspectImageArguments(ToolArguments):
    asset_id: str


def registry(*, record_weight_version: str = "v1") -> ToolRegistry:
    return ToolRegistry(
        (
            RegisteredTool(
                name="record_weight",
                description="Record one measured body weight.",
                version=record_weight_version,
                arguments_model=RecordWeightArguments,
                effect_level=ToolEffectLevel.REVERSIBLE_WRITE,
                idempotent=True,
                requires_confirmation=False,
                timeout_seconds=3,
            ),
            RegisteredTool(
                name="inspect_image",
                description="Inspect one uploaded image asset.",
                version="v2",
                arguments_model=InspectImageArguments,
                effect_level=ToolEffectLevel.READ,
                idempotent=True,
                requires_confirmation=False,
                timeout_seconds=10,
            ),
        )
    )


def manifest(**overrides: object) -> AgentManifest:
    values: dict[str, object] = {
        "model_provider": "zhipu",
        "text_model": "glm-5.2",
        "vision_model": "glm-5v-turbo",
        "model_parameters": {
            "thinking": {"type": "disabled"},
            "max_output_tokens": 600,
            "temperature": 0.2,
        },
        "system_prompt_version": "harness-v1",
        "system_prompt": SYSTEM_PROMPT,
        "tool_versions": {"record_weight": "v1", "inspect_image": "v2"},
        "context_policy_version": "single-turn-v1",
        "memory_policy_version": "none-v1",
        "compaction_policy_version": "none-v1",
        "safety_policy_version": "harness-v1",
        "code_revision": "test-revision",
    }
    values.update(overrides)
    return AgentManifest.build(**values)  # type: ignore[arg-type]


def initialized_turn(
    agent_manifest: AgentManifest,
    *,
    items: tuple[ItemRef, ...] | None = None,
    trigger: TurnTrigger = TurnTrigger.USER_MESSAGE,
) -> InitializedTurn:
    thread = ThreadRef(id="thread-1", user_id="user-secret-1", status=ThreadStatus.ACTIVE)
    turn = TurnRef(
        id="turn-1",
        thread_id=thread.id,
        agent_version_id=agent_manifest.version_id,
        trigger=trigger,
        status=TurnStatus.RUNNING,
        deadline_at=None,
        completed_at=None,
    )
    input_items = items
    if input_items is None:
        input_items = (
            ItemRef(
                id="item-text",
                turn_id=turn.id,
                sequence=1,
                item_type=ItemType.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                payload={"text": "今天 77.6kg"},
            ),
            ItemRef(
                id="item-image",
                turn_id=turn.id,
                sequence=2,
                item_type=ItemType.IMAGE_ATTACHMENT,
                status=ItemStatus.COMPLETED,
                payload={"asset_id": "asset-scale-1", "mime_type": "image/jpeg"},
            ),
        )
    context = HarnessTurnContext(
        thread_id=thread.id,
        turn_id=turn.id,
        user_id=thread.user_id,
        agent_version_id=turn.agent_version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
    )
    return InitializedTurn(
        thread=thread,
        turn=turn,
        input_items=input_items,
        context=context,
        source_item_id=input_items[0].id if input_items else None,
    )


def test_compiler_builds_minimal_versioned_model_request() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )
    current_time = datetime(2026, 8, 27, 8, 30, tzinfo=UTC)

    compiled = compiler.compile(
        initialized=initialized_turn(agent_manifest),
        current_time=current_time,
    )

    request = compiled.request
    assert request.model == "glm-5.2"
    assert request.max_output_tokens == 600
    assert request.temperature == 0.2
    assert request.tool_choice is ToolChoice.AUTO
    assert compiled.allowed_tool_names == ("record_weight", "inspect_image")
    assert [tool.name for tool in request.tools] == ["record_weight", "inspect_image"]
    assert [message.role for message in request.messages] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.USER,
    ]
    assert request.messages[0].content == SYSTEM_PROMPT
    assert "不可信数据" in (request.messages[1].content or "")
    assert current_time.isoformat() in (request.messages[1].content or "")
    assert request.messages[2].content == "今天 77.6kg"
    assert json.loads(request.messages[3].content or "") == {
        "asset_id": "asset-scale-1",
        "mime_type": "image/jpeg",
        "type": "image_attachment",
    }
    assert compiled.input_item_ids == ("item-text", "item-image")
    assert request.metadata["agent_version_id"] == agent_manifest.version_id
    assert request.metadata["user_id"] == hashlib.sha256(
        b"user-secret-1"
    ).hexdigest()
    assert "user-secret-1" not in request.metadata.values()


def test_compiler_can_expose_a_versioned_tool_subset() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )

    compiled = compiler.compile(
        initialized=initialized_turn(agent_manifest),
        current_time=datetime.now(UTC),
        allowed_tool_names=("inspect_image",),
    )

    assert compiled.allowed_tool_names == ("inspect_image",)
    assert [tool.name for tool in compiled.request.tools] == ["inspect_image"]


def test_compiler_places_authoritative_facts_before_untrusted_input() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )

    compiled = compiler.compile(
        initialized=initialized_turn(agent_manifest),
        current_time=datetime.now(UTC),
        authoritative_context={
            "profile": {"nickname": "小明"},
            "recent_weights": [{"weight_kg": "77.6"}],
        },
    )

    messages = compiled.request.messages
    assert [message.role for message in messages] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.USER,
    ]
    assert "权威用户事实" in (messages[2].content or "")
    assert json.loads((messages[2].content or "").split("：", maxsplit=1)[1]) == {
        "profile": {"nickname": "小明"},
        "recent_weights": [{"weight_kg": "77.6"}],
    }


def test_compiler_only_authorizes_user_evidence_refs_from_working_memory() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )

    compiled = compiler.compile(
        initialized=initialized_turn(agent_manifest),
        current_time=datetime.now(UTC),
        authoritative_context={
            "working_memory": {
                "recent_dialogue": [
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": "我身高179",
                                "evidence_ref": "item-user-height",
                            },
                            {
                                "role": "assistant",
                                "content": "你的身高是179cm",
                                "evidence_ref": "item-assistant-height",
                            },
                        ]
                    }
                ]
            }
        },
    )

    assert compiled.evidence_item_ids == ("item-user-height",)


def test_scheduled_turn_needs_no_fake_user_message() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )

    compiled = compiler.compile(
        initialized=initialized_turn(
            agent_manifest,
            items=(),
            trigger=TurnTrigger.DAILY_REVIEW,
        ),
        current_time=datetime.now(UTC),
        allowed_tool_names=(),
    )

    assert [message.role for message in compiled.request.messages] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
    ]
    assert '"trigger":"daily_review"' in (
        compiled.request.messages[1].content or ""
    )
    assert compiled.request.tool_choice is ToolChoice.NONE


def test_compiler_rejects_prompt_and_tool_version_drift() -> None:
    agent_manifest = manifest()

    with pytest.raises(ContextCompilationError, match="System prompt content"):
        ContextCompiler(
            manifest=agent_manifest,
            system_prompt="Changed prompt",
            tools=registry(),
        )

    with pytest.raises(ContextCompilationError, match="Tool version mismatch"):
        ContextCompiler(
            manifest=agent_manifest,
            system_prompt=SYSTEM_PROMPT,
            tools=registry(record_weight_version="v9"),
        )


def test_compiler_rejects_turn_version_drift_and_malformed_items() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )
    different_manifest = manifest(code_revision="different")

    with pytest.raises(ContextCompilationError, match="Turn Agent version"):
        compiler.compile(
            initialized=initialized_turn(different_manifest),
            current_time=datetime.now(UTC),
        )

    malformed = ItemRef(
        id="bad-image",
        turn_id="turn-1",
        sequence=1,
        item_type=ItemType.IMAGE_ATTACHMENT,
        status=ItemStatus.COMPLETED,
        payload={"asset_id": "asset-1", "mime_type": "text/plain"},
    )
    with pytest.raises(ContextCompilationError, match="invalid MIME type"):
        compiler.compile(
            initialized=initialized_turn(agent_manifest, items=(malformed,)),
            current_time=datetime.now(UTC),
        )


def test_compiler_rejects_input_from_another_turn() -> None:
    agent_manifest = manifest()
    compiler = ContextCompiler(
        manifest=agent_manifest,
        system_prompt=SYSTEM_PROMPT,
        tools=registry(),
    )
    foreign_item = ItemRef(
        id="foreign-item",
        turn_id="another-turn",
        sequence=1,
        item_type=ItemType.USER_MESSAGE,
        status=ItemStatus.COMPLETED,
        payload={"text": "foreign input"},
    )

    with pytest.raises(ContextCompilationError, match="another Turn"):
        compiler.compile(
            initialized=initialized_turn(agent_manifest, items=(foreign_item,)),
            current_time=datetime.now(UTC),
        )
