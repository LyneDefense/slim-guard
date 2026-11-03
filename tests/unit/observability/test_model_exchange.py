import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
)
from slim_guard.db.models import SlimGuardUser, TraceSpanRecord
from slim_guard.db.session import Database
from slim_guard.memory.lifecycle import MemoryLifecycleRepository
from slim_guard.observability.model_exchange import ModelExchangeRecorder, bounded
from slim_guard.observability.tracing import InteractionTraceRepository, bind_trace
from slim_guard.usage.gateway import MeasuredModelGateway
from slim_guard.usage.repository import UsageRepository


class Model:
    async def complete(self, request):
        return ModelResponse(message=ModelMessage(role=MessageRole.ASSISTANT, content="你好"))

    async def close(self):
        pass


@pytest.fixture
async def storage(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'exchanges.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add(
            SlimGuardUser(
                id="user", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)
            )
        )
    traces = InteractionTraceRepository(db)
    trace_id = await traces.start_user_trace(
        user_id="user", trigger_type="user_message", channel_id="mobile", inbound_msgid="request"
    )
    yield db, traces, trace_id
    await db.close()


def request():
    return ModelRequest(
        purpose=ModelPurpose.COACH_RESPONSE,
        model="test",
        messages=(ModelMessage(role=MessageRole.USER, content="你好"),),
        metadata={"api_key": "never-store-this"},
    )


async def test_exact_attempt_snapshot_excludes_provider_secrets(storage):
    db, traces, trace_id = storage
    gateway = MeasuredModelGateway(
        Model(), UsageRepository(db), exchanges=ModelExchangeRecorder(traces)
    )
    with bind_trace(trace_id):
        response = await gateway.complete(request())
    assert response.message.content == "你好"
    async with db.session() as session:
        row = await session.scalar(
            select(TraceSpanRecord).where(TraceSpanRecord.operation == "model_exchange")
        )
    data = json.loads(row.attributes_json)
    assert data["input"]["conversation"][0]["content"] == "你好"
    assert data["output"]["content"] == "你好"
    assert data["call_id"]
    assert "never-store-this" not in row.attributes_json
    assert "reasoning" not in row.attributes_json


async def test_unbound_offline_calls_do_not_create_user_trace(storage):
    db, traces, _ = storage
    gateway = MeasuredModelGateway(
        Model(), UsageRepository(db), exchanges=ModelExchangeRecorder(traces)
    )
    await gateway.complete(request())
    async with db.session() as session:
        assert await session.scalar(select(TraceSpanRecord)) is None


async def test_snapshot_observer_failure_does_not_change_model_result():
    class BrokenTrace:
        async def start_span(self, **kwargs):
            raise RuntimeError("database unavailable")

    with bind_trace("trace"):
        async with ModelExchangeRecorder(BrokenTrace()).observe("call", request()) as saved:
            saved["response"] = await Model().complete(request())
    assert saved["response"].message.content == "你好"


async def test_provider_error_is_retained_and_not_swallowed(storage):
    _, traces, trace_id = storage
    with bind_trace(trace_id), pytest.raises(RuntimeError, match="provider failed"):
        async with ModelExchangeRecorder(traces).observe("failed-call", request()):
            raise RuntimeError("provider failed")
    db = storage[0]
    async with db.session() as session:
        row = await session.scalar(
            select(TraceSpanRecord).where(TraceSpanRecord.operation == "model_exchange")
        )
    assert row.status == "failed"
    assert row.error_code == "RuntimeError"
    assert "provider failed" not in row.attributes_json


@pytest.mark.parametrize("completed", [True, False])
async def test_retention_scrubs_snapshot_bodies_preserving_correlation(storage, completed):
    db, traces, trace_id = storage
    with bind_trace(trace_id):
        async with ModelExchangeRecorder(traces).observe("retain-id", request()) as saved:
            saved["response"] = await Model().complete(request())
    now = datetime.now(UTC)
    async with db.session() as session, session.begin():
        await session.execute(
            update(TraceSpanRecord).values(
                started_at=now - timedelta(days=40),
                completed_at=now - timedelta(days=40) if completed else None,
            )
        )
    lifecycle = MemoryLifecycleRepository(db)
    result = await lifecycle.scrub_transcript_bodies(
        before=now - timedelta(days=30), redacted_at=now
    )
    assert result.model_exchange_count == 1
    async with db.session() as session:
        row = await session.scalar(
            select(TraceSpanRecord).where(TraceSpanRecord.operation == "model_exchange")
        )
    data = json.loads(row.attributes_json)
    assert data["call_id"] == "retain-id"
    assert data["body_redacted"]
    assert "input" not in data and "output" not in data
    again = await lifecycle.scrub_transcript_bodies(
        before=now - timedelta(days=30), redacted_at=now
    )
    assert again.model_exchange_count == 0


def test_snapshot_size_is_bounded_and_truncation_is_explicit():
    budget = [3]
    result = bounded({"text": "abcdef"}, budget)
    assert result == {"text": "abc…[truncated]"}
    assert budget[0] < 0
