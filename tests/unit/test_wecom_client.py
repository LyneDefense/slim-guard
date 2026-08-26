from __future__ import annotations

import json

import httpx

from slim_guard.integrations.wecom_kf.client import WeComClient


async def test_client_caches_token_and_builds_kf_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200,
                json={"errcode": 0, "access_token": "access-token", "expires_in": 7200},
            )
        if request.url.path == "/cgi-bin/kf/sync_msg":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "next_cursor": "next",
                    "has_more": 0,
                    "msg_list": [],
                },
            )
        if request.url.path == "/cgi-bin/kf/send_msg":
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        if request.url.path == "/cgi-bin/kf/account/list":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "account_list": [
                        {
                            "open_kfid": "wk-test",
                            "name": "减脂助手",
                            "avatar": "https://example.com/avatar.png",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = WeComClient(
        corp_id="ww-test",
        secret="secret",
        base_url="https://qyapi.weixin.qq.com",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    try:
        page = await client.sync_messages(
            callback_token="callback-token",
            open_kfid="wk-test",
            cursor=None,
        )
        await client.send_text(
            external_userid="external-user",
            open_kfid="wk-test",
            content="fixed reply",
            msgid="stable-msgid",
        )
        accounts = await client.list_accounts()
    finally:
        await client.close()

    assert page.next_cursor == "next"
    assert [request.url.path for request in requests].count("/cgi-bin/gettoken") == 1
    sync_body = json.loads(requests[1].content)
    send_body = json.loads(requests[2].content)
    assert sync_body == {
        "token": "callback-token",
        "limit": 1000,
        "voice_format": 0,
        "open_kfid": "wk-test",
    }
    assert send_body["touser"] == "external-user"
    assert send_body["msgid"] == "stable-msgid"
    assert requests[1].url.params["access_token"] == "access-token"
    assert [(account.name, account.open_kfid) for account in accounts] == [("减脂助手", "wk-test")]
