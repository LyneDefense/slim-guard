from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import MessageRole, ModelMessage, ModelResponse
from slim_guard.config import Settings
from slim_guard.db.models import (
    InteractionTraceRecord,
    MobileAgentRequestRecord,
    MobileAuthIdentityRecord,
    MobileCoachProfileRecord,
    MobileDeviceRecord,
    SlimGuardUser,
)
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
        memory_extraction_enabled=False,
        memory_recall_enabled=False,
        routine_scheduler_enabled=False,
        log_level="WARNING",
    )
    model = ScriptedModelGateway((reply("记下了，我们慢慢来。"),))
    app = create_app(settings, model_gateway=model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
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
            required_profile = await http.get(
                "/api/mobile/v1/coach-profile",
                headers=headers,
            )
            chat_payload = {
                "text": "今天开始认真记录",
                "idempotency_key": "mobile-message-0001",
            }
            blocked_chat = await http.post(
                "/api/mobile/v1/chat/messages",
                headers=headers,
                json=chat_payload,
            )
            today_date = datetime.now(UTC).date()
            saved_profile = await http.put(
                "/api/mobile/v1/coach-profile",
                headers=headers,
                json={
                    "age_band": "30_39",
                    "height_cm": 168.5,
                    "current_weight_kg": 72.5,
                    "weight_measured_on": today_date.isoformat(),
                    "goal_type": "lose_weight",
                    "target_weight_kg": 65,
                    "target_date": (today_date + timedelta(days=120)).isoformat(),
                    "current_body_fat_percent": 28.2,
                    "target_body_fat_percent": 22,
                    "exercise_frequency": "weekly_1_2",
                },
            )
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
            device = await http.put(
                "/api/mobile/v1/devices/current",
                headers=headers,
                json={
                    "installation_id": "integration-installation-1",
                    "platform": "ios",
                    "push_provider": "expo",
                    "push_token": "ExponentPushToken[integration-test]",
                    "app_version": "1.0.0",
                    "timezone": "Asia/Shanghai",
                    "locale": "zh-CN",
                },
            )
            binding = await http.post("/api/mobile/v1/bindings/wecom", headers=headers)
            binding_status = await http.get(
                "/api/mobile/v1/bindings/wecom",
                headers=headers,
            )
            ready = await http.get("/health/ready")

        assert unauthorized.status_code == 401
        assert challenge.status_code == 200
        assert code is not None
        assert login.status_code == 200
        assert login.json()["token_type"] == "Bearer"
        assert me.status_code == 200
        assert me.json()["nickname"] == "阿杰"
        assert required_profile.json() == {
            "schema_version": 1,
            "status": "required",
            "coach_enabled": False,
            "profile": None,
        }
        assert blocked_chat.status_code == 428
        assert blocked_chat.json()["detail"]["code"] == "coach_profile_required"
        assert saved_profile.status_code == 200
        assert saved_profile.json()["status"] == "ready"
        assert saved_profile.json()["coach_enabled"] is True
        assert saved_profile.json()["profile"]["height_cm"] == 168.5
        assert saved_profile.json()["profile"]["revision"] == 1
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
        assert today.json()["current_weight_kg"] == 72.5
        assert today.json()["current_body_fat_percent"] == 28.2
        assert today.json()["routine"]["daily_review_time"] == "21:00"
        assert device.status_code == 200
        assert "push_token" not in device.json()
        assert binding.status_code == 200
        assert binding.json()["code"] is not None
        assert binding_status.json()["code_hint"] == binding.json()["code"][-4:]
        assert ready.status_code == 200

        async with app.state.database.session() as session:
            request_count = await session.scalar(select(func.count(MobileAgentRequestRecord.id)))
            trace = await session.scalar(select(InteractionTraceRecord))
            stored_device = await session.scalar(select(MobileDeviceRecord))
            stored_profile = await session.scalar(select(MobileCoachProfileRecord))
        assert request_count == 1
        assert trace is not None
        assert trace.channel_id == "mobile"
        assert trace.generation_status == "succeeded"
        assert trace.delivery_status == "accepted"
        assert stored_device is not None
        assert stored_profile is not None
        assert stored_profile.target_weight_grams == 65_000

        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
            deleted = await http.request(
                "DELETE",
                "/api/mobile/v1/me",
                headers=headers,
                json={"confirmation": "DELETE"},
            )
            after_delete = await http.get("/api/mobile/v1/me", headers=headers)
        assert deleted.status_code == 204
        assert after_delete.status_code == 401
        async with app.state.database.session() as session:
            deleted_user = await session.get(SlimGuardUser, login.json()["user"]["id"])
        assert deleted_user is None
    await model.close()


async def test_mobile_coach_profile_rejects_invalid_dates_and_allows_minors(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mobile-profile-gate.sqlite3'}",
        mobile_api_enabled=True,
        mobile_auth_secret="mobile-api-test-secret-with-at-least-32-characters",
        mobile_test_accounts_enabled=True,
        mobile_test_account_password="123456",
        memory_extraction_enabled=False,
        memory_recall_enabled=False,
        routine_scheduler_enabled=False,
        log_level="WARNING",
    )
    model = ScriptedModelGateway((reply("可以，我们先从今天这顿饭开始。"),))
    app = create_app(settings, model_gateway=model)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        ) as http:
            login = await http.post(
                "/api/mobile/v1/auth/password/login",
                json={"username": "test1", "password": "123456"},
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            today_date = datetime.now(UTC).date()
            payload = {
                "age_band": "10_17",
                "height_cm": 165,
                "current_weight_kg": 60,
                "weight_measured_on": today_date.isoformat(),
                "goal_type": "lose_weight",
                "target_weight_kg": 55,
                "target_date": (today_date + timedelta(days=90)).isoformat(),
                "current_body_fat_percent": None,
                "target_body_fat_percent": None,
                "exercise_frequency": None,
            }
            future_measurement = await http.put(
                "/api/mobile/v1/coach-profile",
                headers=headers,
                json={
                    **payload,
                    "weight_measured_on": (today_date + timedelta(days=1)).isoformat(),
                },
            )
            minor = await http.put(
                "/api/mobile/v1/coach-profile",
                headers=headers,
                json=payload,
            )
            chat = await http.post(
                "/api/mobile/v1/chat/messages",
                headers=headers,
                json={"text": "帮我看看今天吃什么", "idempotency_key": "minor-chat-1"},
            )
            edited = await http.put(
                "/api/mobile/v1/coach-profile",
                headers=headers,
                json={**payload, "age_band": "18_29"},
            )

        assert future_measurement.status_code == 422
        assert future_measurement.json()["detail"]["code"] == "invalid_coach_profile"
        assert minor.status_code == 200
        assert minor.json()["status"] == "ready"
        assert minor.json()["coach_enabled"] is True
        assert chat.status_code == 200
        assert chat.json()["text"] == "可以，我们先从今天这顿饭开始。"
        assert edited.status_code == 200
        assert edited.json()["status"] == "ready"
        assert edited.json()["profile"]["revision"] == 2

        async with app.state.database.session() as session:
            assert await session.scalar(select(func.count(MobileAgentRequestRecord.id))) == 1
        trusted_context = next(
            message.content
            for message in model.requests[0].messages
            if message.content and message.content.startswith("权威用户事实")
        )
        assert '"weight_assessment_standard":"minor"' in trusted_context
    await model.close()


async def test_mobile_test_accounts_login_and_keep_edited_nickname(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'mobile-test-login.sqlite3'}",
        mobile_api_enabled=True,
        mobile_auth_secret="mobile-api-test-secret-with-at-least-32-characters",
        mobile_test_accounts_enabled=True,
        mobile_test_account_password="123456",
        memory_extraction_enabled=False,
        memory_recall_enabled=False,
        routine_scheduler_enabled=False,
        log_level="WARNING",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
        ) as http:
            options = await http.get("/api/mobile/v1/auth/options")
            invalid = await http.post(
                "/api/mobile/v1/auth/password/login",
                json={"username": "test2", "password": "wrong"},
            )
            login = await http.post(
                "/api/mobile/v1/auth/password/login",
                json={
                    "username": "test2",
                    "password": "123456",
                    "device_label": "iPhone Simulator",
                },
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            renamed = await http.patch(
                "/api/mobile/v1/me",
                headers=headers,
                json={"nickname": "模拟器里的小林"},
            )
            progress = await http.get(
                "/api/mobile/v1/progress?limit=60",
                headers=headers,
            )
            second_login = await http.post(
                "/api/mobile/v1/auth/password/login",
                json={"username": "test2", "password": "123456"},
            )

        assert options.status_code == 200
        assert options.json() == {
            "phone_login_enabled": True,
            "test_account_login_enabled": True,
            "test_accounts": [
                {"username": f"test{index}", "default_nickname": f"测试用户 {index}"}
                for index in range(1, 6)
            ],
        }
        assert invalid.status_code == 401
        assert invalid.json()["detail"]["code"] == "invalid_credentials"
        assert login.status_code == 200
        assert login.json()["user"]["identity_hint"] == "测试账号 test2"
        assert renamed.json()["nickname"] == "模拟器里的小林"
        assert progress.status_code == 200
        assert progress.json() == {
            "weights": [],
            "body_fat": [],
            "meals": [],
            "exercise": [],
        }
        assert second_login.json()["user"]["nickname"] == "模拟器里的小林"

        async with app.state.database.session() as session:
            users = list(await session.scalars(select(SlimGuardUser)))
            identities = list(
                await session.scalars(
                    select(MobileAuthIdentityRecord).where(
                        MobileAuthIdentityRecord.provider == "test_account"
                    )
                )
            )
        assert len(users) == 5
        assert len(identities) == 5
