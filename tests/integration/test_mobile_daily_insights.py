import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.config import Settings
from slim_guard.db.models import AgentThreadRecord, AgentTurnRecord, AgentVersionRecord, MealRecord
from slim_guard.features.daily_insights.models import DailyInsightRecord
from slim_guard.main import create_app
from slim_guard.usage.models import ModelUsageRecord

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


class InsightModel:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        data = json.loads(request.messages[-1].content)
        if request.output_schema_name == "InsightReview":
            value = {"passed": True, "issues": []}
        else:
            facts = [e for e in data["evidence"] if e["today"]]
            value = {"headline": {"text": "今天午饭想吃什么？", "claims": []}, "summary": None}
            if facts:
                text = "今天记录了" + "、".join(e["label"] for e in facts) + "。"
                value["summary"] = {
                    "text": text,
                    "claims": [
                        {
                            "text": text,
                            "kind": "observation",
                            "source_refs": [e["id"] for e in facts],
                        }
                    ],
                }
        return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


async def login(http, phone):
    challenge = (await http.post("/api/mobile/v1/auth/otp/request", json={"phone": phone})).json()
    issued = (
        await http.post(
            "/api/mobile/v1/auth/otp/verify",
            json={
                "challenge_id": challenge["challenge_id"],
                "code": challenge["debug_code"],
                "device_label": "daily-insights-test",
            },
        )
    ).json()
    return issued["user"]["id"], {"Authorization": f"Bearer {issued['access_token']}"}


async def test_insight_api_auth_real_record_sources_edit_invalidation_and_account_erasure(
    tmp_path, monkeypatch
):
    # Exercise real app composition, auth, migrations, data adapters and editor;
    # claim the worker deterministically rather than racing its polling timer.
    monkeypatch.setattr("slim_guard.app.mobile.MobileComponents.start", lambda self: None)
    model = InsightModel()
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'insights-api.sqlite'}",
            mobile_api_enabled=True,
            mobile_auth_secret="insights-secret-longer-than-32-characters",
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
        assert await app.state.database.migrate() == ()
        service = app.state.daily_insights
        clock = [NOW]
        service.clock = lambda: clock[0]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
            for method, path in [(http.get, ""), (http.post, "/retry")]:
                assert (await method(f"/api/mobile/v1/daily-insights{path}")).status_code == 401
            owner, headers = await login(http, "13800138001")
            other, other_headers = await login(http, "13800138002")
            async with app.state.database.session() as session, session.begin():
                session.add(
                    AgentVersionRecord(
                        id="insight-version", manifest_json="{}", code_revision="test"
                    )
                )
                await session.flush()
                session.add(AgentThreadRecord(id="insight-thread", user_id=owner))
                await session.flush()
                session.add(
                    AgentTurnRecord(
                        id="insight-turn",
                        thread_id="insight-thread",
                        agent_version_id="insight-version",
                        trigger_type="user_message",
                    )
                )
                await session.flush()
                session.add(
                    MealRecord(
                        id="insight-meal",
                        user_id=owner,
                        meal_type="lunch",
                        foods_json=json.dumps([{"item_id": "food1", "name": "清炒白菜"}]),
                        occurred_on=date(2026, 10, 30),
                        time_precision="date",
                        time_basis="user_reported",
                        idempotency_key="insight-meal",
                        source_turn_id="insight-turn",
                        source_tool_call_id="call",
                    )
                )
            pending = await http.get("/api/mobile/v1/daily-insights", headers=headers)
            assert pending.status_code == 200 and pending.json()["status"] == "pending"
            assert pending.headers["Cache-Control"] == "no-store"
            assert not model.requests  # No LLM wait on the API read.
            assert await service.run_once()
            ready = (await http.get("/api/mobile/v1/daily-insights", headers=headers)).json()
            assert ready["summary"] == "今天记录了清炒白菜。"
            assert ready["evidence"][0]["label"] == "清炒白菜"
            async with app.state.database.session() as session:
                calls = list(await session.scalars(select(ModelUsageRecord)))
                assert len(calls) == 2
                assert all(
                    c.category == "background" and c.user_id == owner and c.turn_id is None
                    for c in calls
                )
                assert {c.component for c in calls} == {
                    "daily_insight_generation",
                    "daily_insight_review",
                }
            isolated = (
                await http.get("/api/mobile/v1/daily-insights", headers=other_headers)
            ).json()
            assert isolated["summary_state"] == "empty" and isolated["evidence"] == []
            assert await service.run_once()
            edited = await http.patch(
                "/api/mobile/v1/records/meal/insight-meal",
                headers=headers,
                json={
                    "operation_id": str(uuid4()),
                    "expected_revision": 1,
                    "meal_type": "dinner",
                    "foods": [{"item_id": "food1", "name": "鸡蛋"}],
                },
            )
            assert edited.status_code == 200, edited.text
            invalidated = (await http.get("/api/mobile/v1/daily-insights", headers=headers)).json()
            assert invalidated["status"] == "pending" and invalidated["summary"] is None
            clock[0] += timedelta(seconds=3)
            assert await service.run_once()
            regenerated = (await http.get("/api/mobile/v1/daily-insights", headers=headers)).json()
            assert regenerated["summary"] == "今天记录了鸡蛋。"
            assert regenerated["evidence"][0]["id"].endswith(":r2")
            await app.state.mobile_platform.delete_account(owner)
            async with app.state.database.session() as session:
                assert await session.get(DailyInsightRecord, (owner, NOW.date())) is None
                assert await session.scalar(select(DailyInsightRecord.user_id)) == other
