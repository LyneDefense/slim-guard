from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

ADMIN_SESSION_COOKIE = "slim_guard_admin_session"


@dataclass(frozen=True, slots=True)
class AdminSession:
    username: str
    expires_at: int


class AdminSessionCodec:
    """Issues short-lived signed sessions without storing browser credentials."""

    def __init__(self, *, password: str, ttl_seconds: int) -> None:
        if not password:
            raise ValueError("Admin session signing material cannot be empty")
        if ttl_seconds <= 0:
            raise ValueError("Admin session TTL must be positive")
        self._key = hashlib.sha256(
            b"slim-guard-admin-session-v1\0" + password.encode("utf-8")
        ).digest()
        self._ttl_seconds = ttl_seconds

    def issue(self, username: str, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else now
        payload = {
            "v": 1,
            "sub": username,
            "iat": issued_at,
            "exp": issued_at + self._ttl_seconds,
            "nonce": secrets.token_urlsafe(18),
        }
        encoded = self._encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = self._encode(hmac.digest(self._key, encoded.encode("ascii"), "sha256"))
        return f"{encoded}.{signature}"

    def verify(
        self,
        token: str,
        *,
        expected_username: str,
        now: int | None = None,
    ) -> AdminSession | None:
        try:
            encoded, signature = token.split(".", 1)
            expected_signature = self._encode(
                hmac.digest(self._key, encoded.encode("ascii"), "sha256")
            )
            if not secrets.compare_digest(signature, expected_signature):
                return None
            payload = json.loads(self._decode(encoded))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        username = payload.get("sub")
        expires_at = payload.get("exp")
        issued_at = payload.get("iat")
        current = int(time.time()) if now is None else now
        if (
            not isinstance(username, str)
            or not secrets.compare_digest(username, expected_username)
            or not isinstance(expires_at, int)
            or not isinstance(issued_at, int)
            or issued_at > current + 60
            or expires_at <= current
            or expires_at - issued_at != self._ttl_seconds
        ):
            return None
        return AdminSession(username=username, expires_at=expires_at)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
