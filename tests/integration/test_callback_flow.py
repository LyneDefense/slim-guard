from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from slim_guard.config import Settings
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.integrations.wecom_kf.schemas import SyncMessage, SyncPage
from slim_guard.main import create_app
from tests.fakes import FakeWeComClient


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


async def test_callback_to_fixed_reply(test_settings: Settings) -> None:
    fake = FakeWeComClient(
        {
            None: SyncPage(
                next_cursor="done",
                has_more=False,
                msg_list=[
                    SyncMessage(
                        msgid="incoming-1",
                        open_kfid="wk-test",
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
    app: FastAPI = create_app(test_settings, client=fake)
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
            assert fake.sent[0].content == "收到，我已经连接成功。"


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
