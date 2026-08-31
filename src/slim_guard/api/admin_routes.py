from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from slim_guard.admin.auth import ADMIN_SESSION_COOKIE, AdminSessionCodec
from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.config import Settings
from slim_guard.db.session import Database

router = APIRouter(prefix="/api/admin", tags=["admin"])


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    username: str
    expires_at: int


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=4096)


def _session_codec(settings: Settings) -> AdminSessionCodec:
    return AdminSessionCodec(
        password=settings.admin_password,
        ttl_seconds=settings.admin_session_ttl_hours * 3600,
    )


def _configured_settings(request: Request) -> Settings:
    settings = cast(Settings, request.app.state.settings)
    if not settings.admin_is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin access is not configured",
        )
    return settings


def _authenticate(request: Request) -> AdminPrincipal:
    settings = _configured_settings(request)
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    session = _session_codec(settings).verify(
        token,
        expected_username=settings.admin_username,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin session is missing or expired",
        )
    return AdminPrincipal(username=session.username, expires_at=session.expires_at)


def _repository(request: Request) -> AdminQueryRepository:
    return AdminQueryRepository(cast(Database, request.app.state.database))


def _remote_ref(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    remote = forwarded or (request.client.host if request.client is not None else "")
    if not remote:
        return None
    return hashlib.sha256(remote.encode()).hexdigest()[:12]


async def _audit(
    request: Request,
    principal: AdminPrincipal,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    await _repository(request).audit(
        actor=principal.username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        trace_id=trace_id,
        remote_ref=_remote_ref(request),
    )


@router.post("/auth/login")
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
) -> dict[str, str | int]:
    settings = _configured_settings(request)
    valid = secrets.compare_digest(
        payload.username.encode(), settings.admin_username.encode()
    ) and secrets.compare_digest(payload.password.encode(), settings.admin_password.encode())
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )

    issued_at = int(time.time())
    max_age = settings.admin_session_ttl_hours * 3600
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=_session_codec(settings).issue(settings.admin_username, now=issued_at),
        max_age=max_age,
        httponly=True,
        secure=settings.app_env.lower() == "production",
        samesite="strict",
        path="/api/admin",
    )
    return {"username": settings.admin_username, "expires_at": issued_at + max_age}


@router.post("/auth/logout")
async def admin_logout(
    request: Request,
    response: Response,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, bool]:
    del principal
    settings = _configured_settings(request)
    response.delete_cookie(
        key=ADMIN_SESSION_COOKIE,
        httponly=True,
        secure=settings.app_env.lower() == "production",
        samesite="strict",
        path="/api/admin",
    )
    return {"logged_out": True}


@router.get("/session")
async def admin_session(
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, str | int]:
    return {"username": principal.username, "expires_at": principal.expires_at}


@router.get("/users")
async def list_users(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    search: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    return await _repository(request).list_users(search=search, limit=limit, offset=offset)


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).get_user(user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _audit(
        request,
        principal,
        action="view",
        resource_type="user",
        resource_id=user_id,
        user_id=user_id,
    )
    return result


@router.get("/users/{user_id}/traces")
async def list_user_traces(
    user_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    generation_status: str | None = Query(default=None, max_length=32),
    delivery_status: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    del principal
    result = await _repository(request).list_traces(
        user_id=user_id,
        limit=limit,
        offset=offset,
        generation_status=generation_status,
        delivery_status=delivery_status,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return result


@router.get("/users/{user_id}/traces/{trace_id}")
async def get_user_trace(
    user_id: str,
    trace_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).get_trace(user_id=user_id, trace_id=trace_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="trace",
        resource_id=trace_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    return result


@router.get("/users/{user_id}/memories")
async def list_user_memories(
    user_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> list[dict[str, Any]]:
    result = await _repository(request).list_memories(user_id=user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="memories",
        resource_id=user_id,
        user_id=user_id,
    )
    return result


@router.get("/users/{user_id}/records")
async def list_user_records(
    user_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).list_records(user_id=user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="records",
        resource_id=user_id,
        user_id=user_id,
    )
    return result


@router.get("/users/{user_id}/routines")
async def list_user_routines(
    user_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).list_routines(user_id=user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return result
