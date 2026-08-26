from __future__ import annotations

import pytest

from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.integrations.wecom_kf.errors import WeComCryptoError


def test_encrypt_decrypt_and_signature_round_trip(encoding_aes_key: str) -> None:
    crypto = WeComCallbackCrypto("token", encoding_aes_key, "ww-corp")
    plaintext = b"<xml><MsgType>event</MsgType></xml>"
    encrypted = crypto.encrypt(plaintext, random_bytes=b"0123456789abcdef")
    signature = crypto.signature("123", "nonce", encrypted)

    crypto.verify_signature(signature, "123", "nonce", encrypted)

    assert crypto.decrypt(encrypted) == plaintext


def test_signature_mismatch_is_rejected(encoding_aes_key: str) -> None:
    crypto = WeComCallbackCrypto("token", encoding_aes_key, "ww-corp")
    encrypted = crypto.encrypt(b"hello", random_bytes=b"0123456789abcdef")

    with pytest.raises(WeComCryptoError, match="signature"):
        crypto.verify_signature("invalid", "123", "nonce", encrypted)


def test_receive_id_mismatch_is_rejected(encoding_aes_key: str) -> None:
    source = WeComCallbackCrypto("token", encoding_aes_key, "ww-source")
    target = WeComCallbackCrypto("token", encoding_aes_key, "ww-target")
    encrypted = source.encrypt(b"hello", random_bytes=b"0123456789abcdef")

    with pytest.raises(WeComCryptoError, match="receive ID"):
        target.decrypt(encrypted)


def test_parse_kf_callback_event() -> None:
    event = WeComCallbackCrypto.parse_kf_event(
        b"""
        <xml>
          <MsgType><![CDATA[event]]></MsgType>
          <Event><![CDATA[kf_msg_or_event]]></Event>
          <Token><![CDATA[sync-token]]></Token>
          <OpenKfId><![CDATA[wk-test]]></OpenKfId>
        </xml>
        """
    )

    assert event is not None
    assert event.token == "sync-token"
    assert event.open_kfid == "wk-test"


def test_verify_url_returns_plain_echo(encoding_aes_key: str) -> None:
    crypto = WeComCallbackCrypto("token", encoding_aes_key, "ww-corp")
    encrypted = crypto.encrypt(b"echo-value", random_bytes=b"0123456789abcdef")
    signature = crypto.signature("123", "nonce", encrypted)

    result = crypto.verify_url(
        msg_signature=signature,
        timestamp="123",
        nonce="nonce",
        echo_str=encrypted,
    )

    assert result == "echo-value"
