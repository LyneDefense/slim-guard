from __future__ import annotations

import json

import httpx

from slim_guard.integrations.wecom_kf.client import WeComClient
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState


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
                    "msg_list": [
                        {
                            "msgid": "incoming-1",
                            "external_userid": "external-user",
                            "send_time": 1_700_000_000,
                            "origin": 3,
                            "msgtype": "text",
                            "text": {"content": "hello"},
                        }
                    ],
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
        if request.url.path == "/cgi-bin/kf/service_state/get":
            return httpx.Response(
                200,
                json={"errcode": 0, "service_state": 0},
            )
        if request.url.path == "/cgi-bin/kf/service_state/trans":
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "msg_code": "state-code"},
            )
        if request.url.path == "/cgi-bin/kf/send_msg_on_event":
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        if request.url.path == "/cgi-bin/kf/customer/batchget":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "customer_list": [
                        {
                            "external_userid": external_userid,
                            "nickname": f"name-{external_userid}",
                            "avatar": "https://example.com/avatar.png",
                            "gender": 1,
                        }
                        for external_userid in body["external_userid_list"]
                    ],
                    "invalid_external_userid": [],
                },
            )
        if request.url.path == "/cgi-bin/media/get":
            return httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nimage",
                headers={"content-type": "image/png"},
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
        state = await client.get_service_state(external_userid="external-user", open_kfid="wk-test")
        transition = await client.transition_service_state(
            external_userid="external-user",
            open_kfid="wk-test",
            service_state=WeComServiceState.SMART_ASSISTANT,
        )
        await client.send_event_text(
            code="state-code", content="session changed", msgid="event-msgid"
        )
        profiles = await client.get_customer_profiles(
            external_userids=["external-user", "external-user-2", "external-user"]
        )
        media = await client.download_media(media_id="media-1", max_bytes=1024)
    finally:
        await client.close()

    assert page.next_cursor == "next"
    assert page.msg_list[0].msgid == "incoming-1"
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
    assert state.service_state == 0
    assert transition.msg_code == "state-code"
    state_body = json.loads(requests[4].content)
    transition_body = json.loads(requests[5].content)
    event_body = json.loads(requests[6].content)
    profile_body = json.loads(requests[7].content)
    assert state_body == {
        "open_kfid": "wk-test",
        "external_userid": "external-user",
    }
    assert transition_body["service_state"] == 1
    assert event_body["code"] == "state-code"
    assert profile_body == {
        "external_userid_list": ["external-user", "external-user-2"],
        "need_enter_session_context": 0,
    }
    assert [profile.nickname for profile in profiles.customer_list] == [
        "name-external-user",
        "name-external-user-2",
    ]
    assert media.content == b"\x89PNG\r\n\x1a\nimage"
    assert media.content_type == "image/png"
    assert requests[8].url.params["media_id"] == "media-1"
