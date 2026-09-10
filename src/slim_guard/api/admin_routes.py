from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from slim_guard.admin.auth import ADMIN_SESSION_COOKIE, AdminSessionCodec
from slim_guard.admin.repository import AdminQueryRepository
from slim_guard.agents.contracts import AgentArtifact, ArtifactProducerRole
from slim_guard.agents.dish_recognition import (
    DishRecognitionCorrection,
    DishRecognitionCorrectionItem,
    DishRecognitionResult,
)
from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.orchestration.repository import OrchestrationRepository

router = APIRouter(prefix="/api/admin", tags=["admin"])


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    username: str
    expires_at: int


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=4096)


class DishRecognitionCorrectionRequest(BaseModel):
    corrected_dishes: tuple[DishRecognitionCorrectionItem, ...] = Field(min_length=1, max_length=20)
    comment: str = Field(min_length=3, max_length=2000)


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


def _require_csrf(request: Request) -> None:
    if request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF header required")


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


@router.get("/metrics/workflows")
async def workflow_metrics(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    window_days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    del principal
    return await _repository(request).workflow_review_metrics(window_days=window_days)


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
    mode: str | None = Query(default=None, max_length=32),
    agent_failure: bool | None = Query(default=None),
    rag: bool | None = Query(default=None),
    repair: bool | None = Query(default=None),
    degraded: bool | None = Query(default=None),
    graph_version: str | None = Query(default=None, max_length=128),
    agent_version: str | None = Query(default=None, max_length=128),
    profile_version: str | None = Query(default=None, max_length=128),
    dish_confirmation: str | None = Query(default=None, max_length=32),
    dish_match: str | None = Query(default=None, max_length=32),
    dish_suitability: str | None = Query(default=None, max_length=32),
    review_verdict: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    del principal
    result = await _repository(request).list_traces(
        user_id=user_id,
        limit=limit,
        offset=offset,
        generation_status=generation_status,
        delivery_status=delivery_status,
        mode=mode,
        agent_failure=agent_failure,
        rag=rag,
        repair=repair,
        degraded=degraded,
        graph_version=graph_version,
        agent_version=agent_version,
        profile_version=profile_version,
        dish_confirmation=dish_confirmation,
        dish_match=dish_match,
        dish_suitability=dish_suitability,
        review_verdict=review_verdict,
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


@router.post(
    "/users/{user_id}/traces/{trace_id}/dish-recognition-corrections/{artifact_id}",
    status_code=status.HTTP_201_CREATED,
)
async def append_dish_recognition_correction(
    user_id: str,
    trace_id: str,
    artifact_id: str,
    payload: DishRecognitionCorrectionRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    trace = await _repository(request).get_trace(user_id=user_id, trace_id=trace_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    turn = trace.get("turn")
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    if not isinstance(turn_id, str):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trace has no agent turn to attach the correction to",
        )

    database = cast(Database, request.app.state.database)
    orchestration = OrchestrationRepository(database)
    source = await orchestration.get_artifact(artifact_id)
    if (
        source is None
        or source.turn_id != turn_id
        or source.artifact_type.lower().replace("_", "") != "dishrecognition"
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dish recognition artifact was not found in this trace",
        )
    try:
        recognition = DishRecognitionResult.model_validate(source.payload)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dish recognition artifact failed schema validation",
        ) from error
    expected_refs = {item.dish_ref for item in recognition.dishes}
    submitted_refs = {item.dish_ref for item in payload.corrected_dishes}
    if len(submitted_refs) != len(payload.corrected_dishes) or submitted_refs != expected_refs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Correction must provide exactly one name for every recognized dish",
        )

    correction = DishRecognitionCorrection(
        recognition_artifact_id=source.artifact_id,
        corrected_dishes=payload.corrected_dishes,
        reviewer=principal.username,
        comment=payload.comment,
    )
    created_at = datetime.now(UTC)
    artifact = AgentArtifact.create(
        artifact_id=f"dish-correction-{uuid.uuid4().hex}",
        turn_id=turn_id,
        producer_role=ArtifactProducerRole.ADMIN_REVIEWER,
        artifact_type="dish_recognition_correction",
        schema_version="1",
        payload=correction.model_dump(mode="json"),
        parent_artifact_ids=(source.artifact_id,),
        created_at=created_at,
    )
    await orchestration.append_artifact(artifact)
    await _audit(
        request,
        principal,
        action="append_dish_recognition_correction",
        resource_type="dish_recognition_artifact",
        resource_id=artifact.artifact_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    return {
        **artifact.model_dump(mode="json"),
        "invocation_id": None,
        "body_redacted": True,
        "integrity_status": "verified",
    }


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
