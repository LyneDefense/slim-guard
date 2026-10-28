from datetime import UTC, date, datetime

from httpx import ASGITransport, AsyncClient

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.config import Settings
from slim_guard.db.models import AgentThreadRecord, AgentTurnRecord, AgentVersionRecord, MealRecord
from slim_guard.main import create_app


async def test_period_progress_api_validates_period_auth_and_serializes_complete_counts(
    tmp_path, monkeypatch
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 16, 30, tzinfo=UTC)

    monkeypatch.setattr("slim_guard.api.mobile_routes.datetime", Clock)
    model = ScriptedModelGateway(())
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'period-api.sqlite'}",
            mobile_api_enabled=True,
            mobile_auth_secret="period-progress-secret-longer-than-32-characters",
            mobile_dev_otp_enabled=True,
            zhipu_api_key="test-configured",
            memory_extraction_enabled=False,
            memory_recall_enabled=False,
            routine_scheduler_enabled=False,
            log_level="WARNING",
        ),
        model_gateway=model,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
            assert (await http.get("/api/mobile/v1/progress/period?days=7")).status_code == 401
            challenge = (
                await http.post("/api/mobile/v1/auth/otp/request", json={"phone": "13800138000"})
            ).json()
            issued = (
                await http.post(
                    "/api/mobile/v1/auth/otp/verify",
                    json={
                        "challenge_id": challenge["challenge_id"],
                        "code": challenge["debug_code"],
                        "device_label": "period-test",
                    },
                )
            ).json()
            headers = {"Authorization": f"Bearer {issued['access_token']}"}
            async with app.state.database.session() as session, session.begin():
                session.add(
                    AgentVersionRecord(id="version", manifest_json="{}", code_revision="test")
                )
                await session.flush()
                session.add(AgentThreadRecord(id="thread", user_id=issued["user"]["id"]))
                await session.flush()
                session.add(
                    AgentTurnRecord(
                        id="turn",
                        thread_id="thread",
                        agent_version_id="version",
                        trigger_type="user_message",
                    )
                )
                await session.flush()
                session.add_all(
                    [
                        MealRecord(
                            id=f"meal-{index}",
                            user_id=issued["user"]["id"],
                            meal_type="dinner",
                            foods_json="[]",
                            occurred_on=date(2026, 10, 23),
                            time_precision="date",
                            time_basis="user_reported",
                            idempotency_key=f"meal-{index}",
                            source_turn_id="turn",
                            source_tool_call_id=f"call-{index}",
                        )
                        for index in range(131)
                    ]
                )
            response = await http.get("/api/mobile/v1/progress/period?days=7", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["from_date"] == "2026-10-23"
            assert data["to_date"] == "2026-10-29"  # User timezone, not server UTC date.
            assert data["timezone"] == "Asia/Shanghai"
            assert data["counts"] == {"weight": 0, "body_fat": 0, "meal": 131, "exercise": 0}
            assert data["weights"] == data["body_fat"] == []
            thirty = await http.get("/api/mobile/v1/progress/period?days=30", headers=headers)
            assert thirty.status_code == 200
            assert thirty.json()["from_date"] == "2026-09-30"
            for value in ["0", "14", "31", "foo"]:
                assert (
                    await http.get(f"/api/mobile/v1/progress/period?days={value}", headers=headers)
                ).status_code == 422
            assert model.requests == []
