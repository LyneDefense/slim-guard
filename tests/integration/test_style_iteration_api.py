"""Admin style iteration API tests use de-identified invented feedback."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.main import create_app
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)

PREFIX = "/api/admin/style-iterations"
CSRF = {"X-SlimGuard-CSRF": "1"}


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "admin_username": "TEST-admin",
            "admin_password": "TEST-password",
            "zhipu_api_key": "TEST-model-key",
        }
    )
    app = create_app(settings, model_gateway=ScriptedModelGateway(()))
    async with app.router.lifespan_context(app):
        await StyleCorrectionFeedbackRepository(app.state.database).append(
            StyleCorrectionFeedbackInput(
                profile_version="TEST-doctor_v1",
                communication_act=None,
                scenario="TEST 新场景。",
                user_message="TEST 用户消息。",
                agent_response="TEST 旧回复。",
                desired_response="TEST 新回复。",
                deidentified_confirmed=True,
                expression_only_confirmed=True,
            ),
            actor="TEST-reviewer",
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/admin/auth/login",
        json={"username": "TEST-admin", "password": "TEST-password"},
    )
    assert response.status_code == 200


async def test_iteration_api_requires_authentication_and_csrf(api):
    payload = {
        "source_profile_version": "TEST-doctor_v1",
        "idempotency_key": "TEST-browser-request-1",
        "reviewed_inputs_confirmed": True,
    }
    assert (await api.get(f"{PREFIX}/context")).status_code == 401
    await login(api)
    assert (await api.post(PREFIX, json=payload)).status_code == 403
    created = await api.post(PREFIX, json=payload, headers=CSRF)
    assert created.status_code == 202
    run = created.json()
    assert run["target_version"] == "TEST-doctor_v2"
    repeated = await api.post(PREFIX, json=payload, headers=CSRF)
    assert repeated.json()["run_id"] == run["run_id"]

    context = (await api.get(f"{PREFIX}/context")).json()
    assert context["model_configured"] is True
    assert context["runtime"]["active_profile_version"] == "slimguard_default_v1"
    assert context["open_run"]["run_id"] == run["run_id"]
    runtime = await api.get("/api/admin/style-runtime")
    assert runtime.status_code == 200
    assert runtime.json()["fallback_profile_version"] == "slimguard_default_v1"
    assert runtime.json()["direct_activation_allowed"] is True
    assert (
        await api.post(
            "/api/admin/style-runtime/activate",
            json={
                "version": "TEST-doctor_v1",
                "expected_revision": 0,
                "reason": "TEST direct activation",
            },
        )
    ).status_code == 403
    assert (await api.get(PREFIX)).json()["total"] == 1
    assert (await api.get(f"{PREFIX}/{run['run_id']}")).status_code == 200
    events = (await api.get(f"{PREFIX}/{run['run_id']}/events")).json()["items"]
    assert [event["event_type"] for event in events] == ["run_created"]


async def test_iteration_api_cancel_is_append_only_admin_action(api):
    await login(api)
    created = await api.post(
        PREFIX,
        json={
            "source_profile_version": "TEST-doctor_v1",
            "idempotency_key": "TEST-browser-request-2",
            "reviewed_inputs_confirmed": True,
        },
        headers=CSRF,
    )
    run_id = created.json()["run_id"]
    cancelled = await api.post(
        f"{PREFIX}/{run_id}/cancel",
        json={"reason": "TEST 不再需要本次构建"},
        headers=CSRF,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    events = (await api.get(f"{PREFIX}/{run_id}/events")).json()["items"]
    assert events[-1]["event_type"] == "run_cancelled"
