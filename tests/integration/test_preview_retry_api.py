from httpx import ASGITransport, AsyncClient

from slim_guard.expression_style.package import Guide, make_package
from slim_guard.group_chat import ROLE_POLICY_VERSION
from slim_guard.main import create_app
from slim_guard.style_management.models import ReviewCase, Version


async def test_retry_endpoint_is_scoped_csrf_protected_idempotent_and_does_not_publish(
    test_settings,
):
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
        app.state.settings = app.state.settings.model_copy(
            update={
                "style_iteration_worker_enabled": True,
                "zhipu_api_key": "test-only",
            }
        )
        package = make_package("doctor", "retry-api-v", "重试测试", Guide(summary="不改变原意"))
        async with app.state.database.session() as session, session.begin():
            session.add(
                Version(
                    id=package.version_id,
                    style_id="doctor",
                    name="重试 API 测试",
                    actor="test",
                    package=package.model_dump(mode="json"),
                    report={
                        "schema_version": "2",
                        "role_policy_version": ROLE_POLICY_VERSION,
                        "package_hash": package.package_hash,
                        "preview_metrics": {"total": 1},
                        "release_eligible": False,
                    },
                )
            )
            await session.flush()
            session.add(
                ReviewCase(
                    id="retry-api-case",
                    version_id=package.version_id,
                    test_case={
                        "id": "synthetic",
                        "family": "synthetic",
                        "user_input": "谢谢",
                        "source_text": "不客气。",
                        "context": [],
                        "protected_literals": [],
                    },
                    user_input="谢谢",
                    original_response="不客气。",
                    doctor_response="不客气。",
                    baseline_response="不客气。",
                    automated={
                        "passed": False,
                        "fallback": True,
                        "package_hash": package.package_hash,
                    },
                )
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            endpoint = f"/api/admin/styles/doctor/versions/{package.version_id}/retry-failed"
            assert (await client.post(endpoint)).status_code == 401
            await client.post(
                "/api/admin/auth/login",
                json={"username": "tester", "password": "test-style-password"},
            )
            assert (await client.post(endpoint)).status_code == 403
            assert (
                await client.post(
                    endpoint, headers={"X-SlimGuard-CSRF": "1", "Idempotency-Key": " "}
                )
            ).status_code == 422
            headers = {"X-SlimGuard-CSRF": "1", "Idempotency-Key": "retry-one"}
            assert (
                await client.post(endpoint.replace("/doctor/", "/another/"), headers=headers)
            ).status_code == 404
            response = await client.post(endpoint, headers=headers)
            assert response.status_code == 202, response.text
            job = response.json()
            assert job["job_kind"] == "preview_retry" and job["preview_count"] == 1
            assert not {"snapshot", "checkpoints", "worker_token"} & job.keys()
            assert (await client.post(endpoint, headers=headers)).json()["id"] == job["id"]
            assert (
                await client.post(endpoint, headers={"X-SlimGuard-CSRF": "1"})
            ).status_code == 409
            base = f"/api/admin/styles/doctor/builds/{job['id']}"
            data = (await client.get(base + "/artifacts?key=retry_inputs")).json()
            assert data["items"][0]["case_id"] == "retry-api-case"
            assert (
                await client.post(
                    f"/api/admin/styles/doctor/versions/{package.version_id}/publish",
                    headers=headers,
                )
            ).status_code == 409
            assert (await client.post(base + "/cancel", headers=headers)).status_code == 200
