from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.config import Settings
from slim_guard.db.models import InteractionTraceRecord, MobileAgentRequestRecord
from slim_guard.main import create_app


def reply(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role=MessageRole.ASSISTANT, content=text),
        finish_reason="stop",
    )


async def test_mobile_api_auth_chat_idempotency_and_dashboard(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mobile-api.sqlite3'}",
        mobile_api_enabled=True,
        mobile_auth_secret="mobile-api-test-secret-with-at-least-32-characters",
        mobile_dev_otp_enabled=True,
        zhipu_api_key="configured-for-test",
        memory_ingestion_enabled=False,
        memory_recall_enabled=False,
        routine_scheduler_enabled=False,
        log_level="WARNING",
    )
    model = ScriptedModelGateway((reply("记下了，我们慢慢来。"),))
    app = create_app(settings, model_gateway=model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as http:
            unauthorized = await http.get("/api/mobile/v1/me")
            challenge = await http.post(
                "/api/mobile/v1/auth/otp/request",
                json={"phone": "13800138000"},
            )
            code = challenge.json()["debug_code"]
            login = await http.post(
                "/api/mobile/v1/auth/otp/verify",
                json={
                    "challenge_id": challenge.json()["challenge_id"],
                    "code": code,
                    "device_label": "iOS Simulator",
                },
            )
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            me = await http.patch(
                "/api/mobile/v1/me",
                headers=headers,
                json={"nickname": "阿杰"},
            )
            chat_payload = {
                "text": "今天开始认真记录",
                "idempotency_key": "mobile-message-0001",
            }
            chat = await http.post(
                "/api/mobile/v1/chat/messages",
                headers=headers,
                json=chat_payload,
            )
            replayed = await http.post(
                "/api/mobile/v1/chat/messages",
                headers=headers,
                json=chat_payload,
            )
            history = await http.get("/api/mobile/v1/chat/messages", headers=headers)
            routine = await http.put(
                "/api/mobile/v1/routine",
                headers=headers,
                json={
                    "timezone": "Asia/Shanghai",
                    "weight": {"enabled": True, "local_time": "08:00"},
                    "daily_review": {"enabled": True, "local_time": "21:00"},
                },
            )
            today = await http.get("/api/mobile/v1/today", headers=headers)
            ready = await http.get("/health/ready")

        assert unauthorized.status_code == 401
        assert challenge.status_code == 200
        assert code is not None
        assert login.status_code == 200
        assert login.json()["token_type"] == "Bearer"
        assert me.status_code == 200
        assert me.json()["nickname"] == "阿杰"
        assert chat.status_code == 200
        assert chat.json()["text"] == "记下了，我们慢慢来。"
        assert replayed.status_code == 200
        assert replayed.json()["replayed"] is True
        assert [item["role"] for item in history.json()["items"]] == [
            "user",
            "assistant",
        ]
        assert routine.json()["weight_reminder_time"] == "08:00"
        assert today.status_code == 200
        assert today.json()["routine"]["daily_review_time"] == "21:00"
        assert ready.status_code == 200

        async with app.state.database.session() as session:
            request_count = await session.scalar(
                select(func.count(MobileAgentRequestRecord.id))
            )
            trace = await session.scalar(select(InteractionTraceRecord))
        assert request_count == 1
        assert trace is not None
        assert trace.channel_id == "mobile"
        assert trace.generation_status == "succeeded"
        assert trace.delivery_status == "accepted"
    await model.close()
