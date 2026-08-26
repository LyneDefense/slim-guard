from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import httpx

from slim_guard.integrations.wecom_kf.errors import WeComAPIError, WeComTransportError
from slim_guard.integrations.wecom_kf.schemas import (
    KfAccount,
    ServiceStateSnapshot,
    ServiceStateTransition,
    SyncPage,
)
from slim_guard.integrations.wecom_kf.service_state import WeComServiceState

TOKEN_INVALID_ERROR_CODES = {40014, 42001, 42007, 42009}


class WeComClientProtocol(Protocol):
    async def sync_messages(
        self,
        *,
        callback_token: str,
        open_kfid: str,
        cursor: str | None,
    ) -> SyncPage: ...

    async def send_text(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        content: str,
        msgid: str,
    ) -> None: ...

    async def get_service_state(
        self, *, external_userid: str, open_kfid: str
    ) -> ServiceStateSnapshot: ...

    async def transition_service_state(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        service_state: WeComServiceState,
    ) -> ServiceStateTransition: ...

    async def send_event_text(self, *, code: str, content: str, msgid: str) -> None: ...


class WeComClient:
    def __init__(
        self,
        *,
        corp_id: str,
        secret: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.corp_id = corp_id
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def sync_messages(
        self,
        *,
        callback_token: str,
        open_kfid: str,
        cursor: str | None,
    ) -> SyncPage:
        body: dict[str, Any] = {
            "token": callback_token,
            "limit": 1000,
            "voice_format": 0,
            "open_kfid": open_kfid,
        }
        if cursor:
            body["cursor"] = cursor
        payload = await self._authorized_request("POST", "/cgi-bin/kf/sync_msg", json=body)
        return SyncPage.model_validate(payload)

    async def send_text(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        content: str,
        msgid: str,
    ) -> None:
        body = {
            "touser": external_userid,
            "open_kfid": open_kfid,
            "msgid": msgid,
            "msgtype": "text",
            "text": {"content": content},
        }
        await self._authorized_request("POST", "/cgi-bin/kf/send_msg", json=body)

    async def get_service_state(
        self, *, external_userid: str, open_kfid: str
    ) -> ServiceStateSnapshot:
        payload = await self._authorized_request(
            "POST",
            "/cgi-bin/kf/service_state/get",
            json={"open_kfid": open_kfid, "external_userid": external_userid},
        )
        return ServiceStateSnapshot.model_validate(payload)

    async def transition_service_state(
        self,
        *,
        external_userid: str,
        open_kfid: str,
        service_state: WeComServiceState,
    ) -> ServiceStateTransition:
        payload = await self._authorized_request(
            "POST",
            "/cgi-bin/kf/service_state/trans",
            json={
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "service_state": int(service_state),
            },
        )
        return ServiceStateTransition.model_validate(payload)

    async def send_event_text(self, *, code: str, content: str, msgid: str) -> None:
        await self._authorized_request(
            "POST",
            "/cgi-bin/kf/send_msg_on_event",
            json={
                "code": code,
                "msgid": msgid,
                "msgtype": "text",
                "text": {"content": content},
            },
        )

    async def list_accounts(self) -> list[KfAccount]:
        accounts: list[KfAccount] = []
        offset = 0
        limit = 100
        while True:
            payload = await self._authorized_request(
                "GET",
                "/cgi-bin/kf/account/list",
                json={"offset": offset, "limit": limit},
            )
            page = [KfAccount.model_validate(item) for item in payload.get("account_list", [])]
            accounts.extend(page)
            if len(page) < limit:
                return accounts
            offset += limit

    async def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any],
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        payload = await self._request(method, path, params={"access_token": token}, json=json)
        if int(payload.get("errcode", 0)) in TOKEN_INVALID_ERROR_CODES:
            await self._invalidate_access_token(token)
            token = await self._get_access_token()
            payload = await self._request(method, path, params={"access_token": token}, json=json)
        self._raise_for_api_error(payload)
        return payload

    async def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        async with self._token_lock:
            now = time.monotonic()
            if self._access_token and now < self._access_token_expires_at:
                return self._access_token
            payload = await self._request(
                "GET",
                "/cgi-bin/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
            )
            self._raise_for_api_error(payload)
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise WeComAPIError(-1, "access_token missing from response")
            expires_in = int(payload.get("expires_in", 7200))
            self._access_token = token
            self._access_token_expires_at = now + max(1, expires_in - 300)
            return token

    async def _invalidate_access_token(self, token: str) -> None:
        async with self._token_lock:
            if self._access_token == token:
                self._access_token = None
                self._access_token_expires_at = 0.0

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
            )
        except httpx.TimeoutException:
            raise WeComTransportError("WeCom request timed out") from None
        except httpx.TransportError:
            raise WeComTransportError("WeCom network request failed") from None
        if response.is_error:
            raise WeComTransportError(
                f"WeCom returned HTTP status {response.status_code}"
            ) from None
        payload = response.json()
        if not isinstance(payload, dict):
            raise WeComAPIError(-1, "API response is not a JSON object")
        return payload

    @staticmethod
    def _raise_for_api_error(payload: dict[str, Any]) -> None:
        errcode = int(payload.get("errcode", 0))
        if errcode:
            raise WeComAPIError(errcode, str(payload.get("errmsg", "unknown error")))
