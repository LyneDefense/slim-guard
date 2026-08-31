from __future__ import annotations

import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from slim_guard.agent_models.fake import ScriptedModelGateway
from slim_guard.agent_models.gateway import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NormalizedToolCall,
)
from slim_guard.agent_models.vision import (
    VisionInspectionRequest,
    VisionInspectionResponse,
)
from slim_guard.config import Settings
from slim_guard.db.models import InteractionTraceRecord, TraceSpanRecord
from slim_guard.db.repositories import MessageRepository
from slim_guard.domain.weight.repository import WeightRepository
from slim_guard.harness.repository import AgentVersionRepository
from slim_guard.integrations.wecom_kf.client import WeComMedia
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.integrations.wecom_kf.schemas import SyncMessage, SyncPage
from slim_guard.main import create_app
from tests.fakes import FakeReplyAgent, FakeWeComClient


class ImageHarnessModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = len(self.requests)
        if step == 1:
            image_input = json.loads(request.messages[-1].content or "")
            return _tool_response(
                "call-inspect",
                "inspect_image",
                {"asset_id": image_input["asset_id"], "focus": "weight_scale"},
            )
        if step == 2:
            return _tool_response(
                "call-record",
                "record_weight",
                {"value": 77.6, "unit": "kg", "condition": "unspecified"},
            )
        if step == 3:
            return _tool_response(
                "call-trend",
                "get_recent_weight_trend",
                {"limit": 7},
            )
        return ModelResponse(
            message=ModelMessage(
                role=MessageRole.ASSISTANT,
                content="体重秤显示 77.6kg，已记录为第一条体重基线。",
            ),
            finish_reason="stop",
        )

    async def close(self) -> None:
        self.closed = True


class ImageHarnessVision:
    def __init__(self) -> None:
        self.requests: list[VisionInspectionRequest] = []
        self.closed = False

    async def inspect(self, request: VisionInspectionRequest) -> VisionInspectionResponse:
        self.requests.append(request)
        return VisionInspectionResponse(description="体重秤显示 77.6 kg，单位清晰。")

    async def close(self) -> None:
        self.closed = True


def _tool_response(
    call_id: str,
    name: str,
    arguments: dict[str, object],
) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                NormalizedToolCall(id=call_id, name=name, arguments=arguments),
            ),
        ),
        finish_reason="tool_calls",
    )


def _encrypted_callback(
    crypto: WeComCallbackCrypto,
    *,
    timestamp: str,
    nonce: str,
) -> tuple[bytes, str]:
    plaintext = b"""
    <xml>
      <MsgType><![CDATA[event]]></MsgType>
      <Event><![CDATA[kf_msg_or_event]]></Event>
      <Token><![CDATA[callback-sync-token]]></Token>
      <OpenKfId><![CDATA[wk-test]]></OpenKfId>
    </xml>
    """
    encrypted = crypto.encrypt(plaintext, random_bytes=b"0123456789abcdef")
    signature = crypto.signature(timestamp, nonce, encrypted)
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    return body, signature


async def test_callback_to_agent_reply(test_settings: Settings) -> None:
    fake = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="incoming-1",
                        external_userid="external-user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="text",
                        text={"content": "hello"},
                    )
                ],
            ),
            "done": SyncPage(next_cursor="done", has_more=False, msg_list=[]),
        }
    )
    reply_agent = FakeReplyAgent("收到，这是 AI 回复。")
    app: FastAPI = create_app(test_settings, client=fake, reply_agent=reply_agent)
    crypto = WeComCallbackCrypto(
        test_settings.wecom_callback_token,
        test_settings.wecom_callback_aes_key,
        test_settings.wecom_corp_id,
    )
    body, signature = _encrypted_callback(crypto, timestamp="123", nonce="nonce")

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.post(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                },
                content=body,
                headers={"content-type": "application/xml"},
            )
            duplicate = await http.post(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                },
                content=body,
                headers={"content-type": "application/xml"},
            )

            assert response.status_code == 200
            assert response.text == "success"
            assert duplicate.status_code == 200
            assert len(fake.sent) == 1
            assert fake.sent[0].content == "收到，这是 AI 回复。"
            assert [item.text for item in reply_agent.requests] == ["hello"]


async def test_callback_url_verification(test_settings: Settings) -> None:
    callback_only_settings = test_settings.model_copy(
        update={"wecom_kf_secret": "", "wecom_open_kf_id": ""}
    )
    app = create_app(callback_only_settings)
    crypto = WeComCallbackCrypto(
        callback_only_settings.wecom_callback_token,
        callback_only_settings.wecom_callback_aes_key,
        callback_only_settings.wecom_corp_id,
    )
    encrypted = crypto.encrypt(b"verified", random_bytes=b"0123456789abcdef")
    signature = crypto.signature("123", "nonce", encrypted)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                    "echostr": encrypted,
                },
            )

    assert response.status_code == 200
    assert response.text == "verified"


async def test_callback_runs_harness_weight_loop(test_settings: Settings) -> None:
    harness_settings = test_settings.model_copy(
        update={"agent_runtime_mode": "harness"}
    )
    fake = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="weight-message-1",
                        external_userid="external-user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="text",
                        text={"content": "今天空腹 77.6kg"},
                    )
                ],
            )
        }
    )
    model = ScriptedModelGateway(
        (
            _tool_response(
                "call-record",
                "record_weight",
                {"value": 77.6, "unit": "kg", "condition": "fasting"},
            ),
            _tool_response(
                "call-trend",
                "get_recent_weight_trend",
                {"limit": 7},
            ),
            ModelResponse(
                message=ModelMessage(
                    role=MessageRole.ASSISTANT,
                    content="已记录空腹体重 77.6kg，这是你的第一条体重基线。",
                ),
                finish_reason="stop",
            ),
        )
    )
    app = create_app(harness_settings, client=fake, model_gateway=model)
    crypto = WeComCallbackCrypto(
        harness_settings.wecom_callback_token,
        harness_settings.wecom_callback_aes_key,
        harness_settings.wecom_corp_id,
    )
    body, signature = _encrypted_callback(crypto, timestamp="123", nonce="nonce")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http:
            response = await http.post(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                },
                content=body,
                headers={"content-type": "application/xml"},
            )

        users = await MessageRepository(app.state.database).list_users()
        trend = await WeightRepository(app.state.database).recent_trend(users[0].id)
        stored_manifest = await AgentVersionRepository(app.state.database).get(
            app.state.agent_manifest.version_id
        )
        async with app.state.database.session() as session:
            trace = await session.scalar(select(InteractionTraceRecord))
            assert trace is not None
            spans = tuple(
                await session.scalars(
                    select(TraceSpanRecord)
                    .where(TraceSpanRecord.trace_id == trace.id)
                    .order_by(TraceSpanRecord.sequence)
                )
            )

        assert response.status_code == 200
        assert [sent.content for sent in fake.sent] == [
            "已记录空腹体重 77.6kg，这是你的第一条体重基线。"
        ]
        assert app.state.agent_runtime is not None
        assert stored_manifest is not None
        assert len(trend.records) == 1
        assert trend.records[0].weight_grams == 77_600
        assert trace.agent_turn_id == trend.records[0].source_turn_id
        assert trace.generation_status == "succeeded"
        assert trace.delivery_status == "accepted"
        assert {span.operation for span in spans} >= {
            "message_ingested",
            "ensure_agent_control",
            "generate_reply",
            "turn_finished",
            "send_text",
        }
        model.assert_exhausted()

    assert model.closed is False
    await model.close()


async def test_callback_delivers_bulk_memory_clear_confirmation(
    test_settings: Settings,
) -> None:
    harness_settings = test_settings.model_copy(
        update={"agent_runtime_mode": "harness"}
    )
    fake = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="clear-memory-message-1",
                        external_userid="external-user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="text",
                        text={"content": "清空我的个性化记忆"},
                    )
                ],
            )
        }
    )
    model = ScriptedModelGateway(
        (
            _tool_response(
                "call-clear",
                "clear_user_memories",
                {
                    "scope": "profile_goal_constraint",
                    "evidence_excerpt": "清空我的个性化记忆",
                },
            ),
        )
    )
    app = create_app(harness_settings, client=fake, model_gateway=model)
    crypto = WeComCallbackCrypto(
        harness_settings.wecom_callback_token,
        harness_settings.wecom_callback_aes_key,
        harness_settings.wecom_corp_id,
    )
    body, signature = _encrypted_callback(crypto, timestamp="123", nonce="nonce")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http:
            response = await http.post(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                },
                content=body,
                headers={"content-type": "application/xml"},
            )

        assert response.status_code == 200
        assert len(fake.sent) == 1
        assert "需要你再次明确确认" in fake.sent[0].content
        assert "确认执行" in fake.sent[0].content
        model.assert_exhausted()

    await model.close()


async def test_image_callback_runs_vision_then_records_weight(
    test_settings: Settings,
) -> None:
    harness_settings = test_settings.model_copy(
        update={"agent_runtime_mode": "harness"}
    )
    image = b"\x89PNG\r\n\x1a\nscale-image"
    fake = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="scale-image-message-1",
                        external_userid="external-user-1",
                        send_time=1_700_000_000,
                        origin=3,
                        msgtype="image",
                        image={"media_id": "scale-media-1"},
                    )
                ],
            )
        },
        media={
            "scale-media-1": WeComMedia(
                content=image,
                content_type="image/png",
            )
        },
    )
    model = ImageHarnessModel()
    vision = ImageHarnessVision()
    app = create_app(
        harness_settings,
        client=fake,
        model_gateway=model,
        vision_gateway=vision,
    )
    crypto = WeComCallbackCrypto(
        harness_settings.wecom_callback_token,
        harness_settings.wecom_callback_aes_key,
        harness_settings.wecom_corp_id,
    )
    body, signature = _encrypted_callback(crypto, timestamp="123", nonce="nonce")

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as http:
            response = await http.post(
                "/callbacks/wecom/kf",
                params={
                    "msg_signature": signature,
                    "timestamp": "123",
                    "nonce": "nonce",
                },
                content=body,
                headers={"content-type": "application/xml"},
            )

        users = await MessageRepository(app.state.database).list_users()
        trend = await WeightRepository(app.state.database).recent_trend(users[0].id)

        assert response.status_code == 200
        assert [sent.content for sent in fake.sent] == [
            "体重秤显示 77.6kg，已记录为第一条体重基线。"
        ]
        assert len(vision.requests) == 1
        assert vision.requests[0].image_bytes == image
        assert vision.requests[0].image_mime_type == "image/png"
        assert len(trend.records) == 1
        assert trend.records[0].weight_grams == 77_600
        assert len(model.requests) == 4

    assert model.closed is False
    assert vision.closed is False
    await model.close()
    await vision.close()
