from httpx import ASGITransport, AsyncClient

from slim_guard.expression_style.package import Guide, make_package
from slim_guard.expression_style.trainer.contracts import WORKFLOW_VERSION
from slim_guard.group_chat import ROLE_POLICY_VERSION
from slim_guard.main import create_app
from slim_guard.style_management.models import ReviewCase, Style, Version


async def test_failed_preview_history_remains_readable_after_retry_and_publish_retirement(
    test_settings,
):
    app = create_app(
        test_settings.model_copy(
            update={
                "admin_username": "tester",
                "admin_password": "test-style-password",
            }
        )
    )
    async with app.router.lifespan_context(app):
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
                        "workflow_version": WORKFLOW_VERSION,
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
            cases_url = f"/api/admin/styles/doctor/versions/{package.version_id}/cases"
            assert (await client.get(cases_url)).status_code == 401
            await client.post(
                "/api/admin/auth/login",
                json={"username": "tester", "password": "test-style-password"},
            )
            assert (await client.post(endpoint)).status_code == 404
            headers = {"X-SlimGuard-CSRF": "1", "Idempotency-Key": "retry-one"}
            assert (
                await client.post(endpoint.replace("/doctor/", "/another/"), headers=headers)
            ).status_code == 404
            assert (await client.post(endpoint, headers=headers)).status_code == 404
            versions = (await client.get("/api/admin/styles/doctor/versions")).json()["items"]
            historical = next(row for row in versions if row["id"] == package.version_id)
            assert historical["review_summary"]["auto_failed"] == 1
            assert historical["package"]["package_hash"] == package.package_hash
            cases = (await client.get(cases_url)).json()
            assert cases["total"] == 1 and cases["counts"]["pending"] == 1
            assert cases["items"][0]["id"] == "retry-api-case"
            assert cases["items"][0]["doctor_response"] == "不客气。"
            assert (
                await client.get(cases_url.replace("/doctor/", "/another/"))
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/admin/styles/doctor/versions/{package.version_id}/publish",
                    headers=headers,
                )
            ).status_code == 404
        async with app.state.database.session() as session:
            assert (await session.get(Version, package.version_id)).status == "ready_for_review"
            assert (await session.get(ReviewCase, "retry-api-case")).review is None
            assert (await session.get(Style, "doctor")).active_version_id == "doctor_builtin_v1"
