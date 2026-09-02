from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from slim_guard.config import Settings
from slim_guard.db.models import (
    AdminAuditEventRecord,
    InboundMessage,
    InteractionTraceRecord,
    OutboundMessage,
    SchemaMigrationRecord,
    SlimGuardUser,
    TraceSpanRecord,
)
from slim_guard.main import create_app


async def test_admin_api_is_authenticated_and_user_scoped(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={
            "app_env": "production",
            "wecom_corp_id": "",
            "wecom_kf_secret": "",
            "wecom_open_kf_id": "",
            "wecom_callback_token": "",
            "wecom_callback_aes_key": "",
            "admin_username": "operator",
            "admin_password": "a-long-test-password",
        }
    )
    app = create_app(settings)
    now = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)

    async with app.router.lifespan_context(app):
        async with app.state.database.session() as session, session.begin():
            session.add(
                SlimGuardUser(
                    id="user-1",
                    nickname="测试用户",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            session.add(
                InboundMessage(
                    channel_id="default",
                    msgid="inbound-1",
                    open_kfid="wk-test",
                    external_userid="external-1",
                    msgtype="text",
                    origin=3,
                    send_time=now,
                )
            )
            session.add(
                OutboundMessage(
                    idempotency_key="outbound-1",
                    platform_msgid="platform-1",
                    channel_id="default",
                    inbound_msgid="inbound-1",
                    open_kfid="wk-test",
                    external_userid="external-1",
                    content="已记录。",
                    status="accepted",
                    completed_at=now,
                )
            )
            session.add(
                InteractionTraceRecord(
                    id="trace-1",
                    user_id="user-1",
                    trigger_type="user_message",
                    channel_id="default",
                    inbound_msgid="inbound-1",
                    outbound_idempotency_key="outbound-1",
                    reply_kind="agent",
                    generation_status="succeeded",
                    delivery_status="accepted",
                    created_at=now,
                    completed_at=now,
                )
            )
            session.add(
                TraceSpanRecord(
                    id="span-1",
                    trace_id="trace-1",
                    sequence=1,
                    component="wecom",
                    operation="send_text",
                    status="completed",
                    attributes_json="{}",
                    started_at=now,
                    completed_at=now,
                )
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as http:
            unauthorized = await http.get("/api/admin/users")
            wrong_login = await http.post(
                "/api/admin/auth/login",
                json={"username": "operator", "password": "wrong-password"},
            )
            login = await http.post(
                "/api/admin/auth/login",
                json={
                    "username": "operator",
                    "password": "a-long-test-password",
                },
            )
            session_response = await http.get("/api/admin/session")
            users = await http.get("/api/admin/users")
            traces = await http.get("/api/admin/users/user-1/traces")
            detail = await http.get("/api/admin/users/user-1/traces/trace-1")
            wrong_user = await http.get("/api/admin/users/user-2/traces/trace-1")
            logout = await http.post("/api/admin/auth/logout")
            after_logout = await http.get("/api/admin/users")

        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "no-store"
        assert wrong_login.status_code == 401
        assert login.status_code == 200
        assert login.json()["username"] == "operator"
        assert "slim_guard_admin_session=" in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "Secure" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert session_response.status_code == 200
        assert session_response.json()["username"] == "operator"
        assert users.status_code == 200
        assert users.json()["items"][0]["nickname"] == "测试用户"
        assert users.json()["items"][0]["external_refs"] == []
        assert traces.status_code == 200
        assert traces.json()["items"][0]["id"] == "trace-1"
        assert detail.status_code == 200
        assert detail.json()["timeline"][0]["operation"] == "send_text"
        assert detail.json()["timeline"][0]["presentation"]["title"] == "发送回复到企业微信"
        assert detail.json()["execution_summary"] == {
            "architecture": "service",
            "model_call_count": 0,
            "tool_call_count": 0,
                "observation_count": 0,
                "context_snapshot_count": 0,
                "memory_ingestion_count": 0,
                "memory_recall_count": 0,
        }
        assert detail.json()["output"]["content"] == "已记录。"
        assert wrong_user.status_code == 404
        assert logout.status_code == 200
        assert after_logout.status_code == 401

        async with app.state.database.session() as session:
            audit_count = await session.scalar(
                select(func.count(AdminAuditEventRecord.id))
            )
            migrations = set(
                await session.scalars(select(SchemaMigrationRecord.version))
            )
        assert audit_count == 1
        assert "20260831_01_interaction_tracing" in migrations
        assert "20260902_01_body_fat_records" in migrations
