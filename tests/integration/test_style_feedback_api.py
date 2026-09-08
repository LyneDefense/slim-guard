"""Authenticated API checks use invented style corrections only."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slim_guard.db.models import AdminAuditEventRecord, StyleCorrectionFeedbackRecord
from slim_guard.main import create_app

PREFIX = "/api/admin/style-feedback"
CSRF = {"X-SlimGuard-CSRF": "1"}
PAYLOAD = {
    "profile_version": "TEST-style-v3",
    "communication_act": None,
    "scenario": "TEST 新场景，用户询问三天记录能否代表长期趋势。",
    "user_message": "TEST 这三天能看出长期趋势吗？",
    "agent_response": "TEST 目前信息有限，请继续保持记录。",
    "desired_response": "TEST 只有三天记录，判断不了长期趋势。",
    "guidance_note": "TEST 直接说明原因。",
    "deidentified_confirmed": True,
    "expression_only_confirmed": True,
}


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "wecom_corp_id": "",
            "wecom_kf_secret": "",
            "wecom_open_kf_id": "",
            "wecom_callback_token": "",
            "wecom_callback_aes_key": "",
            "admin_username": "TEST-style-reviewer",
            "admin_password": "TEST-password-not-real",
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, app


async def login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/admin/auth/login",
        json={
            "username": "TEST-style-reviewer",
            "password": "TEST-password-not-real",
        },
    )
    assert response.status_code == 200


@pytest.mark.parametrize("operation", ["context", "list", "append"])
async def test_style_feedback_endpoints_require_admin_authentication(api, operation):
    client, _ = api
    if operation == "append":
        response = await client.post(PREFIX, json=PAYLOAD, headers=CSRF)
    else:
        response = await client.get(PREFIX + ("/context" if operation == "context" else ""))
    assert response.status_code == 401


async def test_named_feedback_is_appended_and_returned_with_development_rollout_context(api):
    client, app = api
    await login(client)
    context = await client.get(f"{PREFIX}/context")
    assert context.status_code == 200
    assert context.json()["runtime_default_profile_version"] == "slimguard_default_v1"
    assert context.json()["development_direct_rollout"] is True

    result = await client.post(PREFIX, json=PAYLOAD, headers=CSRF)
    assert result.status_code == 201
    created = result.json()
    assert created["actor"] == "TEST-style-reviewer"
    assert created["communication_act"] is None
    assert len(created["content_sha256"]) == 64

    listing = await client.get(PREFIX, params={"profile_version": "TEST-style-v3"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0] == created
    after = (await client.get(f"{PREFIX}/context")).json()
    assert after["profile_versions"] == ["TEST-style-v3"]
    assert after["suggested_profile_version"] == "TEST-style-v3"

    async with app.state.database.session() as session:
        records = tuple(await session.scalars(select(StyleCorrectionFeedbackRecord)))
        audits = tuple(await session.scalars(select(AdminAuditEventRecord)))
    assert len(records) == 1
    assert records[0].actor == "TEST-style-reviewer"
    assert {audit.action for audit in audits} == {"append_feedback", "list"}


@pytest.mark.parametrize("headers", [{}, {"X-SlimGuard-CSRF": "0"}])
async def test_feedback_requires_csrf_header(api, headers):
    client, _ = api
    await login(client)
    assert (await client.post(PREFIX, json=PAYLOAD, headers=headers)).status_code == 403
    assert (await client.get(PREFIX)).json()["total"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"actor": "forged-reviewer"},
        {"deidentified_confirmed": False},
        {"expression_only_confirmed": False},
        {"communication_act": "unknown"},
        {"desired_response": PAYLOAD["agent_response"]},
        {"scenario": " "},
    ],
)
async def test_client_identity_and_invalid_feedback_are_rejected(api, changes):
    client, _ = api
    await login(client)
    response = await client.post(PREFIX, json={**PAYLOAD, **changes}, headers=CSRF)
    assert response.status_code == 422
    assert (await client.get(PREFIX)).json()["total"] == 0


async def test_feedback_has_no_http_mutation_or_activation_routes(api):
    client, _ = api
    await login(client)
    created = (await client.post(PREFIX, json=PAYLOAD, headers=CSRF)).json()
    item_url = f"{PREFIX}/{created['feedback_id']}"
    assert (await client.put(item_url, json={})).status_code == 404
    assert (await client.patch(item_url, json={})).status_code == 404
    assert (await client.delete(item_url)).status_code == 404
    assert (await client.post(f"{PREFIX}/activate", json={}, headers=CSRF)).status_code == 404
