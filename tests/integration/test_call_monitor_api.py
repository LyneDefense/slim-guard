"""Real SQLite and authenticated HTTP boundaries; no production data or models."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from slim_guard.admin.calls.routes import router as monitor_router
from slim_guard.api.admin_routes import router as admin_router
from slim_guard.config import Settings
from slim_guard.db.models import (
    AdminAuditEventRecord,
    AgentItemRecord,
    AgentItemRedactionRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    InteractionTraceRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.usage.models import ModelUsageRecord

NOW = datetime(2026, 11, 3, tzinfo=UTC)


@pytest.fixture
async def monitor(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'monitor.sqlite'}")
    await db.create_schema()
    async with db.session() as session, session.begin():
        session.add(AgentVersionRecord(id="version", manifest_json="{}", code_revision="test"))
        await session.flush()
        for index, user in enumerate(("user-a", "user-b")):
            created = NOW + timedelta(minutes=index)
            session.add(
                SlimGuardUser(
                    id=user, nickname=f"测试{user}", first_seen_at=created, last_seen_at=created
                )
            )
            await session.flush()
            session.add(
                AgentThreadRecord(
                    id=f"thread-{user}", user_id=user, created_at=created, last_active_at=created
                )
            )
            await session.flush()
            session.add(
                AgentTurnRecord(
                    id=f"turn-{user}",
                    thread_id=f"thread-{user}",
                    agent_version_id="version",
                    trigger_type="user_message",
                    status="completed",
                    created_at=created,
                    updated_at=created,
                    completed_at=created,
                )
            )
            await session.flush()
            session.add(
                InteractionTraceRecord(
                    id=f"trace-{user}",
                    user_id=user,
                    trigger_type="user_message",
                    agent_turn_id=f"turn-{user}",
                    reply_kind="agent",
                    generation_status="succeeded",
                    delivery_status="accepted",
                    created_at=created,
                    completed_at=created + timedelta(seconds=5),
                )
            )
            for sequence, (kind, body) in enumerate(
                [
                    ("user_message", {"text": f"这是{user}的晚餐"}),
                    ("agent_message", {"participant": "coach", "text": "有主食和蔬菜。"}),
                    ("user_message", {"text": "redacted-secret"}),
                ]
            ):
                item_id = f"item-{user}-{sequence}"
                session.add(
                    AgentItemRecord(
                        id=item_id,
                        thread_id=f"thread-{user}",
                        turn_id=f"turn-{user}",
                        sequence=sequence,
                        item_type=kind,
                        status="completed",
                        payload_json=json.dumps(body, ensure_ascii=False),
                        created_at=created,
                    )
                )
                await session.flush()
                if sequence == 2:
                    session.add(
                        AgentItemRedactionRecord(
                            item_id=item_id,
                            original_payload_sha256="a" * 64,
                            policy_version="test",
                            redacted_at=NOW,
                        )
                    )
            session.add(
                ModelUsageRecord(
                    id=f"call-{user}",
                    user_id=user,
                    turn_id=f"turn-{user}",
                    category="online",
                    component="agent_generation",
                    execution_kind="agent",
                    provider="test",
                    model="test-model",
                    purpose="CoachReply",
                    status="succeeded",
                    started_at=created,
                    completed_at=created + timedelta(seconds=1),
                    total_tokens=10,
                )
            )
    app = FastAPI()
    app.state.database = db
    app.state.settings = Settings(
        _env_file=None,
        admin_username="operator",
        admin_password="test-long-password",
        app_env="production",
    )
    app.include_router(admin_router)
    app.include_router(monitor_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test.local"
    ) as client:
        yield client, db
    await db.close()


async def login(client):
    response = await client.post(
        "/api/admin/auth/login", json={"username": "operator", "password": "test-long-password"}
    )
    assert response.status_code == 200


async def test_authentication_and_owner_scope(monitor):
    client, _ = monitor
    assert (await client.get("/api/admin/calls")).status_code == 401
    await login(client)
    assert (await client.get("/api/admin/calls/user-b/traces/trace-user-a")).status_code == 404
    assert (await client.get("/api/admin/calls/user-a/traces/trace-user-a")).status_code == 200


async def test_list_search_pagination_and_sensitive_preview(monitor):
    client, _ = monitor
    await login(client)
    first = (await client.get("/api/admin/calls?limit=1")).json()
    second = (await client.get("/api/admin/calls?limit=1&offset=1")).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert "redacted-secret" not in json.dumps(first)
    hidden = (await client.get("/api/admin/calls?search=redacted-secret")).json()
    assert hidden["total"] == 0
    match = (await client.get("/api/admin/calls?search=user-a")).json()
    assert match["total"] == 1
    assert match["items"][0]["process"]["model_calls"] == 1


async def test_detail_final_output_and_unknown_client_are_not_fabricated(monitor):
    client, db = monitor
    await login(client)
    data = (await client.get("/api/admin/calls/user-a/traces/trace-user-a")).json()
    assert "redacted-secret" not in json.dumps(data)
    assert data["process"]["messages"][0]["text"] == "有主食和蔬菜。"
    assert data["process"]["summary"]["client_status"] == "not_reported"
    assert data["process"]["summary"]["model_calls"] == 1
    request = next(n for n in data["process"]["nodes"] if n["kind"] == "model")
    assert request["input"] is None
    assert request["duration_ms"] == 1000
    # SQLite stores ledger timestamps in UTC but drops tzinfo. All monitor
    # timestamps must use the same explicit timezone, including the Trace origin.
    start = datetime.fromisoformat(data["trace"]["created_at"].replace("Z", "+00:00"))
    model_start = datetime.fromisoformat(request["started_at"].replace("Z", "+00:00"))
    assert start.utcoffset() == model_start.utcoffset() == timedelta(0)
    assert (model_start - start).total_seconds() == 0
    async with db.session() as session:
        assert await session.scalar(select(func.count(AdminAuditEventRecord.id))) > 0


@pytest.mark.parametrize(
    "query",
    [
        "start=2026-11-03T00:00:00",
        "start=2026-11-04T00:00:00Z&end=2026-11-03T00:00:00Z",
        "limit=101",
        "offset=-1",
        "result=made-up",
    ],
)
async def test_invalid_filters_are_rejected(monitor, query):
    client, _ = monitor
    await login(client)
    assert (await client.get(f"/api/admin/calls?{query}")).status_code == 422


async def test_image_must_belong_to_both_user_and_turn(monitor):
    client, _ = monitor
    await login(client)
    assert (
        await client.get("/api/admin/calls/user-a/traces/trace-user-a/images/nonexistent")
    ).status_code == 404


async def test_user_filter_has_database_total_not_browser_page_filter(monitor):
    client, _ = monitor
    await login(client)
    data = (await client.get("/api/admin/calls?user_id=user-a&limit=1")).json()
    assert data["total"] == 1
    assert data["items"][0]["user_id"] == "user-a"


async def test_structural_repairs_are_searchable_and_keep_exact_feedback(monitor):
    client, db = monitor
    async with db.session() as session, session.begin():
        session.add(
            AgentItemRecord(
                id="format-feedback",
                thread_id="thread-user-a",
                turn_id="turn-user-a",
                sequence=10,
                item_type="structured_output_rejected",
                status="completed",
                payload_json=json.dumps(
                    {
                        "schema": "CoachReply",
                        "attempt": 1,
                        "errors_json": json.dumps([{"path": ["text"], "code": "missing"}]),
                    }
                ),
                created_at=NOW,
            )
        )
    await login(client)
    listing = (await client.get("/api/admin/calls?result=issues")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["process"]["repaired"]
    data = (await client.get("/api/admin/calls/user-a/traces/trace-user-a")).json()
    rejected = next(n for n in data["process"]["nodes"] if n["kind"] == "validation")
    assert rejected["output"] == [{"path": ["text"], "code": "missing"}]
