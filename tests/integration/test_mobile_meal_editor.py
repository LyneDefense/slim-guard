import json
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.config import Settings
from slim_guard.db.models import (
    AgentItemRecord,
    AgentThreadRecord,
    AgentTurnRecord,
    AgentVersionRecord,
)
from slim_guard.domain.assets.contracts import SaveImageAssetCommand
from slim_guard.domain.assets.repository import ImageAssetRepository
from slim_guard.features.health_records.commands import CreateRecord, RecordWriteContext
from slim_guard.features.health_records.content import MealContent, MealItem
from slim_guard.features.health_records.occurrence import Occurrence
from slim_guard.main import create_app


async def test_mobile_record_photo_history_and_direct_edit_without_model(tmp_path):
    model = ScriptedModelGateway(())
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'mobile-edit.sqlite'}",
            mobile_api_enabled=True,
            mobile_auth_secret="meal-editor-secret-longer-than-32-characters",
            mobile_dev_otp_enabled=True,
            zhipu_api_key="test-configured",
            memory_extraction_enabled=False,
            memory_recall_enabled=False,
            routine_scheduler_enabled=False,
            log_level="WARNING",
        ),
        model_gateway=model,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:

            async def login(phone):
                challenge = (
                    await http.post("/api/mobile/v1/auth/otp/request", json={"phone": phone})
                ).json()
                issued = (
                    await http.post(
                        "/api/mobile/v1/auth/otp/verify",
                        json={
                            "challenge_id": challenge["challenge_id"],
                            "code": challenge["debug_code"],
                            "device_label": "test-device",
                        },
                    )
                ).json()
                return {"Authorization": f"Bearer {issued['access_token']}"}, issued["user"]["id"]

            headers, user_id = await login("13800138000")
            foreign_headers, _ = await login("13800138001")
            db = app.state.database
            async with db.session() as session, session.begin():
                session.add(
                    AgentVersionRecord(id="test-version", manifest_json="{}", code_revision="test")
                )
                await session.flush()
                session.add(AgentThreadRecord(id="test-thread", user_id=user_id))
                await session.flush()
                session.add(
                    AgentTurnRecord(
                        id="test-turn",
                        thread_id="test-thread",
                        agent_version_id="test-version",
                        trigger_type="user_message",
                    )
                )
                await session.flush()
                session.add(
                    AgentItemRecord(
                        id="test-message",
                        thread_id="test-thread",
                        turn_id="test-turn",
                        sequence=1,
                        item_type="user_message",
                        status="completed",
                        payload_json='{"text":"早餐"}',
                    )
                )
                await session.flush()
                session.add(
                    AgentItemRecord(
                        id="stored-message-id",
                        thread_id="test-thread",
                        turn_id="test-turn",
                        sequence=2,
                        item_type="agent_message",
                        status="completed",
                        payload_json=json.dumps(
                            {
                                "id": "canonical-notice-id",
                                "participant": "system_assistant",
                                "kind": "text",
                                "text": "请补充菜品。",
                                "notice_text": "这道菜叫什么？",
                                "requires_response": True,
                            }
                        ),
                    )
                )
            image_bytes = b"\x89PNG\r\n\x1a\nmeal-photo"
            photo = (
                await ImageAssetRepository(db).save(
                    SaveImageAssetCommand(
                        user_id=user_id,
                        content=image_bytes,
                        declared_mime_type="image/png",
                    )
                )
            ).asset.id
            record = (
                await app.state.mobile_service.records.write(
                    RecordWriteContext(
                        user_id=user_id,
                        thread_id="test-thread",
                        turn_id="test-turn",
                        source_item_id="test-message",
                        tool_call_id="create1",
                        operation_id="create1",
                    ),
                    CreateRecord(
                        content=MealContent(
                            meal_type="breakfast",
                            foods=(MealItem(item_id="f1", name="鸡肉炒菜"),),
                            photo_asset_ids=(photo,),
                        ),
                        occurrence=Occurrence(),
                    ),
                )
            ).record
            page = await http.get("/api/mobile/v1/records?kind=meal", headers=headers)
            assert page.status_code == 200
            assert page.json()["records"][0]["content"]["photo_asset_ids"] == [photo]
            image = await http.get(f"/api/mobile/v1/chat/assets/{photo}", headers=headers)
            assert image.content == image_bytes
            assert (
                await http.get(f"/api/mobile/v1/chat/assets/{photo}", headers=foreign_headers)
            ).status_code == 404
            payload = {
                "operation_id": str(uuid4()),
                "expected_revision": record.ref.revision,
                "meal_type": "dinner",
                "foods": [{"item_id": "f1", "name": "香菇炒鸡肉"}, {"name": "米饭"}],
            }
            url = f"/api/mobile/v1/records/meal/{record.ref.record_id}"
            assert (await http.patch(url, json=payload)).status_code == 401
            assert (await http.patch(url, json=payload, headers=foreign_headers)).status_code == 404
            saved = await http.patch(url, json=payload, headers=headers)
            assert saved.status_code == 200
            assert saved.json()["ref"]["revision"] == 2
            assert saved.json()["content"]["meal_type"] == "dinner"
            assert saved.json()["content"]["photo_asset_ids"] == [photo]
            assert [food["name"] for food in saved.json()["content"]["foods"]] == [
                "香菇炒鸡肉",
                "米饭",
            ]
            assert (await http.patch(url, json=payload, headers=headers)).json() == saved.json()
            assert (
                await http.patch(
                    url, json={**payload, "operation_id": str(uuid4())}, headers=headers
                )
            ).status_code == 409
            history = (await http.get("/api/mobile/v1/chat/messages", headers=headers)).json()[
                "items"
            ]
            notice = next(item for item in history if item["participant"] == "system_assistant")
            assert notice["id"] == "canonical-notice-id"
            assert notice["requires_response"] is True
            assert notice["notice_text"] == "这道菜叫什么？"
            assert not model.requests
