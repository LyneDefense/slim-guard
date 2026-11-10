"""Exercise real studio composition with scripted provider responses, without network calls."""

import json

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import ModelMessage, ModelResponse
from slim_guard.main import create_app


def response(value):
    return ModelResponse(message=ModelMessage(role="assistant", content=json.dumps(value)))


async def test_studio_preview_and_publication_use_real_model_composition_without_user_writes(
    test_settings,
):
    gateway = ScriptedModelGateway(
        [
            response({"text": "来了。"}),
            response(
                {
                    "fidelity_passed": True,
                    "role_passed": True,
                    "expression_passed": True,
                    "issues": [],
                }
            ),
            response(
                {
                    "checks": [
                        {
                            "source_id": "persona",
                            "role_passed": True,
                            "safety_passed": True,
                            "issues": [],
                        }
                    ]
                }
            ),
        ]
    )
    settings = test_settings.model_copy(
        update={
            "admin_username": "operator",
            "admin_password": "studio-test-password",
            "zhipu_api_key": "",
            "nutrition_rag_engine": "v1",
            "memory_extraction_enabled": False,
        }
    )
    app = create_app(settings, model_gateway=gateway)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/admin/auth/login",
                json={
                    "username": "operator",
                    "password": "studio-test-password",
                },
            )
            headers = {"X-SlimGuard-CSRF": "1"}
            base = "/api/admin/styles/doctor/studio"
            studio = (await client.get(base)).json()
            draft = studio["draft"]
            before_active = studio["active_version"]["id"]
            content = {
                "expression": "自然、简短，必要风险说清楚。",
                "avoid": "不机械鼓励。",
                "examples": [],
            }
            # Unsaved editing content is used directly, without changing the saved draft.
            preview = await client.post(
                base + "/preview",
                headers=headers,
                json={
                    "content": content,
                    "user_input": "你好",
                    "history": [],
                },
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["text"] == "来了。"
            assert preview.json()["model_calls"] == 2
            assert (await client.get(base)).json()["draft"] == draft
            saved = await client.put(
                base + "/draft",
                headers=headers,
                json={
                    "revision": draft["revision"],
                    "content": content,
                },
            )
            assert saved.status_code == 200, saved.text
            draft = saved.json()
            assert (await client.get(base)).json()["active_version"]["id"] == before_active
            check = await client.post(
                base + "/check",
                headers=headers,
                json={
                    "revision": draft["revision"],
                    "content_hash": draft["content_hash"],
                },
            )
            assert check.status_code == 200, check.text
            assert check.json()["passed"]
            published = await client.post(
                base + "/publish",
                headers=headers,
                json={
                    "revision": draft["revision"],
                    "content_hash": draft["content_hash"],
                    "check_id": check.json()["id"],
                },
            )
            assert published.status_code == 200, published.text
            assert published.json()["version"]["id"] != before_active
        async with app.state.database.session() as session:
            for table in (
                "users",
                "agent_threads",
                "agent_turns",
                "agent_items",
                "agent_invocations",
                "user_memory_facts",
                "user_long_term_memories",
                "conversation_tasks",
            ):
                assert await session.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0
            # Usage remains observable, attributed to admin evaluation rather than a user.
            rows = (
                (
                    await session.execute(
                        text("SELECT category, user_id, thread_id FROM model_usage_calls")
                    )
                )
                .mappings()
                .all()
            )
            assert len(rows) == 3
            assert all(
                row["category"] == "evaluation"
                and row["user_id"] is None
                and row["thread_id"] is None
                for row in rows
            )
    gateway.assert_exhausted()
