from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from defusedxml.ElementTree import ParseError, fromstring

from slim_guard.integrations.wecom_kf.errors import (
    WeComCryptoError,
    WeComMalformedPayloadError,
)

PKCS7_BLOCK_SIZE = 32


@dataclass(frozen=True, slots=True)
class KfCallbackEvent:
    token: str
    open_kfid: str


class WeComCallbackCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        if not token or not receive_id:
            raise ValueError("callback token and receive_id are required")
        try:
            aes_key = base64.b64decode(f"{encoding_aes_key}=", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid EncodingAESKey") from exc
        if len(aes_key) != 32:
            raise ValueError("EncodingAESKey must decode to 32 bytes")
        self.token = token
        self.aes_key = aes_key
        self.receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        parts = sorted((self.token, timestamp, nonce, encrypted))
        return hashlib.sha1("".join(parts).encode()).hexdigest()  # noqa: S324

    def verify_signature(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> None:
        expected = self.signature(timestamp, nonce, encrypted)
        if not hmac.compare_digest(expected, msg_signature):
            raise WeComCryptoError("callback signature mismatch")

    def decrypt(self, encrypted: str) -> bytes:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise WeComCryptoError("ciphertext is not valid base64") from exc
        if not ciphertext or len(ciphertext) % 16:
            raise WeComCryptoError("ciphertext length is invalid")

        decryptor = Cipher(algorithms.AES(self.aes_key), modes.CBC(self.aes_key[:16])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plaintext = self._unpad(padded)
        if len(plaintext) < 20:
            raise WeComCryptoError("decrypted payload is too short")

        message_length = struct.unpack(">I", plaintext[16:20])[0]
        message_end = 20 + message_length
        if message_end > len(plaintext):
            raise WeComCryptoError("decrypted message length is invalid")
        message = plaintext[20:message_end]
        try:
            receive_id = plaintext[message_end:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeComCryptoError("receive ID is not UTF-8") from exc
        if not hmac.compare_digest(receive_id, self.receive_id):
            raise WeComCryptoError("receive ID mismatch")
        return message

    def encrypt(self, message: bytes, *, random_bytes: bytes | None = None) -> str:
        prefix = random_bytes if random_bytes is not None else os.urandom(16)
        if len(prefix) != 16:
            raise ValueError("random prefix must be 16 bytes")
        plaintext = prefix + struct.pack(">I", len(message)) + message + self.receive_id.encode()
        encryptor = Cipher(algorithms.AES(self.aes_key), modes.CBC(self.aes_key[:16])).encryptor()
        ciphertext = encryptor.update(self._pad(plaintext)) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")

    def verify_url(
        self,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echo_str: str,
    ) -> str:
        self.verify_signature(msg_signature, timestamp, nonce, echo_str)
        try:
            return self.decrypt(echo_str).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeComCryptoError("decrypted echo is not UTF-8") from exc

    def decrypt_callback(
        self,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        body: bytes,
    ) -> bytes:
        encrypted = self._extract_encrypted(body)
        self.verify_signature(msg_signature, timestamp, nonce, encrypted)
        return self.decrypt(encrypted)

    @staticmethod
    def parse_kf_event(plaintext_xml: bytes) -> KfCallbackEvent | None:
        try:
            root = fromstring(plaintext_xml)
        except ParseError as exc:
            raise WeComMalformedPayloadError("decrypted callback XML is malformed") from exc
        msg_type = root.findtext("MsgType")
        event_type = root.findtext("Event")
        if msg_type != "event" or event_type != "kf_msg_or_event":
            return None
        token = root.findtext("Token")
        open_kfid = root.findtext("OpenKfId")
        if not token or not open_kfid:
            raise WeComMalformedPayloadError("KF callback is missing Token or OpenKfId")
        return KfCallbackEvent(token=token, open_kfid=open_kfid)

    @staticmethod
    def _extract_encrypted(body: bytes) -> str:
        try:
            root = fromstring(body)
        except ParseError as exc:
            raise WeComMalformedPayloadError("callback XML is malformed") from exc
        encrypted = root.findtext("Encrypt")
        if not encrypted:
            raise WeComMalformedPayloadError("callback XML is missing Encrypt")
        return encrypted

    @staticmethod
    def _pad(value: bytes) -> bytes:
        padding_length = PKCS7_BLOCK_SIZE - (len(value) % PKCS7_BLOCK_SIZE)
        return value + bytes((padding_length,)) * padding_length

    @staticmethod
    def _unpad(value: bytes) -> bytes:
        if not value:
            raise WeComCryptoError("decrypted payload has no padding")
        padding_length = value[-1]
        if padding_length < 1 or padding_length > PKCS7_BLOCK_SIZE:
            raise WeComCryptoError("decrypted payload padding is invalid")
        if value[-padding_length:] != bytes((padding_length,)) * padding_length:
            raise WeComCryptoError("decrypted payload padding is corrupt")
        return value[:-padding_length]
