from __future__ import annotations

from typing import cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.integrations.wecom_kf.crypto import WeComCallbackCrypto
from slim_guard.integrations.wecom_kf.errors import (
    WeComCryptoError,
    WeComMalformedPayloadError,
)
from slim_guard.services.fixed_reply import FixedReplySyncService

router = APIRouter()


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _crypto(request: Request) -> WeComCallbackCrypto:
    crypto = cast(WeComCallbackCrypto | None, request.app.state.wecom_crypto)
    if crypto is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return crypto


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    settings = _settings(request)
    if not settings.wecom_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeCom is not configured",
        )
    database = cast(Database, request.app.state.database)
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return {"status": "ready"}


@router.get("/callbacks/wecom/kf", response_class=PlainTextResponse)
async def verify_wecom_callback(
    request: Request,
    msg_signature: str = Query(alias="msg_signature"),
    timestamp: str = Query(),
    nonce: str = Query(),
    echo_str: str = Query(alias="echostr"),
) -> str:
    try:
        return _crypto(request).verify_url(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echo_str=echo_str,
        )
    except WeComCryptoError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc


@router.post("/callbacks/wecom/kf", response_class=PlainTextResponse)
async def receive_wecom_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(alias="msg_signature"),
    timestamp: str = Query(),
    nonce: str = Query(),
) -> Response:
    settings = _settings(request)
    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > settings.callback_body_limit_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        plaintext = _crypto(request).decrypt_callback(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        )
        event = WeComCallbackCrypto.parse_kf_event(plaintext)
    except WeComMalformedPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    except WeComCryptoError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc

    if event is not None:
        service = cast(FixedReplySyncService | None, request.app.state.sync_service)
        if service is not None:
            background_tasks.add_task(service.handle_callback, event.token, event.open_kfid)
    return PlainTextResponse("success")
