from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from slim_guard.db.models import (
    MobileAuthIdentityRecord,
    MobileOtpChallengeRecord,
    MobileSessionRecord,
)
from slim_guard.db.session import Database
from slim_guard.mobile.auth import MobileAuthError, MobileAuthService, NullMobileOtpSender

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
SECRET = "mobile-test-secret-with-at-least-32-characters"


async def service(tmp_path) -> tuple[Database, MobileAuthService]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'mobile-auth.sqlite3'}")
    await database.create_schema()
    return database, MobileAuthService(
        database=database,
        secret=SECRET,
        sender=NullMobileOtpSender(),
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(days=30),
        otp_ttl=timedelta(minutes=5),
        resend_after=timedelta(seconds=60),
        code_factory=lambda: "123456",
    )


async def test_mobile_otp_login_refresh_and_logout(tmp_path) -> None:
    database, auth = await service(tmp_path)
    try:
        challenge = await auth.request_otp("138 0013 8000", now=NOW)
        issued = await auth.verify_otp(
            challenge_id=challenge.id,
            code="123456",
            device_label="iPhone",
            now=NOW,
        )
        principal = await auth.authenticate(issued.access_token, now=NOW)
        refreshed = await auth.refresh(issued.refresh_token, now=NOW)
        refreshed_principal = await auth.authenticate(refreshed.access_token, now=NOW)

        assert challenge.code == "123456"
        assert issued.identity_hint == "手机号尾号 8000"
        assert principal.user_id == issued.user_id
        assert refreshed_principal == principal
        assert refreshed.refresh_token != issued.refresh_token

        async with database.session() as session:
            identity = await session.scalar(select(MobileAuthIdentityRecord))
            otp = await session.scalar(select(MobileOtpChallengeRecord))
            mobile_session = await session.scalar(select(MobileSessionRecord))
        assert identity is not None
        assert identity.subject_hash != "13800138000"
        assert otp is not None and otp.status == "consumed"
        assert mobile_session is not None
        assert "13800138000" not in repr(identity.__dict__)

        await auth.logout(principal, now=NOW)
        with pytest.raises(MobileAuthError, match="inactive"):
            await auth.authenticate(refreshed.access_token, now=NOW)
    finally:
        await database.close()


async def test_mobile_otp_failed_attempts_are_persisted_and_locked(tmp_path) -> None:
    database, auth = await service(tmp_path)
    try:
        challenge = await auth.request_otp("+8613800138000", now=NOW)
        for _ in range(4):
            with pytest.raises(MobileAuthError) as error:
                await auth.verify_otp(
                    challenge_id=challenge.id,
                    code="000000",
                    device_label=None,
                    now=NOW,
                )
            assert error.value.code == "otp_invalid"
        with pytest.raises(MobileAuthError):
            await auth.verify_otp(
                challenge_id=challenge.id,
                code="000000",
                device_label=None,
                now=NOW,
            )
        async with database.session() as session:
            row = await session.get(MobileOtpChallengeRecord, challenge.id)
        assert row is not None
        assert row.status == "locked"
        assert row.attempt_count == 5
        with pytest.raises(MobileAuthError) as locked:
            await auth.verify_otp(
                challenge_id=challenge.id,
                code="123456",
                device_label=None,
                now=NOW,
            )
        assert locked.value.code == "otp_not_active"
    finally:
        await database.close()
