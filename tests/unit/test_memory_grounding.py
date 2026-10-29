"""Constraint grounding: normalization is semantic, source isolation is mechanical."""

from datetime import UTC, datetime

import pytest

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.db.models import SlimGuardUser
from slim_guard.db.session import Database
from slim_guard.harness.events import TurnTrigger
from slim_guard.harness.initialization import TurnInitializationRequest, TurnInitializer, TurnInput
from slim_guard.harness.manifest import AgentManifest
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.memory.contracts import MemoryFactInput, MemoryKey, MemoryWriteCommand
from slim_guard.memory.errors import MemoryEvidenceMismatch, MemorySourceMismatch
from slim_guard.memory.grounding import (
    ConstraintMemoryWriter,
    GroundingDecision,
    GroundingRejected,
    ModelGroundingReviewer,
)
from slim_guard.memory.repository import MemoryRepository
from slim_guard.tools.contracts import ToolContext, ToolExecutionMode
from slim_guard.tools.memory import MemoryToolHandlers, RecordUserConstraintArguments

QUOTE = "我不能吃太辣的"
NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.fixture
async def env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'grounding.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add_all(
            [SlimGuardUser(id=id, first_seen_at=NOW, last_seen_at=NOW) for id in ("u1", "u2")]
        )
    manifest = AgentManifest.build(
        model_provider="test",
        text_model="test",
        vision_model="test",
        model_parameters={},
        system_prompt_version="test",
        system_prompt="test",
        context_policy_version="test",
        memory_policy_version="test",
        compaction_policy_version="test",
        safety_policy_version="test",
        code_revision="test",
    )
    await AgentVersionRepository(db).register(manifest)
    initialized = await TurnInitializer(HarnessStateRepository(db)).initialize(
        TurnInitializationRequest(
            user_id="u1",
            agent_version_id=manifest.version_id,
            trigger=TurnTrigger.USER_MESSAGE,
            execution_mode=ToolExecutionMode.EVALUATION,
            inputs=(TurnInput.user_message(text=f"我观察了几天，{QUOTE}，以及我对西兰花有点过敏"),),
        )
    )
    context = ToolContext(
        user_id="u1",
        thread_id=initialized.thread.id,
        turn_id=initialized.turn.id,
        agent_version_id=manifest.version_id,
        source_item_id=initialized.source_item_id,
        tool_call_id="constraint1",
        execution_idempotency_key="constraint1",
        execution_mode=ToolExecutionMode.EVALUATION,
    )
    try:
        yield MemoryRepository(db), context
    finally:
        await db.close()


def command(context, *, subject="辛辣食物", statement=QUOTE, quote=QUOTE, operation="write1"):
    return MemoryWriteCommand(
        user_id=context.user_id,
        facts=(
            MemoryFactInput(
                key=MemoryKey.DIETARY_CONSTRAINT, value={"subject": subject, "statement": statement}
            ),
        ),
        evidence_excerpt=quote,
        operation_id=operation,
        source_turn_id=context.turn_id,
        source_item_id=context.source_item_id,
        source_tool_call_id=context.tool_call_id,
    )


def decision(**overrides):
    return GroundingDecision.model_validate(
        {
            "verdict": "supported",
            "topic_relation": "new",
            "matched_memory_id": None,
            "subject": "辛辣食物",
            "reason_code": "faithful_topic",
            **overrides,
        }
    )


class Reviewer:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def review(self, command, existing):
        self.calls.append((command, existing))
        return self.result(existing) if callable(self.result) else self.result


async def test_normalized_topic_does_not_need_to_be_a_literal_substring(env):
    repo, context = env
    reviewer = Reviewer(decision())
    result = await ConstraintMemoryWriter(repo, reviewer).write(command(context))
    assert result.saved.facts[0].value == {"subject": "辛辣食物", "statement": QUOTE}
    assert result.saved.facts[0].evidence_item_id == context.source_item_id
    assert result.review["mode"] == "normalized_topic"
    assert len(reviewer.calls) == 1


async def test_exact_user_fact_has_no_extra_model_call(env):
    repo, context = env
    reviewer = Reviewer(decision())
    result = await ConstraintMemoryWriter(repo, reviewer).write(command(context, subject="辣"))
    assert result.saved.created_count == 1
    assert not reviewer.calls


async def test_constraint_writer_rejects_multiple_facts_instead_of_silently_dropping_them(env):
    repo, context = env
    single = command(context)
    writer = ConstraintMemoryWriter(repo, Reviewer(decision()))
    with pytest.raises(GroundingRejected, match="exactly one"):
        await writer.write(single.model_copy(update={"facts": single.facts * 2}))
    assert not await repo.active("u1")


@pytest.mark.parametrize("statement", ["用户对辣椒过敏", "用户完全不能吃辣"])
async def test_unsupported_strengthening_is_rejected_with_specific_field_feedback(env, statement):
    repo, context = env
    writer = ConstraintMemoryWriter(repo, Reviewer(decision(verdict="contradicted")))
    handlers = MemoryToolHandlers(repo, writer)
    result = await handlers.record_user_constraint(
        context,
        RecordUserConstraintArguments(
            category="dietary", subject="辛辣食物", statement=statement, evidence_excerpt=QUOTE
        ),
    )
    assert result.failure.code == "memory_claim_not_supported"
    assert result.failure.field_errors[0].path == ("statement",)
    assert result.failure.effect_status.value == "not_applied"
    assert result.failure.retryable is False
    assert not await repo.active("u1")


async def test_uncertainty_retains_only_the_exact_quote_without_overwriting_an_existing_topic(env):
    repo, context = env
    original = await repo.write(command(context, subject="辣", operation="original"))
    reviewer = Reviewer(decision(verdict="uncertain", topic_relation="uncertain"))
    result = await ConstraintMemoryWriter(repo, reviewer).write(
        command(context, statement="不能吃辣")
    )
    assert result.review["mode"] == "quote_only"
    assert result.saved.facts[0].value == {"subject": QUOTE, "statement": QUOTE}
    assert original.facts[0].id in {item.id for item in await repo.active("u1")}


async def test_semantically_same_topic_reuses_existing_slot_instead_of_creating_synonym_duplicates(
    env,
):
    repo, context = env
    original = await repo.write(command(context, subject="辣", operation="original"))
    reviewer = Reviewer(
        lambda existing: decision(topic_relation="same", matched_memory_id=existing[0].id)
    )
    result = await ConstraintMemoryWriter(repo, reviewer).write(command(context))
    assert result.review["mode"] == "existing_topic"
    assert result.saved.created_count == 0
    assert result.saved.facts[0].id == original.facts[0].id
    assert len(await repo.active("u1")) == 1


async def test_changed_fact_on_same_topic_uses_existing_subject_and_versioned_update(env):
    repo, context = env
    original = await repo.write(
        command(context, subject="辣", statement="不能吃太辣的", operation="old")
    )
    reviewer = Reviewer(
        lambda existing: decision(topic_relation="same", matched_memory_id=existing[0].id)
    )
    result = await ConstraintMemoryWriter(repo, reviewer).write(command(context))
    assert result.saved.changes[0].action.value == "updated"
    assert result.saved.facts[0].supersedes_id == original.facts[0].id
    assert result.saved.facts[0].value["subject"] == "辣"
    assert len(await repo.active("u1")) == 1


@pytest.mark.parametrize("fault", ["quote", "owner"])
async def test_forged_or_foreign_sources_are_rejected_before_sending_anything_to_a_model(
    env, fault
):
    repo, context = env
    reviewer = Reviewer(decision())
    proposal = command(context)
    if fault == "quote":
        proposal = proposal.model_copy(update={"evidence_excerpt": "没有说过的话"})
    else:
        proposal = proposal.model_copy(update={"user_id": "u2"})
    with pytest.raises((MemorySourceMismatch, MemoryEvidenceMismatch)):
        await ConstraintMemoryWriter(repo, reviewer).write(proposal)
    assert not reviewer.calls


async def test_reviewer_cannot_select_an_unavailable_or_other_users_topic(env):
    repo, context = env
    reviewer = Reviewer(decision(topic_relation="same", matched_memory_id="foreign-memory"))
    with pytest.raises(GroundingRejected) as caught:
        await ConstraintMemoryWriter(repo, reviewer).write(command(context))
    assert caught.value.code == "memory_topic_not_visible"
    assert not await repo.active("u1")


async def test_unavailable_reviewer_never_silently_accepts_a_normalized_fact(env):
    repo, context = env
    with pytest.raises(GroundingRejected) as caught:
        await ConstraintMemoryWriter(repo).write(command(context))
    assert caught.value.code == "memory_grounding_unavailable"
    assert not await repo.active("u1")


async def test_tool_result_carries_grounding_audit_and_keeps_one_successful_write(env):
    repo, context = env
    handlers = MemoryToolHandlers(repo, ConstraintMemoryWriter(repo, Reviewer(decision())))
    arguments = RecordUserConstraintArguments(
        category="dietary", subject="太辣的食物", statement=QUOTE, evidence_excerpt=QUOTE
    )
    first = await handlers.record_user_constraint(context, arguments)
    assert first.failure is None
    assert first.output["grounding"]["mode"] == "normalized_topic"
    assert len(await repo.active("u1")) == 1


async def test_model_review_uses_an_explicit_schema_and_only_returns_an_auditable_decision(env):
    repo, context = env
    model = ScriptedModelGateway(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=decision().model_dump_json())
            )
        ]
    )
    result = await ConstraintMemoryWriter(repo, ModelGroundingReviewer(model, "test")).write(
        command(context)
    )
    assert result.saved.created_count == 1
    assert '"output_schema"' in model.requests[0].messages[-1].content
    assert model.requests[0].metadata["turn_id"] == context.turn_id
    assert model.requests[0].tool_choice.value == "none"
    assert model.requests[0].response_format.value == "json_object"
