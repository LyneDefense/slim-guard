"""Authenticated entry point for append-only style correction feedback."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from slim_guard.agents.contracts import CommunicationAct
from slim_guard.api.admin_routes import AdminPrincipal, _audit, _authenticate
from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.style_feedback import (
    StyleCorrectionFeedbackInput,
    StyleCorrectionFeedbackRepository,
)

router = APIRouter(prefix="/api/admin/style-feedback", tags=["admin-style-feedback"])


def _repository(request: Request) -> StyleCorrectionFeedbackRepository:
    return StyleCorrectionFeedbackRepository(cast(Database, request.app.state.database))


def _require_csrf(request: Request) -> None:
    if request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF header required")


@router.get("/context")
async def feedback_context(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    settings = cast(Settings, request.app.state.settings)
    options = await _repository(request).profile_options()
    return {
        "runtime_default_profile_version": settings.default_style_profile,
        "suggested_profile_version": options[0] if options else settings.default_style_profile,
        "profile_versions": list(options),
        "development_direct_rollout": settings.app_env.lower() != "production",
    }


@router.get("")
async def list_feedback(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    profile_version: str | None = Query(default=None, min_length=1, max_length=128),
    communication_act: Annotated[CommunicationAct | None, Query()] = None,
) -> dict[str, Any]:
    result = await _repository(request).list(
        limit=limit,
        offset=offset,
        profile_version=profile_version,
        communication_act=(
            communication_act.value if communication_act is not None else None
        ),
    )
    await _audit(
        request,
        principal,
        action="list",
        resource_type="style_correction_feedback",
        resource_id="collection",
    )
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def append_feedback(
    payload: StyleCorrectionFeedbackInput,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        result = await _repository(request).append(payload, actor=principal.username)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid style correction feedback",
        ) from error
    await _audit(
        request,
        principal,
        action="append_feedback",
        resource_type="style_correction_feedback",
        resource_id=result["feedback_id"],
    )
    return result
