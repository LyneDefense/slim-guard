from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy import select

from slim_guard.db.models import MobileDeviceRecord
from slim_guard.db.session import Database


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    data: dict[str, str]


@dataclass(frozen=True, slots=True)
class PushDelivery:
    token: str
    accepted: bool
    error_code: str | None = None


class PushProvider(Protocol):
    name: str

    async def send(
        self,
        *,
        tokens: tuple[str, ...],
        message: PushMessage,
    ) -> tuple[PushDelivery, ...]: ...


class ExpoPushProvider:
    """Expo transport behind a provider boundary replaceable by direct APNs/FCM."""

    name = "expo"

    def __init__(
        self,
        *,
        access_token: str = "",
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._http = httpx.AsyncClient(
            base_url="https://exp.host/--/api/v2/push",
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def send(
        self,
        *,
        tokens: tuple[str, ...],
        message: PushMessage,
    ) -> tuple[PushDelivery, ...]:
        if not tokens:
            return ()
        response = await self._http.post(
            "/send",
            json=[
                {
                    "to": token,
                    "title": message.title,
                    "body": message.body,
                    "data": message.data,
                    "sound": "default",
                }
                for token in tokens
            ],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        deliveries: list[PushDelivery] = []
        for index, token in enumerate(tokens):
            row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
            accepted = row.get("status") == "ok"
            details_value = row.get("details")
            details: dict[str, object] = details_value if isinstance(details_value, dict) else {}
            deliveries.append(
                PushDelivery(
                    token=token,
                    accepted=accepted,
                    error_code=None if accepted else str(details.get("error") or "push_rejected"),
                )
            )
        return tuple(deliveries)


class MobilePushService:
    def __init__(self, *, database: Database, providers: tuple[PushProvider, ...]) -> None:
        self._database = database
        self._providers = {provider.name: provider for provider in providers}

    async def notify_user(self, user_id: str, message: PushMessage) -> tuple[PushDelivery, ...]:
        async with self._database.session() as session:
            devices = tuple(
                await session.scalars(
                    select(MobileDeviceRecord).where(
                        MobileDeviceRecord.user_id == user_id,
                        MobileDeviceRecord.revoked_at.is_(None),
                    )
                )
            )
        deliveries: list[PushDelivery] = []
        for name, provider in self._providers.items():
            tokens = tuple(row.push_token for row in devices if row.push_provider == name)
            deliveries.extend(await provider.send(tokens=tokens, message=message))
        return tuple(deliveries)
