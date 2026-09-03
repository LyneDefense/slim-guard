from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from slim_guard.db.models import (
    ChannelIdentity,
    MobileAuthIdentityRecord,
    MobileDeviceRecord,
    MobileSessionRecord,
    MobileWeComBindingRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database
from slim_guard.mobile.contracts import DeviceRegistrationRequest
from slim_guard.mobile.platform import MobilePlatformService
from slim_guard.mobile.push import ExpoPushProvider, PushMessage

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)
SECRET = "mobile-platform-test-secret-with-at-least-32-characters"


async def platform_service(tmp_path) -> tuple[Database, MobilePlatformService, str, str]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'mobile-platform.sqlite3'}")
    await database.create_schema()
    async with database.session() as session, session.begin():
        mobile_user = SlimGuardUser(first_seen_at=NOW, last_seen_at=NOW)
        wecom_user = SlimGuardUser(first_seen_at=NOW, last_seen_at=NOW, nickname="微信用户")
        session.add_all((mobile_user, wecom_user))
        await session.flush()
        session.add(
            MobileAuthIdentityRecord(
                user_id=mobile_user.id,
                provider="phone",
                subject_hash="subject-hash",
                display_hint="手机号尾号 8000",
                created_at=NOW,
                last_authenticated_at=NOW,
            )
        )
        session.add(
            MobileSessionRecord(
                id="session-id",
                user_id=mobile_user.id,
                refresh_token_hash="refresh-hash",
                expires_at=NOW + timedelta(days=1),
                created_at=NOW,
                last_used_at=NOW,
            )
        )
        session.add(
            ChannelIdentity(
                channel_id="default",
                external_userid="wecom-user",
                user_id=wecom_user.id,
            )
        )
        mobile_id = mobile_user.id
        wecom_id = wecom_user.id
    return database, MobilePlatformService(database=database, secret=SECRET), mobile_id, wecom_id


async def test_binding_moves_fresh_mobile_login_to_existing_wecom_user(tmp_path) -> None:
    database, service, mobile_id, wecom_id = await platform_service(tmp_path)
    try:
        binding = await service.create_binding(mobile_id, now=NOW)
        assert binding.code is not None
        result = await service.claim_wecom_message(
            channel_id="default",
            external_userid="wecom-user",
            text=f"SG-{binding.code}",
            now=NOW + timedelta(minutes=1),
        )

        assert result is not None and result.status == "claimed"
        assert (
            await service.claim_wecom_message(
                channel_id="default",
                external_userid="wecom-user",
                text="我今天吃了米饭",
                now=NOW,
            )
            is None
        )
        async with database.session() as session:
            identity = await session.scalar(select(MobileAuthIdentityRecord))
            mobile_session = await session.scalar(select(MobileSessionRecord))
            stored_binding = await session.scalar(select(MobileWeComBindingRecord))
            old_user = await session.get(SlimGuardUser, mobile_id)
        assert identity is not None and identity.user_id == wecom_id
        assert mobile_session is not None and mobile_session.user_id == wecom_id
        assert stored_binding is not None and stored_binding.mobile_user_id == wecom_id
        assert old_user is None
    finally:
        await database.close()


async def test_device_registration_is_idempotent_and_hides_token(tmp_path) -> None:
    database, service, mobile_id, _ = await platform_service(tmp_path)
    try:
        request = DeviceRegistrationRequest(
            installation_id="installation-0001",
            platform="ios",
            push_provider="expo",
            push_token="ExponentPushToken[test-device-token]",
            app_version="1.0.0",
            timezone="Asia/Shanghai",
            locale="zh-CN",
        )
        first = await service.register_device(mobile_id, request, now=NOW)
        second = await service.register_device(
            mobile_id,
            request.model_copy(update={"app_version": "1.0.1"}),
            now=NOW + timedelta(minutes=1),
        )
        async with database.session() as session:
            devices = tuple(await session.scalars(select(MobileDeviceRecord)))
        assert first.id == second.id
        assert second.app_version == "1.0.1"
        assert len(devices) == 1
        assert "push_token" not in second.model_dump()
    finally:
        await database.close()


async def test_expo_push_provider_maps_delivery_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/send")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"status": "ok", "id": "receipt-1"},
                    {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                ]
            },
        )

    provider = ExpoPushProvider(transport=httpx.MockTransport(handler))
    try:
        deliveries = await provider.send(
            tokens=("token-1", "token-2"),
            message=PushMessage(title="提醒", body="记一下体重", data={"screen": "today"}),
        )
    finally:
        await provider.close()
    assert deliveries[0].accepted is True
    assert deliveries[1].error_code == "DeviceNotRegistered"
