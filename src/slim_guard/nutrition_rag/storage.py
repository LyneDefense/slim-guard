from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import-untyped]
from qcloud_cos.cos_exception import (  # type: ignore[import-untyped]
    CosClientError,
    CosServiceError,
)


class NutritionObjectStoreError(RuntimeError):
    pass


class NutritionObjectNotFound(NutritionObjectStoreError):
    pass


class NutritionObjectIntegrityError(NutritionObjectStoreError):
    pass


@dataclass(frozen=True, slots=True)
class StoredNutritionObject:
    bucket: str
    region: str
    key: str
    etag: str | None
    byte_size: int
    sha256: str
    media_type: str


class NutritionObjectStore(Protocol):
    @property
    def bucket(self) -> str: ...

    @property
    def region(self) -> str: ...

    def object_key(self, *, sha256: str, filename: str) -> str: ...

    async def put(
        self,
        *,
        key: str,
        content: bytes,
        sha256: str,
        media_type: str,
    ) -> StoredNutritionObject: ...

    async def get(self, *, key: str, max_bytes: int) -> bytes: ...

    async def head(self, *, key: str) -> StoredNutritionObject | None: ...


class TencentCosNutritionObjectStore:
    """Private, content-addressed storage backed by Tencent Cloud COS."""

    def __init__(
        self,
        *,
        region: str,
        bucket: str,
        prefix: str,
        secret_id: str,
        secret_key: str,
        session_token: str | None = None,
        domain: str | None = None,
        client: CosS3Client | None = None,
    ) -> None:
        if not region or not bucket or not secret_id or not secret_key:
            raise ValueError("Tencent COS region, bucket, Secret ID, and secret key are required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}", prefix):
            raise ValueError("Invalid Tencent COS object prefix")
        if prefix.startswith("/") or prefix.endswith("/") or ".." in prefix.split("/"):
            raise ValueError("Tencent COS object prefix must be a safe relative path")
        self._region = region
        self._bucket = bucket
        self._prefix = prefix
        self._client = client or CosS3Client(
            CosConfig(
                Region=region,
                SecretId=secret_id,
                SecretKey=secret_key,
                Token=session_token or None,
                Scheme="https",
                Domain=domain or None,
            ),
            retry=3,
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def region(self) -> str:
        return self._region

    def object_key(self, *, sha256: str, filename: str) -> str:
        _validate_sha256(sha256)
        suffix = _safe_suffix(filename)
        return f"{self._prefix}/sha256/{sha256[:2]}/{sha256}{suffix}"

    async def put(
        self,
        *,
        key: str,
        content: bytes,
        sha256: str,
        media_type: str,
    ) -> StoredNutritionObject:
        _validate_key(key, prefix=self._prefix)
        _validate_sha256(sha256)
        if not content or hashlib.sha256(content).hexdigest() != sha256:
            raise NutritionObjectIntegrityError("Nutrition object content hash mismatch")
        existing = await self.head(key=key)
        if existing is not None:
            if existing.sha256 != sha256 or existing.byte_size != len(content):
                raise NutritionObjectIntegrityError("Existing COS object does not match its key")
            return existing

        def upload() -> Mapping[str, object]:
            try:
                return cast(
                    Mapping[str, object],
                    self._client.put_object(
                        Bucket=self._bucket,
                        Key=key,
                        Body=content,
                        # The SDK forwards this value directly as an HTTP header.
                        ContentLength=str(len(content)),
                        ContentType=media_type,
                        Metadata={
                            "x-cos-meta-sha256": sha256,
                            "x-cos-meta-slimguard-purpose": "nutrition-knowledge-source",
                        },
                        EnableMD5=True,
                        ServerSideEncryption="AES256",
                    ),
                )
            except (CosClientError, CosServiceError) as error:
                raise NutritionObjectStoreError("cos_put_failed") from error

        response = await asyncio.to_thread(upload)
        return StoredNutritionObject(
            bucket=self._bucket,
            region=self._region,
            key=key,
            etag=_header(response, "ETag"),
            byte_size=len(content),
            sha256=sha256,
            media_type=media_type,
        )

    async def get(self, *, key: str, max_bytes: int) -> bytes:
        _validate_key(key, prefix=self._prefix)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")

        def download() -> bytes:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
                body = response["Body"]
                content = bytearray()
                for chunk in body.get_stream(chunk_size=1024 * 1024):
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise NutritionObjectStoreError("cos_object_too_large")
                raw = body.get_raw_stream()
                close = getattr(raw, "close", None)
                if callable(close):
                    close()
                return bytes(content)
            except CosServiceError as error:
                if error.get_status_code() == 404:
                    raise NutritionObjectNotFound(key) from error
                raise NutritionObjectStoreError("cos_get_failed") from error
            except CosClientError as error:
                raise NutritionObjectStoreError("cos_get_failed") from error

        return await asyncio.to_thread(download)

    async def head(self, *, key: str) -> StoredNutritionObject | None:
        _validate_key(key, prefix=self._prefix)

        def inspect_object() -> Mapping[str, object] | None:
            try:
                return cast(
                    Mapping[str, object],
                    self._client.head_object(Bucket=self._bucket, Key=key),
                )
            except CosServiceError as error:
                if error.get_status_code() == 404:
                    return None
                raise NutritionObjectStoreError("cos_head_failed") from error
            except CosClientError as error:
                raise NutritionObjectStoreError("cos_head_failed") from error

        response = await asyncio.to_thread(inspect_object)
        if response is None:
            return None
        size_text = _header(response, "Content-Length")
        digest = _header(response, "x-cos-meta-sha256")
        media_type = _header(response, "Content-Type") or "application/octet-stream"
        if size_text is None or not size_text.isdigit() or digest is None:
            raise NutritionObjectIntegrityError("COS object is missing SlimGuard metadata")
        _validate_sha256(digest)
        return StoredNutritionObject(
            bucket=self._bucket,
            region=self._region,
            key=key,
            etag=_header(response, "ETag"),
            byte_size=int(size_text),
            sha256=digest,
            media_type=media_type,
        )


class InMemoryNutritionObjectStore:
    """Deterministic test double with the same integrity behavior as COS."""

    def __init__(self, *, prefix: str = "slim-guard/nutrition-knowledge") -> None:
        self._prefix = prefix
        self._objects: dict[str, tuple[bytes, StoredNutritionObject]] = {}

    @property
    def bucket(self) -> str:
        return "test-nutrition-bucket-1000000000"

    @property
    def region(self) -> str:
        return "ap-test"

    def object_key(self, *, sha256: str, filename: str) -> str:
        _validate_sha256(sha256)
        return f"{self._prefix}/sha256/{sha256[:2]}/{sha256}{_safe_suffix(filename)}"

    async def put(
        self,
        *,
        key: str,
        content: bytes,
        sha256: str,
        media_type: str,
    ) -> StoredNutritionObject:
        _validate_key(key, prefix=self._prefix)
        if hashlib.sha256(content).hexdigest() != sha256:
            raise NutritionObjectIntegrityError("Nutrition object content hash mismatch")
        stored = StoredNutritionObject(
            bucket=self.bucket,
            region=self.region,
            key=key,
            etag=hashlib.md5(content, usedforsecurity=False).hexdigest(),
            byte_size=len(content),
            sha256=sha256,
            media_type=media_type,
        )
        existing = self._objects.get(key)
        if existing is not None and existing[0] != content:
            raise NutritionObjectIntegrityError("Existing object differs")
        self._objects[key] = (bytes(content), stored)
        return stored

    async def get(self, *, key: str, max_bytes: int) -> bytes:
        _validate_key(key, prefix=self._prefix)
        stored = self._objects.get(key)
        if stored is None:
            raise NutritionObjectNotFound(key)
        if len(stored[0]) > max_bytes:
            raise NutritionObjectStoreError("cos_object_too_large")
        return stored[0]

    async def head(self, *, key: str) -> StoredNutritionObject | None:
        _validate_key(key, prefix=self._prefix)
        stored = self._objects.get(key)
        return stored[1] if stored is not None else None


def _validate_sha256(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("sha256 must be lowercase hexadecimal")


def _validate_key(key: str, *, prefix: str) -> None:
    if (
        not key
        or len(key) > 1024
        or key.startswith("/")
        or ".." in key.split("/")
        or not key.startswith(prefix + "/")
    ):
        raise ValueError("Invalid nutrition object key")


def _safe_suffix(filename: str) -> str:
    filename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in filename:
        return ""
    suffix = "." + filename.rsplit(".", 1)[-1].casefold()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ""


def _header(response: Mapping[str, object], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in response.items():
        if key.casefold() == wanted and isinstance(value, (str, int)):
            return str(value).strip('"')
    return None


__all__ = [
    "InMemoryNutritionObjectStore",
    "NutritionObjectIntegrityError",
    "NutritionObjectNotFound",
    "NutritionObjectStore",
    "NutritionObjectStoreError",
    "StoredNutritionObject",
    "TencentCosNutritionObjectStore",
]
