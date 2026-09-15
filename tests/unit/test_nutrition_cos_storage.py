from __future__ import annotations

import hashlib

import pytest
import requests

from slim_guard.nutrition_rag.storage import (
    NutritionObjectIntegrityError,
    NutritionObjectStoreError,
    TencentCosNutritionObjectStore,
)


def _store() -> TencentCosNutritionObjectStore:
    # Exercise the real SDK and HTTP header preparation, not a fake put_object.
    return TencentCosNutritionObjectStore(
        region="ap-shanghai",
        bucket="nutrition-test-1234567890",
        prefix="slim-guard/nutrition-knowledge",
        secret_id="test-secret-id",
        secret_key="test-secret-key",
    )


async def test_cos_upload_prepares_valid_headers_and_reuses_existing_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "# 营养资料\n\n保持食物多样，合理搭配蔬菜和全谷物。".encode()
    digest = hashlib.sha256(content).hexdigest()
    sent: list[requests.PreparedRequest] = []
    uploaded = False

    def send(
        session: requests.Session, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        nonlocal uploaded
        sent.append(request)
        response = requests.Response()
        response.request = request
        response._content = b""
        if request.method == "HEAD":
            response.status_code = 200 if uploaded else 404
            if uploaded:
                response.headers.update(
                    {
                        "ETag": '"test-etag"',
                        "Content-Length": str(len(content)),
                        "Content-Type": "text/markdown",
                        "x-cos-meta-sha256": digest,
                    }
                )
        else:
            assert request.method == "PUT"
            assert request.body == content
            assert request.headers["Content-Length"] == str(len(content))
            assert (
                requests.utils.to_native_string(request.headers["Content-Type"]) == "text/markdown"
            )
            assert requests.utils.to_native_string(request.headers["x-cos-meta-sha256"]) == digest
            assert (
                requests.utils.to_native_string(request.headers["x-cos-server-side-encryption"])
                == "AES256"
            )
            assert request.headers["Content-MD5"]
            assert request.headers["Authorization"]
            uploaded = True
            response.status_code = 200
            response.headers["ETag"] = '"test-etag"'
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    store = _store()
    key = store.object_key(sha256=digest, filename="guide.md")
    stored = await store.put(key=key, content=content, sha256=digest, media_type="text/markdown")
    existing = await store.put(key=key, content=content, sha256=digest, media_type="text/markdown")

    assert stored == existing
    assert stored.byte_size == len(content)
    assert stored.sha256 == digest
    assert [request.method for request in sent] == ["HEAD", "PUT", "HEAD"]


async def test_cos_upload_rejects_bad_hash_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(*args: object, **kwargs: object) -> requests.Response:
        pytest.fail("Invalid content must never be sent to COS")

    monkeypatch.setattr(requests.Session, "send", send)
    store = _store()
    digest = hashlib.sha256(b"original").hexdigest()
    with pytest.raises(NutritionObjectIntegrityError, match="content hash mismatch"):
        await store.put(
            key=store.object_key(sha256=digest, filename="guide.md"),
            content=b"changed",
            sha256=digest,
            media_type="text/markdown",
        )


async def test_cos_upload_permission_error_is_reported_without_false_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(
        session: requests.Session, request: requests.PreparedRequest, **kwargs: object
    ) -> requests.Response:
        response = requests.Response()
        response.request = request
        response._content = b"<Error><Code>AccessDenied</Code><Message>Denied</Message></Error>"
        response.status_code = 404 if request.method == "HEAD" else 403
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    store = _store()
    content = b"nutrition source"
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(NutritionObjectStoreError, match="cos_put_failed") as error:
        await store.put(
            key=store.object_key(sha256=digest, filename="guide.md"),
            content=content,
            sha256=digest,
            media_type="text/markdown",
        )
    assert type(error.value.__cause__).__name__ == "CosServiceError"
