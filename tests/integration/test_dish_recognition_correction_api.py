from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slim_guard.agents.contracts import AgentArtifact, ArtifactProducerRole
from slim_guard.db.models import (
    AdminAuditEventRecord,
    AgentArtifactRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
    InteractionTraceRecord,
    SlimGuardUser,
)
from slim_guard.main import create_app
from slim_guard.orchestration.repository import OrchestrationRepository

NOW = datetime(2026, 9, 10, tzinfo=UTC)
TRACE_ID = "00000000-0000-0000-0000-000000000010"
USER_ID = "00000000-0000-0000-0000-000000000020"
ARTIFACT_ID = "dish-recognition-test"
CSRF = {"X-SlimGuard-CSRF": "1"}
CORRECTION = {
    "corrected_dishes": [
        {"dish_ref": "dish-1", "corrected_name": "地三鲜"},
    ],
    "comment": "实际菜品由现场标签确认。",
}


def correction_path() -> str:
    return (
        f"/api/admin/users/{USER_ID}/traces/{TRACE_ID}/dish-recognition-corrections/{ARTIFACT_ID}"
    )


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "wecom_corp_id": "",
            "wecom_kf_secret": "",
            "wecom_open_kf_id": "",
            "wecom_callback_token": "",
            "wecom_callback_aes_key": "",
            "admin_username": "TEST-dish-reviewer",
            "admin_password": "TEST-password-not-real",
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.session() as session, session.begin():
            session.add_all(
                (
                    SlimGuardUser(id=USER_ID, first_seen_at=NOW, last_seen_at=NOW),
                    AgentVersionRecord(
                        id="dish-version",
                        manifest_json="{}",
                        code_revision="test",
                        created_at=NOW,
                    ),
                )
            )
        async with app.state.database.session() as session, session.begin():
            session.add(
                AgentThreadRecord(
                    id="dish-thread",
                    user_id=USER_ID,
                    created_at=NOW,
                    last_active_at=NOW,
                )
            )
        async with app.state.database.session() as session, session.begin():
            session.add(
                AgentTurnRecord(
                    id="dish-turn",
                    thread_id="dish-thread",
                    agent_version_id="dish-version",
                    trigger_type="user_message",
                    status="completed",
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=NOW,
                )
            )
            session.add(
                InteractionTraceRecord(
                    id=TRACE_ID,
                    user_id=USER_ID,
                    trigger_type="user_message",
                    agent_turn_id="dish-turn",
                    agent_version_id="dish-version",
                    reply_kind="text",
                    generation_status="completed",
                    delivery_status="accepted",
                    created_at=NOW,
                    updated_at=NOW,
                    completed_at=NOW,
                )
            )
        recognition = AgentArtifact.create(
            artifact_id=ARTIFACT_ID,
            turn_id="dish-turn",
            producer_role=ArtifactProducerRole.DISH_RECOGNITION,
            artifact_type="dish_recognition",
            schema_version="1",
            payload={
                "schema_version": "1",
                "asset_id": "asset-1",
                "model": "test-vision",
                "prompt_version": "test-prompt",
                "policy_version": "test-policy",
                "image_kind": "meal",
                "quality_flags": [],
                "dishes": [
                    {
                        "dish_ref": "dish-1",
                        "candidates": [
                            {"label": "红烧茄子", "confidence": 0.68},
                            {"label": "地三鲜", "confidence": 0.63},
                        ],
                        "visible_ingredients": ["茄子"],
                        "preparation_candidates": [],
                        "uncertainty_reasons": ["外观相似"],
                        "requires_confirmation": True,
                    }
                ],
                "suggested_question": "这是红烧茄子还是地三鲜？",
                "overall_requires_confirmation": True,
                "provider_request_id": "test-request",
            },
            created_at=NOW,
        )
        await OrchestrationRepository(app.state.database).append_artifact(recognition)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, app


async def login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/admin/auth/login",
        json={
            "username": "TEST-dish-reviewer",
            "password": "TEST-password-not-real",
        },
    )
    assert response.status_code == 200


async def test_dish_correction_requires_authentication_and_csrf(api) -> None:
    client, _ = api
    path = correction_path()
    assert (await client.post(path, json=CORRECTION, headers=CSRF)).status_code == 401
    await login(client)
    assert (await client.post(path, json=CORRECTION)).status_code == 403


async def test_dish_correction_appends_audited_artifact_without_overwriting_source(api) -> None:
    client, app = api
    await login(client)
    path = correction_path()
    response = await client.post(path, json=CORRECTION, headers=CSRF)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["artifact_type"] == "dish_recognition_correction"
    assert created["parent_artifact_ids"] == [ARTIFACT_ID]
    assert created["payload"]["reviewer"] == "TEST-dish-reviewer"

    trace = await client.get(f"/api/admin/users/{USER_ID}/traces/{TRACE_ID}")
    assert trace.status_code == 200
    artifacts = trace.json()["workflow"]["artifacts"]
    assert [item["artifact_type"] for item in artifacts] == [
        "dish_recognition",
        "dish_recognition_correction",
    ]
    assert artifacts[0]["payload"]["dishes"][0]["candidates"][0]["label"] == "红烧茄子"
    assert artifacts[1]["payload"]["corrected_dishes"][0]["corrected_name"] == "地三鲜"
    assert artifacts[1]["payload"]["comment_present"] is True

    async with app.state.database.session() as session:
        artifact_rows = tuple(await session.scalars(select(AgentArtifactRecord)))
        audits = tuple(await session.scalars(select(AdminAuditEventRecord)))
    assert len(artifact_rows) == 2
    assert {row.id for row in artifact_rows} == {ARTIFACT_ID, created["artifact_id"]}
    assert "append_dish_recognition_correction" in {item.action for item in audits}


async def test_dish_correction_rejects_missing_dish_reference(api) -> None:
    client, _ = api
    await login(client)
    path = correction_path()
    response = await client.post(
        path,
        json={
            "corrected_dishes": [{"dish_ref": "dish-2", "corrected_name": "地三鲜"}],
            "comment": "错误引用测试",
        },
        headers=CSRF,
    )
    assert response.status_code == 422
