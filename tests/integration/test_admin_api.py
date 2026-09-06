from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from slim_guard.agents.contracts import payload_sha256
from slim_guard.config import Settings
from slim_guard.db.models import (
    AdminAuditEventRecord,
    AgentArtifactRecord,
    AgentInvocationRecord,
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
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
                AgentVersionRecord(
                    id="version-1",
                    manifest_json="{}",
                    code_revision="test",
                    created_at=now,
                )
            )
            session.add(
                AgentThreadRecord(
                    id="thread-1",
                    user_id="user-1",
                    created_at=now,
                    last_active_at=now,
                )
            )
            session.add(
                AgentTurnRecord(
                    id="turn-1",
                    thread_id="thread-1",
                    agent_version_id="version-1",
                    trigger_type="user_message",
                    status="completed",
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
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
                    agent_turn_id="turn-1",
                    agent_version_id="version-1",
                    created_at=now,
                    completed_at=now,
                )
            )
            candidate_payload = {
                "text": "Shadow 候选回复",
                "style_profile_version": "slimguard_default_v1",
                "prompt": "不得展示的系统提示词",
                "chain_of_thought": "不得展示的隐藏推理",
            }
            session.add(
                AgentInvocationRecord(
                    id="invocation-1",
                    trace_id="trace-1",
                    turn_id="turn-1",
                    graph_version="shadow-v1",
                    agent_role="response_style",
                    agent_version="style-v1",
                    attempt=1,
                    caller="coordinator",
                    input_artifact_ids_json="[]",
                    input_schema_version="1",
                    allowed_tools_json="[]",
                    privacy_scopes_json="[]",
                    deadline_at=now,
                    max_model_calls=1,
                    max_tool_calls=0,
                    max_total_tokens=1000,
                    input_payload_json='{"prompt":"also hidden"}',
                    status="succeeded",
                    output_schema="StyledResponse",
                    output_schema_version="1",
                    output_artifact_id="artifact-1",
                    tool_receipt_ids_json="[]",
                    model_call_count=1,
                    tool_call_count=0,
                    total_token_count=12,
                    started_at=now,
                    completed_at=now,
                )
            )
            session.add(
                AgentArtifactRecord(
                    id="artifact-1",
                    turn_id="turn-1",
                    invocation_id="invocation-1",
                    producer_role="response_style",
                    artifact_type="StyledResponse",
                    schema_version="1",
                    parent_artifact_ids_json="[]",
                    payload_sha256=payload_sha256(candidate_payload),
                    payload_json=(
                        '{"chain_of_thought":"不得展示的隐藏推理",'
                        '"prompt":"不得展示的系统提示词",'
                        '"style_profile_version":"slimguard_default_v1",'
                        '"text":"Shadow 候选回复"}'
                    ),
                    created_at=now,
                )
            )
            session.add_all(
                (
                    AgentItemRecord(
                        id="item-transition",
                        thread_id="thread-1",
                        turn_id="turn-1",
                        sequence=1,
                        item_type="workflow_transition",
                        status="completed",
                        payload_json=(
                            '{"attempt":1,"from_node":"orchestrator",'
                            '"reason_code":"plan_ready",'
                            '"to_node":"response_style",'
                            '"transition_type":"route"}'
                        ),
                        created_at=now,
                    ),
                    AgentItemRecord(
                        id="item-adopted",
                        thread_id="thread-1",
                        turn_id="turn-1",
                        sequence=2,
                        item_type="response_adopted",
                        status="completed",
                        payload_json=('{"artifact_id":"artifact-1","final":false,"mode":"shadow"}'),
                        created_at=now,
                    ),
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

        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
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
        assert detail.json()["workflow"]["summary"] == {
            "mode": "shadow",
            "graph_version": "shadow-v1",
            "status": "succeeded",
            "model_call_count": 1,
            "tool_call_count": 0,
            "total_token_count": 12,
            "repair_count": 0,
            "degraded": False,
            "style_profile_version": "slimguard_default_v1",
            "style_status": "shadow_candidate",
            "style_bypassed": False,
            "style_degraded": False,
            "style_adopted": True,
        }
        assert detail.json()["invocations"][0]["invocation_id"] == "invocation-1"
        assert detail.json()["artifacts"][0]["payload"] == {
            "style_profile_version": "slimguard_default_v1"
        }
        assert detail.json()["artifacts"][0]["body_redacted"] is True
        assert detail.json()["workflow"]["style"] == {
            "style_profile_version": "slimguard_default_v1",
            "status": "shadow_candidate",
            "bypassed": False,
            "bypass_reason": None,
            "degraded": False,
            "degraded_reason": None,
            "adopted": True,
            "adopted_artifact_id": "artifact-1",
            "adoption_mode": "shadow",
            "final": False,
        }
        assert detail.json()["transitions"][0]["to_node"] == "response_style"
        assert detail.json()["shadow_comparison"] == {
            "mode": "shadow",
            "delivery_status": "not_sent",
            "business_writes": "no_business_writes",
            "legacy": {
                "artifact_id": None,
                "content": "已记录。",
                "status": "accepted",
            },
            "candidate": {
                "artifact_id": "artifact-1",
                "content": "Shadow 候选回复",
                "status": "succeeded",
            },
        }
        assert "不得展示" not in detail.text
        assert wrong_user.status_code == 404
        assert logout.status_code == 200
        assert after_logout.status_code == 401

        async with app.state.database.session() as session:
            audit_count = await session.scalar(select(func.count(AdminAuditEventRecord.id)))
            migrations = set(await session.scalars(select(SchemaMigrationRecord.version)))
        assert audit_count == 1
        assert "20260831_01_interaction_tracing" in migrations
        assert "20260902_01_body_fat_records" in migrations
