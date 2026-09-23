import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from slim_guard.agent_models.embeddings import EmbeddingError, ZhipuEmbeddingGateway
from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelRequest, ModelResponse, ModelUsage
from slim_guard.db.models import (
    AgentInvocationRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.usage.context import CURRENT, usage_operation, usage_scope
from slim_guard.usage.gateway import MeasuredModelGateway, capture_provider_usage
from slim_guard.usage.models import ModelUsageRecord
from slim_guard.usage.repository import UsageRepository


class Store:
    def __init__(self):
        self.rows = {}

    async def start(self, values):
        self.rows[values["id"]] = {**values}

    async def finish(self, call_id, values):
        self.rows[call_id].update(values)


def test_usage_results_do_not_reintroduce_cumulative_limits():
    from slim_guard.runtime.contracts import AgentResult

    result = AgentResult(
        invocation_id="i",
        status="succeeded",
        artifact_id="artifact-test",
        output_schema="test",
        output_schema_version="1",
        tool_receipt_ids=tuple(f"receipt-{i}" for i in range(100)),
        model_call_count=100,
        tool_call_count=100,
        token_usage=20_000_000,
    )
    assert len(result.tool_receipt_ids) == 100 and result.token_usage == 20_000_000


def request():
    return ModelRequest(
        purpose="harness_turn", model="test", messages=(ModelMessage(role="user", content="hi"),)
    )


def response(usage=None):
    return ModelResponse(
        message=ModelMessage(role="assistant", content="ok"), usage=usage or ModelUsage()
    )


@pytest.mark.parametrize(
    "usage,expected",
    [
        (ModelUsage(), None),
        (ModelUsage(total_tokens=0), 0),
        (ModelUsage(input_tokens=3, output_tokens=2), 5),
    ],
)
async def test_usage_distinguishes_unknown_and_zero(usage, expected):
    store = Store()
    gateway = MeasuredModelGateway(ScriptedModelGateway([response(usage)]), store)
    with usage_scope(user_id="u", turn_id="t", agent_role="core", invocation_id="i"):
        await gateway.complete(request())
    (row,) = store.rows.values()
    assert row.get("total_tokens") == expected
    assert row["user_id"] == "u" and row["invocation_id"] == "i"
    assert row["status"] == "succeeded"
    assert CURRENT.get().user_id is None


async def test_invalid_provider_output_and_retry_both_count():
    class Gateway:
        attempts = 0

        async def complete(self, req):
            self.attempts += 1
            capture_provider_usage(
                {"id": str(self.attempts), "usage": {"prompt_tokens": 6, "completion_tokens": 4}}
            )
            if self.attempts == 1:
                raise ValueError("invalid structured content")
            return response()

    store = Store()
    gateway = MeasuredModelGateway(Gateway(), store)
    with pytest.raises(ValueError):
        await gateway.complete(request())
    await gateway.complete(request())
    assert len(store.rows) == 2
    assert sum(row["total_tokens"] for row in store.rows.values()) == 20
    assert [row["status"] for row in store.rows.values()] == ["failed", "succeeded"]


@pytest.mark.parametrize("raw_usage", [None, {}, {"prompt_tokens": 5}])
async def test_raw_partial_usage_never_becomes_normalized_zero(raw_usage):
    class Gateway:
        async def complete(self, req):
            capture_provider_usage({"usage": raw_usage})
            return response(ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0))

    store = Store()
    await MeasuredModelGateway(Gateway(), store).complete(request())
    (row,) = store.rows.values()
    assert row.get("total_tokens") is None
    assert row.get("output_tokens") is None


async def test_cancellation_is_recorded_without_inventing_tokens():
    class Gateway:
        async def complete(self, req):
            raise asyncio.CancelledError()

    store = Store()
    with pytest.raises(asyncio.CancelledError):
        await MeasuredModelGateway(Gateway(), store).complete(request())
    (row,) = store.rows.values()
    assert row["status"] == "cancelled" and "total_tokens" not in row


async def test_broken_accounting_does_not_reject_model_result():
    class Broken(Store):
        async def start(self, values):
            raise RuntimeError("db offline")

        async def finish(self, call_id, values):
            raise RuntimeError("db offline")

    result = await MeasuredModelGateway(ScriptedModelGateway([response()]), Broken()).complete(
        request()
    )
    assert result.message.content == "ok"


async def test_concurrent_user_and_agent_scopes_do_not_leak():
    class Gateway:
        async def complete(self, req):
            await asyncio.sleep(0)
            capture_provider_usage({"usage": {"total_tokens": 2}})
            return response()

    store = Store()
    gateway = MeasuredModelGateway(Gateway(), store)

    async def run(user):
        with usage_scope(
            user_id=user, turn_id=user, agent_role="core", invocation_id="core-" + user
        ):
            await gateway.complete(request())
            with usage_scope(
                agent_role="nutrition_expert",
                invocation_id="child-" + user,
                parent_invocation_id="core-" + user,
            ):
                await gateway.complete(request())
            await gateway.complete(request())

    await asyncio.gather(run("a"), run("b"))
    for user in ("a", "b"):
        rows = [r for r in store.rows.values() if r["user_id"] == user]
        assert [r["agent_role"] for r in rows] == ["core", "nutrition_expert", "core"]
        assert sum(r["total_tokens"] for r in rows) == 6


async def test_background_job_binds_original_user_turn():
    from slim_guard.memory.extraction import MemoryExtractionJob

    @usage_operation("memory_extraction", source="job", category="background")
    async def process(job):
        return CURRENT.get()

    with usage_scope(user_id="wrong", agent_role="core", invocation_id="wrong"):
        bound = await process(MemoryExtractionJob("job", "owner", "turn", "source", 1))
    assert bound.user_id == "owner" and bound.turn_id == "turn"
    assert bound.agent_role is None and bound.invocation_id is None
    assert bound.category == "background"


async def test_embedding_shape_failure_retains_raw_usage():
    store = Store()
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"usage": {"prompt_tokens": 9, "total_tokens": 9}, "data": []}
            )
        ),
    ) as client:
        gateway = ZhipuEmbeddingGateway(
            api_key="test", base_url="https://test", client=client, usage_store=store
        )
        with pytest.raises(EmbeddingError):
            await gateway.embed(["text"])
    (row,) = store.rows.values()
    assert row["total_tokens"] == 9 and row["status"] == "failed"
    assert row["component"] == "embedding"


@pytest.fixture
async def usage_db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'usage.sqlite'}")
    await db.migrate()
    async with db.session() as s, s.begin():
        s.add_all(
            [
                SlimGuardUser(
                    id="a", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)
                ),
                SlimGuardUser(
                    id="b", first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)
                ),
                AgentVersionRecord(id="v", code_revision="test", manifest_json="{}"),
            ]
        )
        s.add(AgentThreadRecord(id="thread", user_id="a"))
        s.add_all(
            [
                AgentTurnRecord(
                    id="t", thread_id="thread", agent_version_id="v", trigger_type="user_message"
                ),
                AgentTurnRecord(
                    id="empty",
                    thread_id="thread",
                    agent_version_id="v",
                    trigger_type="user_message",
                ),
            ]
        )
    yield db
    await db.close()


async def test_repository_user_isolation_no_double_count_and_empty_turn(usage_db):
    repo = UsageRepository(usage_db)

    async def call(user, turn, role, invocation, tokens, component="agent_generation"):
        with usage_scope(
            user_id=user,
            turn_id=turn,
            agent_role=role,
            invocation_id=invocation,
            component=component,
        ):
            await MeasuredModelGateway(
                ScriptedModelGateway([response(ModelUsage(total_tokens=tokens))]), repo
            ).complete(request())

    await call("a", "t", "core", "i1", 10)
    await call("a", "t", "response_style", "i2", 20)
    await call("a", "t", "response_style", "i2", 30, "style_internal_review")
    await call("b", "t", "core", "i3", 999)
    detail = await repo.detail(user_id="a", request_id="t", limit=1, offset=0)
    assert detail["summary"]["total_tokens"] == 60
    assert detail["summary"]["calls"] == 3 and detail["summary"]["invocations"] == 2
    assert len(detail["items"]) == 1
    (style,) = [a for a in detail["agents"] if a["agent_role"] == "response_style"]
    assert style["invocations"] == 1 and style["total_tokens"] == 50
    listing = await repo.requests(user_id="a", start=None, end=None, limit=20, offset=0)
    assert listing["total"] == 2
    assert {r["id"]: r["calls"] for r in listing["items"]} == {"t": 3, "empty": 0}
    assert (await repo.detail(user_id="b", request_id="empty", limit=20, offset=0))["total"] == 0
    async with usage_db.session() as s, s.begin():
        await UsageRepository.delete_user(s, "a")
    async with usage_db.session() as s:
        assert len(list(await s.scalars(select(ModelUsageRecord)))) == 1


async def test_date_filter_includes_entire_exchange_usage(usage_db):
    repo = UsageRepository(usage_db)
    now = datetime.now(UTC)
    async with usage_db.session() as s, s.begin():
        turn = await s.get(AgentTurnRecord, "t")
        turn.created_at = now - timedelta(days=1)
    with usage_scope(user_id="a", turn_id="t"):
        await MeasuredModelGateway(
            ScriptedModelGateway([response(ModelUsage(total_tokens=3))]), repo
        ).complete(request())
    page = await repo.requests(
        user_id="a", start=now - timedelta(days=2), end=now - timedelta(hours=1), limit=20, offset=0
    )
    assert page["total"] == 1 and page["items"][0]["total_tokens"] == 3


async def test_old_budget_columns_are_removed_and_migration_is_repeatable(usage_db):
    from slim_guard.usage.migration import migrate_usage

    async with usage_db.session() as s, s.begin():
        s.add(invocation_record("parent", "core"))
        s.add(invocation_record("child", "nutrition_expert", "parent"))

    async with usage_db.engine.begin() as conn:
        for column in ("max_model_calls", "max_tool_calls", "max_total_tokens"):
            await conn.execute(
                text(
                    f"ALTER TABLE agent_invocations ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1"
                )
            )
        await migrate_usage(conn)
        await migrate_usage(conn)
        columns = [
            row[1] for row in await conn.execute(text("PRAGMA table_info(agent_invocations)"))
        ]
        assert not {"max_model_calls", "max_tool_calls", "max_total_tokens"}.intersection(columns)
        assert (
            await conn.execute(
                text("SELECT parent_invocation_id FROM agent_invocations WHERE id = 'child'")
            )
        ).scalar_one() == "parent"
        assert (
            await conn.execute(text("SELECT COUNT(*) FROM agent_invocations"))
        ).scalar_one() == 2


def invocation_record(identifier, role, parent=None):
    return AgentInvocationRecord(
        id=identifier,
        trace_id="trace",
        turn_id="t",
        graph_version="current",
        agent_role=role,
        agent_version="test",
        attempt=1,
        caller="harness",
        input_schema_version="1",
        deadline_at=datetime.now(UTC),
        parent_invocation_id=parent,
    )


async def test_agent_execution_with_no_model_call_remains_visible(usage_db):
    async with usage_db.session() as s, s.begin():
        s.add(invocation_record("no-model", "nutrition_expert"))
    repo = UsageRepository(usage_db)
    detail = await repo.detail(user_id="a", request_id="t", limit=20, offset=0)
    assert detail["executions"] == 1 and detail["summary"]["calls"] == 0
    assert detail["agents"][0]["executions"] == 1
    assert detail["agents"][0]["agent_role"] == "nutrition_expert"
    assert (await repo.detail(user_id="b", request_id="t", limit=20, offset=0))["executions"] == 0


async def test_admin_api_authentication_pagination_dates_and_user_scope(usage_db):
    from fastapi import FastAPI

    from slim_guard.admin.auth import ADMIN_SESSION_COOKIE, AdminSessionCodec
    from slim_guard.config import Settings
    from slim_guard.usage.routes import router

    app = FastAPI()
    app.state.settings = Settings(admin_username="reviewer", admin_password="usage-test-password")
    app.state.database = usage_db
    app.include_router(router)
    with usage_scope(user_id="a", turn_id="t"):
        await MeasuredModelGateway(
            ScriptedModelGateway([response(ModelUsage(total_tokens=10))]), UsageRepository(usage_db)
        ).complete(request())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/admin/usage/users")).status_code == 401
        token = AdminSessionCodec(
            password="usage-test-password",
            ttl_seconds=app.state.settings.admin_session_ttl_hours * 3600,
        ).issue("reviewer")
        client.cookies.set(ADMIN_SESSION_COOKIE, token)
        page = await client.get("/api/admin/usage/users/a/requests?limit=1")
        assert page.status_code == 200 and page.json()["total"] == 2
        assert len(page.json()["items"]) == 1
        detail = await client.get("/api/admin/usage/users/b/requests/t")
        assert detail.status_code == 200 and detail.json()["total"] == 0
        assert (await client.get("/api/admin/usage/users?limit=101")).status_code == 422
        assert (
            await client.get("/api/admin/usage/users?start=2026-01-01T00:00:00")
        ).status_code == 422
        assert (await client.get("/api/admin/usage/jobs")).json()["total"] == 0
