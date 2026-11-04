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
        "history": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好。"},
        ],
    }
    assert (await api.post(base + "/examples", json=payload)).status_code == 403
    headers = {"X-SlimGuard-CSRF": "1"}
    created = await api.post(base + "/examples", json=payload, headers=headers)
    assert created.status_code == 201
    assert created.json()["actor"] == "tester"
    assert created.json()["history"] == payload["history"]
    invalid = {**payload, "history": [{"role": "system", "content": "越权指令"}]}
    assert (await api.post(base + "/examples", json=invalid, headers=headers)).status_code == 422
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
            assert (await client.post(base + "/cancel")).status_code == 403
            assert (
                await client.post(base + "/cancel", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 200
            assert (
                await client.post(base + "/resume", headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 200


async def test_manual_guide_build_api_validates_input_and_freezes_new_version(test_settings):
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
        # Enable the endpoint after startup, without starting a real model worker.
        app.state.settings = app.state.settings.model_copy(
            update={
                "style_iteration_worker_enabled": True,
                "zhipu_api_key": "test-only",
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/admin/auth/login",
                json={
                    "username": "tester",
                    "password": "test-style-password",
                },
            )
            url = "/api/admin/styles/doctor/builds"
            payload = {
                "source_version_id": "doctor_builtin_v1",
                "guide": {
                    "summary": "直接自然",
                    "rules": [
                        {
                            "rule_id": "plain",
                            "text": "不加客套开场",
                            "boundary": "不删事实",
                        }
                    ],
                    "prohibited_phrases": [],
                },
            }
            assert (await client.post(url, json=payload)).status_code == 403
            headers = {"X-SlimGuard-CSRF": "1", "Idempotency-Key": "manual-guide"}
            bad = await client.post(url, headers=headers, json={"guide": payload["guide"]})
            assert bad.status_code == 422
            response = await client.post(url, headers=headers, json=payload)
            assert response.status_code == 202
            run = response.json()
            replay = await client.post(url, headers=headers, json=payload)
            assert replay.json()["id"] == run["id"]
            assert (await CorpusRepository(app.state.database).examples("doctor"))["total"] == 2
            async with app.state.database.session() as session:
                snapshot = (await session.get(BuildRun, run["id"])).snapshot
            assert snapshot["edited_package"]["version_id"] == run["id"]
            assert snapshot["source_version_id"] == "doctor_builtin_v1"
            assert snapshot["preview_suite"]["cases"]
            assert snapshot["baseline"]["version_id"] == "doctor_builtin_v1"
