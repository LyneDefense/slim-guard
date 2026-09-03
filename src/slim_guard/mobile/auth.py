from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import func, select

from slim_guard.db.models import (
    MobileAuthIdentityRecord,
    MobileOtpChallengeRecord,
    MobileSessionRecord,
    SlimGuardUser,
)
from slim_guard.db.session import Database

_PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class MobileAuthError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MobilePrincipal:
    user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class IssuedMobileTokens:
    access_token: str
    access_expires_in_seconds: int
    refresh_token: str
    user_id: str
    identity_hint: str | None


@dataclass(frozen=True, slots=True)
class OtpChallenge:
    id: str
    code: str
    expires_in_seconds: int
    retry_after_seconds: int


class MobileOtpSender(Protocol):
    async def send(self, *, phone: str, code: str, expires_in_seconds: int) -> None: ...


class WebhookMobileOtpSender:
    """Small provider boundary; the webhook owns vendor-specific SMS integration."""

    def __init__(self, *, url: str, token: str = "", timeout_seconds: float = 10) -> None:
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds

    async def send(self, *, phone: str, code: str, expires_in_seconds: int) -> None:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                self._url,
                headers=headers,
                json={
                    "phone": phone,
                    "code": code,
                    "expires_in_seconds": expires_in_seconds,
                    "purpose": "slim_guard_login",
                },
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise MobileAuthError("otp_delivery_failed", "Unable to deliver login code")


class NullMobileOtpSender:
    async def send(self, *, phone: str, code: str, expires_in_seconds: int) -> None:
        return None


class MobileTokenCodec:
    """Issues short-lived signed access tokens; refresh tokens remain server-side state."""

    def __init__(self, secret: str, *, access_ttl: timedelta) -> None:
        if len(secret) < 32:
            raise ValueError("Mobile token secret must contain at least 32 characters")
        if access_ttl <= timedelta(0):
            raise ValueError("Mobile access token TTL must be positive")
        self._key = secret.encode()
        self._access_ttl = access_ttl

    @property
    def access_ttl_seconds(self) -> int:
        return int(self._access_ttl.total_seconds())

    def issue(self, principal: MobilePrincipal, *, now: datetime) -> str:
        issued_at = self._aware(now)
        payload = {
            "v": 1,
            "sid": principal.session_id,
            "uid": principal.user_id,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + self._access_ttl).timestamp()),
        }
        encoded = self._encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signature = self._encode(hmac.digest(self._key, encoded.encode(), "sha256"))
        return f"sg1.{encoded}.{signature}"

    def verify(self, token: str, *, now: datetime) -> MobilePrincipal:
        try:
            prefix, encoded, signature = token.split(".", 2)
        except ValueError as exc:
            raise MobileAuthError("invalid_access_token", "Invalid access token") from exc
        if prefix != "sg1":
            raise MobileAuthError("invalid_access_token", "Invalid access token")
        expected = self._encode(hmac.digest(self._key, encoded.encode(), "sha256"))
        if not hmac.compare_digest(signature, expected):
            raise MobileAuthError("invalid_access_token", "Invalid access token")
        try:
            payload = json.loads(self._decode(encoded))
            session_id = str(payload["sid"])
            user_id = str(payload["uid"])
            expires_at = int(payload["exp"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise MobileAuthError("invalid_access_token", "Invalid access token") from exc
        if int(self._aware(now).timestamp()) >= expires_at:
            raise MobileAuthError("access_token_expired", "Access token has expired")
        if not session_id or not user_id:
            raise MobileAuthError("invalid_access_token", "Invalid access token")
        return MobilePrincipal(user_id=user_id, session_id=session_id)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


class MobileAuthService:
    def __init__(
        self,
        *,
        database: Database,
        secret: str,
        sender: MobileOtpSender,
        access_ttl: timedelta = timedelta(minutes=15),
        refresh_ttl: timedelta = timedelta(days=30),
        otp_ttl: timedelta = timedelta(minutes=5),
        resend_after: timedelta = timedelta(minutes=1),
        hourly_limit: int = 5,
        code_factory: Callable[[], str] | None = None,
    ) -> None:
        if refresh_ttl <= timedelta(0) or otp_ttl <= timedelta(0):
            raise ValueError("Mobile authentication TTLs must be positive")
        if resend_after <= timedelta(0) or hourly_limit < 1:
            raise ValueError("Mobile OTP limits must be positive")
        self._database = database
        self._key = secret.encode()
        self._sender = sender
        self._codec = MobileTokenCodec(secret, access_ttl=access_ttl)
        self._refresh_ttl = refresh_ttl
        self._otp_ttl = otp_ttl
        self._resend_after = resend_after
        self._hourly_limit = hourly_limit
        self._code_factory = code_factory or self._random_code

    @property
    def codec(self) -> MobileTokenCodec:
        return self._codec

    async def request_otp(self, phone: str, *, now: datetime) -> OtpChallenge:
        current = self._aware(now)
        normalized = self.normalize_phone(phone)
        subject_hash = self._subject_hash("phone", normalized)
        code = str(self._code_factory())
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("OTP code factory must return six digits")
        async with self._database.session() as session, session.begin():
            latest = await session.scalar(
                select(MobileOtpChallengeRecord)
                .where(MobileOtpChallengeRecord.subject_hash == subject_hash)
                .order_by(MobileOtpChallengeRecord.created_at.desc())
                .limit(1)
            )
            if latest is not None:
                latest_created = self._aware(latest.created_at)
                retry_at = latest_created + self._resend_after
                if retry_at > current:
                    raise MobileAuthError(
                        "otp_too_frequent",
                        "Request another code after "
                        f"{int((retry_at - current).total_seconds())} seconds",
                    )
            sent_last_hour = await session.scalar(
                select(func.count(MobileOtpChallengeRecord.id)).where(
                    MobileOtpChallengeRecord.subject_hash == subject_hash,
                    MobileOtpChallengeRecord.created_at >= current - timedelta(hours=1),
                )
            )
            if int(sent_last_hour or 0) >= self._hourly_limit:
                raise MobileAuthError("otp_rate_limited", "Too many login code requests")
            challenge = MobileOtpChallengeRecord(
                subject_hash=subject_hash,
                phone_hint=normalized[-4:],
                code_hash=self._otp_hash("pending", code),
                expires_at=current + self._otp_ttl,
                created_at=current,
            )
            session.add(challenge)
            await session.flush()
            challenge.code_hash = self._otp_hash(challenge.id, code)
            challenge_id = challenge.id
        try:
            await self._sender.send(
                phone=normalized,
                code=code,
                expires_in_seconds=int(self._otp_ttl.total_seconds()),
            )
        except Exception:
            async with self._database.session() as session, session.begin():
                row = await session.get(MobileOtpChallengeRecord, challenge_id)
                if row is not None:
                    row.status = "expired"
            raise
        return OtpChallenge(
            id=challenge_id,
            code=code,
            expires_in_seconds=int(self._otp_ttl.total_seconds()),
            retry_after_seconds=int(self._resend_after.total_seconds()),
        )

    async def verify_otp(
        self,
        *,
        challenge_id: str,
        code: str,
        device_label: str | None,
        now: datetime,
    ) -> IssuedMobileTokens:
        current = self._aware(now)
        verification_error: MobileAuthError | None = None
        issued: tuple[str, str, str, str | None] | None = None
        async with self._database.session() as session, session.begin():
            challenge = await session.get(MobileOtpChallengeRecord, challenge_id)
            if challenge is None:
                raise MobileAuthError("otp_not_found", "Login code request was not found")
            if challenge.status != "pending":
                raise MobileAuthError("otp_not_active", "Login code is no longer active")
            if self._aware(challenge.expires_at) <= current:
                challenge.status = "expired"
                verification_error = MobileAuthError("otp_expired", "Login code has expired")
            else:
                challenge.attempt_count += 1
                if not hmac.compare_digest(
                    challenge.code_hash, self._otp_hash(challenge.id, code)
                ):
                    if challenge.attempt_count >= 5:
                        challenge.status = "locked"
                    verification_error = MobileAuthError(
                        "otp_invalid", "Login code is incorrect"
                    )
            if verification_error is None:
                identity = await session.scalar(
                    select(MobileAuthIdentityRecord).where(
                        MobileAuthIdentityRecord.provider == "phone",
                        MobileAuthIdentityRecord.subject_hash == challenge.subject_hash,
                    )
                )
                if identity is None:
                    user = SlimGuardUser(first_seen_at=current, last_seen_at=current)
                    session.add(user)
                    await session.flush()
                    identity = MobileAuthIdentityRecord(
                        user_id=user.id,
                        provider="phone",
                        subject_hash=challenge.subject_hash,
                        display_hint="手机号尾号 " + challenge.phone_hint,
                        created_at=current,
                        last_authenticated_at=current,
                    )
                    session.add(identity)
                else:
                    existing_user = await session.get(SlimGuardUser, identity.user_id)
                    if existing_user is None:
                        raise MobileAuthError("account_unavailable", "Account is unavailable")
                    user = existing_user
                    identity.last_authenticated_at = current
                    user.last_seen_at = current

                refresh_token = self._new_refresh_token()
                session_id, _ = refresh_token.split(".", 1)
                session.add(
                    MobileSessionRecord(
                        id=session_id,
                        user_id=user.id,
                        refresh_token_hash=self._token_hash(refresh_token),
                        device_label=device_label,
                        expires_at=current + self._refresh_ttl,
                        created_at=current,
                        last_used_at=current,
                    )
                )
                challenge.status = "consumed"
                challenge.consumed_at = current
                await session.flush()
                issued = (user.id, session_id, refresh_token, identity.display_hint)
        if verification_error is not None:
            raise verification_error
        assert issued is not None
        user_id, session_id, refresh_token, hint = issued
        principal = MobilePrincipal(user_id=user_id, session_id=session_id)
        return IssuedMobileTokens(
            access_token=self._codec.issue(principal, now=current),
            access_expires_in_seconds=self._codec.access_ttl_seconds,
            refresh_token=refresh_token,
            user_id=user_id,
            identity_hint=hint,
        )

    async def refresh(self, refresh_token: str, *, now: datetime) -> IssuedMobileTokens:
        current = self._aware(now)
        session_id = self._refresh_session_id(refresh_token)
        refresh_error: MobileAuthError | None = None
        issued: tuple[str, str | None, str] | None = None
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileSessionRecord, session_id)
            if row is None or row.revoked_at is not None:
                raise MobileAuthError("invalid_refresh_token", "Refresh token is invalid")
            if self._aware(row.expires_at) <= current:
                row.revoked_at = current
                refresh_error = MobileAuthError(
                    "refresh_token_expired", "Refresh token has expired"
                )
            elif not hmac.compare_digest(
                row.refresh_token_hash, self._token_hash(refresh_token)
            ):
                row.revoked_at = current
                refresh_error = MobileAuthError(
                    "invalid_refresh_token", "Refresh token is invalid"
                )
            if refresh_error is None:
                new_refresh = f"{row.id}.{secrets.token_urlsafe(32)}"
                row.refresh_token_hash = self._token_hash(new_refresh)
                row.last_used_at = current
                user = await session.get(SlimGuardUser, row.user_id)
                identity = await session.scalar(
                    select(MobileAuthIdentityRecord)
                    .where(MobileAuthIdentityRecord.user_id == row.user_id)
                    .order_by(MobileAuthIdentityRecord.created_at)
                    .limit(1)
                )
                if user is None:
                    raise MobileAuthError("account_unavailable", "Account is unavailable")
                issued = (
                    row.user_id,
                    identity.display_hint if identity is not None else None,
                    new_refresh,
                )
        if refresh_error is not None:
            raise refresh_error
        assert issued is not None
        user_id, hint, new_refresh = issued
        principal = MobilePrincipal(user_id=user_id, session_id=session_id)
        return IssuedMobileTokens(
            access_token=self._codec.issue(principal, now=current),
            access_expires_in_seconds=self._codec.access_ttl_seconds,
            refresh_token=new_refresh,
            user_id=user_id,
            identity_hint=hint,
        )

    async def authenticate(self, token: str, *, now: datetime) -> MobilePrincipal:
        current = self._aware(now)
        principal = self._codec.verify(token, now=current)
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileSessionRecord, principal.session_id)
            if (
                row is None
                or row.user_id != principal.user_id
                or row.revoked_at is not None
                or self._aware(row.expires_at) <= current
            ):
                raise MobileAuthError("session_inactive", "Mobile session is inactive")
            row.last_used_at = current
        return principal

    async def logout(self, principal: MobilePrincipal, *, now: datetime) -> None:
        async with self._database.session() as session, session.begin():
            row = await session.get(MobileSessionRecord, principal.session_id)
            if row is not None and row.user_id == principal.user_id and row.revoked_at is None:
                row.revoked_at = self._aware(now)

    def _subject_hash(self, provider: str, subject: str) -> str:
        return hmac.new(self._key, f"{provider}:{subject}".encode(), hashlib.sha256).hexdigest()

    def _otp_hash(self, challenge_id: str, code: str) -> str:
        return hmac.new(
            self._key,
            f"otp:{challenge_id}:{code}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _token_hash(self, token: str) -> str:
        return hmac.new(self._key, f"refresh:{token}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def normalize_phone(value: str) -> str:
        compact = re.sub(r"[\s()\-]", "", value)
        if re.fullmatch(r"1\d{10}", compact):
            compact = "+86" + compact
        elif re.fullmatch(r"86\d{11}", compact):
            compact = "+" + compact
        if not _PHONE_PATTERN.fullmatch(compact):
            raise MobileAuthError("invalid_phone", "Phone number must use E.164 format")
        return compact

    @staticmethod
    def _new_refresh_token() -> str:
        return f"{secrets.token_hex(18)}.{secrets.token_urlsafe(32)}"

    @staticmethod
    def _refresh_session_id(token: str) -> str:
        try:
            session_id, secret = token.split(".", 1)
        except ValueError as exc:
            raise MobileAuthError("invalid_refresh_token", "Refresh token is invalid") from exc
        if len(session_id) != 36 or len(secret) < 32:
            raise MobileAuthError("invalid_refresh_token", "Refresh token is invalid")
        return session_id

    @staticmethod
    def _random_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)
