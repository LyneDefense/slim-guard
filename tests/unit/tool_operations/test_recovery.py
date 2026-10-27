from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import select

from slim_guard.agent_models.gateway import NormalizedToolCall
from slim_guard.db.models import ToolExecutionRecord
from slim_guard.features.health_records.body_fat.repository import BodyFatRepository
from slim_guard.features.health_records.body_fat.tools import (
    body_fat_tool_definitions,
    body_fat_tool_executors,
)
from slim_guard.features.health_records.exercise.repository import ExerciseRepository
from slim_guard.features.health_records.exercise.tools import (
    exercise_tool_definitions,
    exercise_tool_executors,
)
from slim_guard.features.health_records.meal.repository import MealRepository
from slim_guard.features.health_records.meal.tools import meal_tool_definitions, meal_tool_executors
from slim_guard.features.health_records.weight.repository import WeightRepository
from slim_guard.features.health_records.weight.tools import (
    weight_tool_definitions,
    weight_tool_executors,
)
from slim_guard.harness.state_repository import HarnessStateRepository
from slim_guard.tool_operations.gateway import OperationGateway
from slim_guard.tool_operations.models import ToolOperationRecord
from slim_guard.tool_operations.repository import ToolOperationRepository
from slim_guard.tool_operations.tools import (
    GET_OPERATION,
    LIST_OPERATIONS,
    RESUME_OPERATION,
    operation_tool_definitions,
)
from slim_guard.tools.contracts import ToolContext, ToolEffectStatus, ToolExecutionMode, ToolResult
from slim_guard.tools.gateway import ToolExecutor
from slim_guard.tools.policy import DefaultToolPolicy, ToolAuthorization
from slim_guard.tools.registry import ToolRegistry
from tests.unit.test_tool_execution_repository import prepare_ledger
from tests.unit.test_tool_gateway import WeightArguments, registered_tool


@pytest.fixture
async def setup(tmp_path):
    db, executions, turn_id = await prepare_ledger(tmp_path)
    turn = await HarnessStateRepository(db).get_turn(turn_id)
    context = ToolContext(
        user_id="user-1",
        thread_id=turn.thread_id,
        turn_id=turn_id,
        tool_call_id="original",
        agent_version_id=turn.agent_version_id,
        execution_mode=ToolExecutionMode.EVALUATION,
    )
    store = ToolOperationRepository(db)
    try:
        yield db, executions, store, context
    finally:
        await db.close()


def compose(setup, handler, *, tool=None, lookup=None, execution_store=None):
    _, executions, store, context = setup
    tool = tool or registered_tool()
    registry = ToolRegistry((tool, *operation_tool_definitions()))
    gateway = OperationGateway(
        registry=registry,
        executors={
            tool.name: ToolExecutor(
                arguments_model=WeightArguments,
                handler=handler,
                lookup_result=lookup,
            )
        },
        execution_store=execution_store or executions,
        operation_store=store,
        policy=DefaultToolPolicy(),
    )
    auth = ToolAuthorization(
        allowed_tool_names=frozenset(registry.names), isolated_write_environment=True
    )

    async def call(call_id, name=tool.name, args=None, *, as_context=None, grants=None):
        ctx = (as_context or context).model_copy(update={"tool_call_id": call_id})
        return await gateway.execute(
            call=NormalizedToolCall(
                id=call_id, name=name, arguments=({"weight_kg": 77.6} if args is None else args)
            ),
            context=ctx,
            authorization=grants or auth,
        )

    return call, auth


async def test_replayed_attempt_recovers_business_receipt_if_attempt_receipt_write_fails(setup):
    class FailingCompletionStore:
        def __init__(self, delegate):
            self.delegate = delegate

        async def claim(self, **kwargs):
            return await self.delegate.claim(**kwargs)

        async def complete(self, **kwargs):
            raise RuntimeError("attempt ledger unavailable")

        async def get(self, idempotency_key):
            return await self.delegate.get(idempotency_key)

    count = 0

    async def handler(context, arguments):
        nonlocal count
        count += 1
        return ToolResult.success(output={"recorded": True})

    call, _ = compose(
        setup,
        handler,
        execution_store=FailingCompletionStore(setup[1]),
    )

    first = await call("same-call")
    replay = await call("same-call")

    assert first.result.status.value == "succeeded"
    assert replay.result.status.value == "succeeded"
    assert replay.result.operation is not None
    assert replay.result.operation.operation_id == first.result.operation.operation_id
    assert count == 1


async def test_validation_can_be_repaired_without_creating_an_operation(setup):
    calls = []

    async def handler(context, arguments):
        calls.append(context)
        return ToolResult.success(output={"recorded": True})

    call, _ = compose(setup, handler)
    bad = await call("bad", args={"weight_kg": "not a number"})
    assert bad.result.operation is None
    assert bad.result.failure.effect_status is ToolEffectStatus.NOT_APPLIED
    assert bad.result.failure.field_errors[0].path == ("weight_kg",)
    async with setup[0].session() as session:
        assert list(await session.scalars(select(ToolOperationRecord))) == []
    good = await call("fixed")
    assert good.result.operation.status == "succeeded"
    assert len(calls) == 1
    assert calls[0].execution_idempotency_key == good.result.operation.operation_id
    assert good.idempotency_key != calls[0].execution_idempotency_key


async def test_resume_replays_success_and_records_separate_attempt(setup):
    calls = []

    async def handler(context, arguments):
        calls.append((context, arguments))
        return ToolResult.success(output={"record_id": "meal-1"})

    call, _ = compose(setup, handler)
    first = await call("first")
    op = first.result.operation.operation_id
    second = await call("resume", RESUME_OPERATION, {"operation_id": op})
    assert first.result == second.result
    assert second.tool_call_id == "resume"
    assert first.idempotency_key != second.idempotency_key
    assert len(calls) == 1
    async with setup[0].session() as session:
        assert len(list(await session.scalars(select(ToolExecutionRecord)))) == 2


async def test_retry_requires_positive_no_effect_proof_and_frozen_parameters(setup):
    contexts = []

    async def handler(context, arguments):
        contexts.append(context)
        if len(contexts) == 1:
            return ToolResult.failed(
                code="temporarily_unavailable",
                message="Not started.",
                retryable=True,
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        return ToolResult.success(output={"weight_kg": arguments.weight_kg})

    call, _ = compose(setup, handler)
    first = await call("first")
    op = first.result.operation.operation_id
    edited = await call("tamper", RESUME_OPERATION, {"operation_id": op, "weight_kg": 50})
    assert edited.result.failure.code == "invalid_arguments"
    retried = await call("resume", RESUME_OPERATION, {"operation_id": op})
    assert retried.result.output["weight_kg"] == 77.6
    assert contexts[0] == contexts[1]
    assert contexts[1].tool_call_id == "first"
    saved = await setup[2].get(op, context=setup[3])
    assert saved.attempt == 2


@pytest.mark.parametrize(
    "failure",
    [
        ToolResult.failed(code="timeout", message="Unknown.", retryable=True),
        ToolResult.failed(code="failed", message="Unknown.", retryable=False),
    ],
)
async def test_unknown_write_cannot_be_repeated_with_changed_call_or_args(setup, failure):
    count = 0

    async def handler(context, arguments):
        nonlocal count
        count += 1
        return failure

    call, _ = compose(setup, handler)
    first = await call("first")
    op = first.result.operation.operation_id
    second = await call("resume", RESUME_OPERATION, {"operation_id": op})
    assert second.result.failure.code == "operation_result_uncertain"
    new = await call("new", args={"weight_kg": 70.0})
    assert new.result.failure.code == "unsettled_write_exists"
    assert new.result.operation.operation_id == op
    assert count == 1


async def test_concurrent_resumes_have_one_executor(setup):
    count = 0

    async def handler(context, arguments):
        nonlocal count
        count += 1
        if count == 1:
            return ToolResult.failed(
                code="unavailable",
                message="Not executed.",
                retryable=True,
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        await asyncio.sleep(0.01)
        return ToolResult.success(output={"recorded": True})

    call, _ = compose(setup, handler)
    first = await call("first")
    args = {"operation_id": first.result.operation.operation_id}
    await asyncio.gather(call("r1", RESUME_OPERATION, args), call("r2", RESUME_OPERATION, args))
    assert count == 2


@pytest.mark.parametrize(
    "change",
    [{"user_id": "other"}, {"thread_id": "other"}, {"execution_mode": ToolExecutionMode.LIVE}],
)
async def test_operation_reference_does_not_grant_access(setup, change):
    async def handler(context, arguments):
        return ToolResult.success(output={"private": True})

    call, _ = compose(setup, handler)
    first = await call("first")
    other = await call(
        "intruder",
        GET_OPERATION,
        {"operation_id": first.result.operation.operation_id},
        as_context=setup[3].model_copy(update=change),
    )
    assert other.result.failure.code == "operation_not_available"
    assert other.result.operation is None


async def test_current_capability_grant_is_checked_on_resume(setup):
    count = 0

    async def handler(context, arguments):
        nonlocal count
        count += 1
        if count == 1:
            return ToolResult.failed(
                code="temporary_failure",
                message="The write was not applied.",
                retryable=True,
                effect_status=ToolEffectStatus.NOT_APPLIED,
            )
        return ToolResult.success(output={"private": True})

    call, auth = compose(setup, handler)
    first = await call("first")
    args = {"operation_id": first.result.operation.operation_id}
    denied = await call(
        "r1",
        RESUME_OPERATION,
        args,
        grants=auth.model_copy(update={"allowed_tool_names": frozenset({RESUME_OPERATION})}),
    )
    assert denied.result.failure.code == "tool_not_authorized"
    resumed = await call("r2", RESUME_OPERATION, args)
    assert resumed.result.status.value == "succeeded"
    revoked = await call(
        "r3",
        RESUME_OPERATION,
        args,
        grants=auth.model_copy(update={"allowed_tool_names": frozenset({RESUME_OPERATION})}),
    )
    assert revoked.result.failure.code == "tool_not_authorized"
    assert count == 2


async def test_cancellation_keeps_claim_and_can_be_found_after_restart(setup):
    started = asyncio.Event()

    async def handler(context, arguments):
        started.set()
        await asyncio.Event().wait()

    call, _ = compose(setup, handler)
    task = asyncio.create_task(call("first"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    restarted, _ = compose(setup, handler)
    listing = await restarted("list", LIST_OPERATIONS, {})
    op = listing.result.output["operations"][0]
    assert op["status"] == "running"
    result = await restarted("r", RESUME_OPERATION, {"operation_id": op["operation_id"]})
    assert result.result.failure.code == "operation_result_uncertain"


@pytest.mark.parametrize(
    "definitions,executors,repository,name,args",
    [
        (
            meal_tool_definitions,
            meal_tool_executors,
            MealRepository,
            "record_meal",
            {"meal_type": "lunch", "foods": [{"name": "烧茄子"}]},
        ),
        (
            weight_tool_definitions,
            weight_tool_executors,
            WeightRepository,
            "record_weight",
            {"value": 78.0, "unit": "kg"},
        ),
        (
            body_fat_tool_definitions,
            body_fat_tool_executors,
            BodyFatRepository,
            "record_body_fat",
            {"value": 20.0},
        ),
        (
            exercise_tool_definitions,
            exercise_tool_executors,
            ExerciseRepository,
            "record_exercise",
            {"activity_name": "walking", "duration_minutes": 30},
        ),
    ],
)
async def test_committed_local_write_with_lost_response_is_reconciled_not_reinserted(
    setup,
    definitions,
    executors,
    repository,
    name,
    args,
):
    db, attempts, store, context = setup
    registry = ToolRegistry((*definitions(), *operation_tool_definitions()))
    original = dict(executors(repository(db)))
    executor = original[name]
    count = 0

    async def lost_response(context, arguments):
        nonlocal count
        count += 1
        await executor.invoke(context, arguments)
        raise TimeoutError("connection lost after commit")

    original[name] = replace(executor, handler=lost_response)
    gateway = OperationGateway(
        registry=registry,
        executors=original,
        execution_store=attempts,
        operation_store=store,
        policy=DefaultToolPolicy(),
    )
    auth = ToolAuthorization(
        allowed_tool_names=frozenset(registry.names), isolated_write_environment=True
    )
    first = await gateway.execute(
        call=NormalizedToolCall(id="original", name=name, arguments=args),
        context=context,
        authorization=auth,
    )
    assert first.result.operation.status == "unknown"
    op = first.result.operation.operation_id
    resumed = await gateway.execute(
        call=NormalizedToolCall(id="r", name=RESUME_OPERATION, arguments={"operation_id": op}),
        context=context.model_copy(update={"tool_call_id": "r"}),
        authorization=auth,
    )
    assert resumed.result.operation.status == "succeeded"
    assert resumed.result.output["created"] is False
    assert count == 1
    row = await repository(db).by_operation("user-1", op)
    assert row is not None
    assert resumed.result.source_ids == (row.id,)
    assert await repository(db).by_operation("other-user", op) is None


async def test_lookup_absence_is_not_evidence_of_rollback(setup):
    count = 0

    async def handler(context, arguments):
        nonlocal count
        count += 1
        raise TimeoutError()

    async def lookup(context):
        return None

    call, _ = compose(setup, handler, lookup=lookup)
    first = await call("first")
    args = {"operation_id": first.result.operation.operation_id}
    got = await call("get", GET_OPERATION, args)
    assert got.result.output["operation"]["status"] == "unknown"
    resumed = await call("r", RESUME_OPERATION, args)
    assert resumed.result.failure.code == "operation_result_uncertain"
    assert count == 1


async def test_actual_timeout_persists_unknown_operation_reference(setup):
    async def handler(context, arguments):
        await asyncio.Event().wait()

    call, _ = compose(setup, handler, tool=registered_tool(timeout_seconds=0.001))
    result = await call("first")
    assert result.result.failure.code == "tool_timeout"
    assert result.result.operation.status == "unknown"
    saved = await setup[2].get(result.result.operation.operation_id, context=setup[3])
    assert saved.status == "unknown"


async def test_retention_removes_snapshot_and_does_not_enable_retry(setup):
    from datetime import UTC, datetime, timedelta

    from slim_guard.harness.events import TurnStatus
    from slim_guard.memory.lifecycle import MemoryLifecycleRepository

    async def handler(context, arguments):
        raise TimeoutError()

    call, _ = compose(setup, handler)
    first = await call("first")
    op = first.result.operation.operation_id
    await HarnessStateRepository(setup[0]).transition_turn(
        turn_id=setup[3].turn_id,
        target=TurnStatus.FAILED,
        expected=TurnStatus.RUNNING,
    )
    now = datetime.now(UTC) + timedelta(days=1)
    scrub = await MemoryLifecycleRepository(setup[0]).scrub_transcript_bodies(
        before=now,
        redacted_at=now,
    )
    assert scrub.tool_operation_count == 1
    saved = await setup[2].get(op, context=setup[3])
    assert saved.arguments.get("_redacted") is True
    retry = await call("r", RESUME_OPERATION, {"operation_id": op})
    assert retry.result.failure.code == "operation_payload_expired"
    new = await call("new")
    assert new.result.failure.code == "unsettled_write_exists"


async def test_domain_validation_rejection_can_be_corrected(setup):
    db, attempts, store, context = setup
    registry = ToolRegistry((*meal_tool_definitions(), *operation_tool_definitions()))
    gateway = OperationGateway(
        registry=registry,
        executors=meal_tool_executors(MealRepository(db)),
        execution_store=attempts,
        operation_store=store,
        policy=DefaultToolPolicy(),
    )
    auth = ToolAuthorization(
        allowed_tool_names=frozenset(registry.names), isolated_write_environment=True
    )
    invalid = await gateway.execute(
        call=NormalizedToolCall(
            id="original",
            name="record_meal",
            arguments={
                "foods": [{"name": "烧茄子"}],
                "occurred_at": "not a timestamp",
            },
        ),
        context=context,
        authorization=auth,
    )
    assert invalid.result.operation.status == "not_applied"
    valid = await gateway.execute(
        call=NormalizedToolCall(
            id="fixed",
            name="record_meal",
            arguments={
                "foods": [{"name": "烧茄子"}],
                "occurred_at": "2026-09-23T12:00:00+08:00",
            },
        ),
        context=context.model_copy(update={"tool_call_id": "fixed"}),
        authorization=auth,
    )
    assert valid.result.operation.status == "succeeded"
    assert len(await MealRepository(db).recent("user-1")) == 1


async def test_new_operation_is_not_silently_matched_to_previous_success(setup):
    calls = []

    async def handler(context, arguments):
        calls.append(context.execution_idempotency_key)
        return ToolResult.success(output={"recorded": True})

    call, _ = compose(setup, handler)
    first = await call("first")
    new = await call("new")
    assert first.result.operation.operation_id != new.result.operation.operation_id
    assert len(calls) == 2  # Generic operation identity is not domain duplicate detection.
