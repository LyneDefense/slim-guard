from __future__ import annotations

from slim_guard.admin.auth import AdminSessionCodec


def test_admin_session_round_trip() -> None:
    codec = AdminSessionCodec(password="a-long-admin-password", ttl_seconds=3600)

    token = codec.issue("operator", now=1_000)
    session = codec.verify(token, expected_username="operator", now=1_001)

    assert session is not None
    assert session.username == "operator"
    assert session.expires_at == 4_600


def test_admin_session_rejects_tampering_expiration_and_changed_credentials() -> None:
    codec = AdminSessionCodec(password="a-long-admin-password", ttl_seconds=3600)
    token = codec.issue("operator", now=1_000)

    assert codec.verify(f"{token}x", expected_username="operator", now=1_001) is None
    assert codec.verify(token, expected_username="another-user", now=1_001) is None
    assert codec.verify(token, expected_username="operator", now=4_600) is None
    assert (
        AdminSessionCodec(password="a-new-admin-password", ttl_seconds=3600).verify(
            token,
            expected_username="operator",
            now=1_001,
        )
        is None
    )
