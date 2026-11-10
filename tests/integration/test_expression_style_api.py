import pytest
from httpx import ASGITransport, AsyncClient

from slim_guard.main import create_app
from slim_guard.style_management.builds import BuildRepository
from slim_guard.style_management.contracts import ExampleInput
from slim_guard.style_management.corpus import CorpusRepository
from slim_guard.style_management.models import BuildRun


@pytest.fixture
async def api(test_settings):
    settings = test_settings.model_copy(
        update={
            "admin_username": "tester",
            "admin_password": "test-style-password",
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_style_catalog_requires_auth_and_keeps_history_read_only(api):
    assert (await api.get("/api/admin/styles")).status_code == 401
    assert (
        await api.post(
            "/api/admin/auth/login", json={"username": "tester", "password": "test-style-password"}
        )
    ).status_code == 200
    base = "/api/admin/styles/doctor"
    assert (await api.get("/api/admin/styles")).json()["items"][0]["id"] == "doctor"
    assert (
        await api.post("/api/admin/styles", json={"name": "简洁风格"})
    ).status_code == 403
    headers = {"X-SlimGuard-CSRF": "1"}
    assert (await api.get(base + "/examples")).json()["total"] == 0
    assert (await api.get(base + "/examples", params={"role": "positive"})).json()["total"] == 0
    assert (await api.get(base + "/examples", params={"offset": -1})).status_code == 422
    # GET history remains, so FastAPI returns 405 for the removed same-path POSTs.
    assert (await api.post(base + "/examples", json={}, headers=headers)).status_code == 405
    assert (await api.post(base + "/builds", headers=headers)).status_code == 405
    for method, endpoint in (
        ("PUT", "/examples/historical-example"),
        ("DELETE", "/examples/historical-example"),
        ("POST", "/reviews/historical-case"),
        ("POST", "/builds/historical-build/cancel"),
        ("POST", "/builds/historical-build/resume"),
        ("POST", "/versions/doctor_builtin_v1/publish"),
        ("POST", "/versions/doctor_builtin_v1/activate"),
        ("POST", "/versions/doctor_builtin_v1/retry-failed"),
    ):
        response = await api.request(method, base + endpoint, json={}, headers=headers)
        assert response.status_code == 404, (method, endpoint, response.text)
    other = await api.post("/api/admin/styles", json={"name": "简洁风格"}, headers=headers)
    assert other.status_code == 201
    other_id = other.json()["id"]
    assert (await api.get(f"/api/admin/styles/{other_id}/examples")).json()["total"] == 0
    assert (await api.get("/api/admin/style-ab/cases")).status_code == 404


async def test_build_history_is_scoped_read_only_and_hides_private_state(test_settings):
    app = create_app(
        test_settings.model_copy(
            update={
                "admin_username": "tester",
                "admin_password": "test-style-password",
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
        diagnostic_key = "validation:Comparison:test"
        diagnostic = {
            "schema_name": "Comparison",
            "status": "unresolved",
            "failures": [{"raw_output": "private-model-output", "errors": [{"path": "winner"}]}],
        }
        async with db.session() as session, session.begin():
            row = await session.get(BuildRun, run["id"])
            row.checkpoints = {
                diagnostic_key: diagnostic,
                "evidence_catalog": [
                    {"ref": f"e{i}", "quote": "private-frozen-source", "field": "desired_response"}
                    for i in range(24)
                ],
            }
        base = f"/api/admin/styles/doctor/builds/{run['id']}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(base)).status_code == 401
            assert (
                await client.get(base + "/artifacts", params={"key": diagnostic_key})
            ).status_code == 401
            assert (
                await client.get(base + "/artifacts", params={"key": "evidence_catalog"})
            ).status_code == 401
            await client.post(
                "/api/admin/auth/login",
                json={
                    "username": "tester",
                    "password": "test-style-password",
                },
            )
            data = (await client.get(base)).json()
            assert data["material_count"] == 1 and data["unused_count"] == 1
            assert data["retired"] is True and data["can_resume"] is False
            assert data["resume_block_reason"] == "workflow_retired"
            assert not {"worker_token", "snapshot", "checkpoints", "lease_until"} & data.keys()
            assert diagnostic_key in data["artifacts"]
            assert "private-model-output" not in str(data)
            assert "private-frozen-source" not in str(data)
            catalog = await client.get(base + "/artifacts", params={"key": "evidence_catalog"})
            assert catalog.status_code == 200
            assert len(catalog.json()["items"]) == 20 and catalog.json()["total"] == 24
            remainder = await client.get(
                base + "/artifacts", params={"key": "evidence_catalog", "offset": 20}
            )
            assert len(remainder.json()["items"]) == 4
            detailed = await client.get(base + "/artifacts", params={"key": diagnostic_key})
            assert detailed.status_code == 200 and detailed.json()["value"] == diagnostic
            wrong_scope = base.replace("/doctor/", "/another-style/")
            assert (
                await client.get(wrong_scope + "/artifacts", params={"key": diagnostic_key})
            ).status_code == 404
            assert (
                await client.get(wrong_scope + "/artifacts", params={"key": "evidence_catalog"})
            ).status_code == 404
            events = (await client.get(base + "/events")).json()
            assert events["next_sequence"] == 1
            assert (await client.get(base + "/events?after=-1")).status_code == 422
            artifact = (await client.get(base + "/artifacts?key=input_materials")).json()
            assert artifact["items"][0]["user_input"] == "谢谢"
            assert (await client.get(base + "/artifacts?key=model:private")).status_code == 404
            examples = (await client.get("/api/admin/styles/doctor/examples")).json()
            assert examples["total"] == 1
            assert examples["items"][0]["desired_response"] == "不客气"
            listed = (await client.get("/api/admin/styles/doctor/builds")).json()
            assert listed["items"][0]["id"] == run["id"]
            assert listed["items"][0]["retired"] is True
            assert (await client.post(base + "/cancel")).status_code == 404
            assert (
                await client.post(base + "/cancel", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 404
            assert (
                await client.post(base + "/resume", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 404
        async with db.session() as session:
            assert (await session.get(BuildRun, run["id"])).status == "queued"


def test_openapi_exposes_catalog_and_read_only_legacy_history(test_settings):
    paths = create_app(test_settings).openapi()["paths"]
    legacy = {
        path: set(operations)
        for path, operations in paths.items()
        if path.startswith("/api/admin/styles") and "/studio" not in path
    }
    assert legacy.pop("/api/admin/styles") == {"get", "post"}
    assert set(legacy) == {
        "/api/admin/styles/{style_id}/examples",
        "/api/admin/styles/{style_id}/builds",
        "/api/admin/styles/{style_id}/builds/{run_id}",
        "/api/admin/styles/{style_id}/builds/{run_id}/events",
        "/api/admin/styles/{style_id}/builds/{run_id}/artifacts",
        "/api/admin/styles/{style_id}/versions",
        "/api/admin/styles/{style_id}/versions/{version_id}/cases",
    }
    assert all(methods == {"get"} for methods in legacy.values())
