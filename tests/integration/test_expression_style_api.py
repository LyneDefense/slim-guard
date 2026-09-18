import pytest
from httpx import ASGITransport, AsyncClient

from slim_guard.main import create_app


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "admin_username": "tester",
            "admin_password": "test-style-password",
            "style_iteration_worker_enabled": False,
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_style_api_auth_csrf_scoping_and_actual_corpus(api):
    assert (await api.get("/api/admin/styles")).status_code == 401
    assert (
        await api.post(
            "/api/admin/auth/login", json={"username": "tester", "password": "test-style-password"}
        )
    ).status_code == 200
    base = "/api/admin/styles/doctor"
    payload = {
        "user_input": "怎么称呼你？",
        "original_response": "叫我 SlimGuard。",
        "desired_response": "叫我 SlimGuard 就行。",
    }
    assert (await api.post(base + "/examples", json=payload)).status_code == 403
    headers = {"X-SlimGuard-CSRF": "1"}
    created = await api.post(base + "/examples", json=payload, headers=headers)
    assert created.status_code == 201
    assert created.json()["actor"] == "tester"
    assert (await api.get(base + "/examples")).json()["total"] == 1
    assert (await api.get(base + "/examples", params={"category": "expression"})).json()[
        "total"
    ] == 0
    assert (await api.get(base + "/examples", params={"offset": -1})).status_code == 422
    assert (await api.post(base + "/versions", headers=headers)).status_code == 409
    other = await api.post("/api/admin/styles", json={"name": "简洁风格"}, headers=headers)
    assert other.status_code == 201
    other_id = other.json()["id"]
    assert (await api.get(f"/api/admin/styles/{other_id}/examples")).json()["total"] == 0
    assert (
        await api.patch(
            f"/api/admin/styles/{other_id}/examples/{created.json()['id']}",
            json={"status": "excluded"},
            headers=headers,
        )
    ).status_code == 404
    assert (await api.get("/api/admin/style-ab/cases")).status_code == 404
