import pytest
from httpx import ASGITransport, AsyncClient

from slim_guard.main import create_app
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository


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
    assert (await api.get(base + "/examples", params={"role": "positive"})).json()["total"] == 0
    assert (await api.get(base + "/examples", params={"offset": -1})).status_code == 422
    assert (await api.post(base + "/builds", headers=headers)).status_code == 409
    other = await api.post("/api/admin/styles", json={"name": "简洁风格"}, headers=headers)
    assert other.status_code == 201
    other_id = other.json()["id"]
    assert (await api.get(f"/api/admin/styles/{other_id}/examples")).json()["total"] == 0
    assert (
        await api.put(
            f"/api/admin/styles/{other_id}/examples/{created.json()['id']}",
            json=payload,
            headers=headers,
        )
    ).status_code == 404
    assert (await api.get("/api/admin/style-ab/cases")).status_code == 404


async def test_build_process_api_hides_private_state_and_requires_csrf(test_settings):
    app = create_app(
        test_settings.model_copy(
            update={
                "admin_username": "tester",
                "admin_password": "test-style-password",
                "style_iteration_worker_enabled": False,
            }
        )
    )
    async with app.router.lifespan_context(app):
        db = app.state.database
        await CorpusRepository(db).append(
            "doctor",
            ExampleInput(
                user_input="谢谢",
                original_response="不用客气",
                desired_response="不客气",
            ),
            "tester",
        )
        run = await BuildRepository(db).build("doctor", "tester", model="test-model")
        base = f"/api/admin/styles/doctor/builds/{run['id']}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(base)).status_code == 401
            await client.post(
                "/api/admin/auth/login",
                json={
                    "username": "tester",
                    "password": "test-style-password",
                },
            )
            data = (await client.get(base)).json()
            assert data["material_count"] == 1 and data["unused_count"] == 1
            assert not {"worker_token", "snapshot", "checkpoints", "lease_until"} & data.keys()
            events = (await client.get(base + "/events")).json()
            assert events["next_sequence"] == 1
            assert (await client.get(base + "/events?after=-1")).status_code == 422
            artifact = (await client.get(base + "/artifacts?key=input_materials")).json()
            assert artifact["items"][0]["user_input"] == "谢谢"
            assert (await client.get(base + "/artifacts?key=model:private")).status_code == 404
            assert (await client.post(base + "/cancel")).status_code == 403
            assert (
                await client.post(base + "/cancel", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 200
            assert (
                await client.post(base + "/resume", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 200
