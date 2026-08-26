from __future__ import annotations


class WeComError(Exception):
    """Base error for WeCom integration failures."""


class WeComCryptoError(WeComError):
    """Raised when callback signature or ciphertext validation fails."""


class WeComMalformedPayloadError(WeComError):
    """Raised when an XML payload cannot be parsed."""


class WeComAPIError(WeComError):
    def __init__(self, errcode: int, errmsg: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"WeCom API error {errcode}: {errmsg}")


class WeComTransportError(WeComError):
    """A sanitized network failure that never includes credential-bearing URLs."""
