"""Authenticated self-service Style Profile iteration control plane."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from slim_guard.api.admin_routes import AdminPrincipal, _audit, _authenticate
from slim_guard.config import Settings
from slim_guard.db.session import Database
from slim_guard.style_iterations import (
    StyleIterationAction,
    StyleIterationConflict,
    StyleIterationCreate,
    StyleIterationNotFound,
    StyleIterationRepository,
)

router = APIRouter(prefix="/api/admin/style-iterations", tags=["admin-style-iterations"])


def _repository(request: Request) -> StyleIterationRepository:
    return StyleIterationRepository(cast(Database, request.app.state.database))


def _require_csrf(request: Request) -> None:
    if request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF header required")


@router.get("/context")
async def context(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    settings = cast(Settings, request.app.state.settings)
    return await _repository(request).context(
        fallback_version=settings.default_style_profile,
        model_configured=settings.zhipu_is_configured,
    )


@router.get("")
async def list_runs(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    return await _repository(request).list_runs(limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: StyleIterationCreate,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    settings = cast(Settings, request.app.state.settings)
    try:
        result = await _repository(request).create_run(
            payload,
            actor=principal.username,
            model_configured=settings.zhipu_is_configured,
        )
    except StyleIterationConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid style iteration request",
        ) from error
    await _audit(
        request,
        principal,
        action="create",
        resource_type="style_iteration",
        resource_id=result["run_id"],
    )
    return result


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    try:
        result = await _repository(request).get_run(run_id, include_artifacts=True)
    except StyleIterationNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await _audit(
        request,
        principal,
        action="view",
        resource_type="style_iteration",
        resource_id=run_id,
    )
    return result


@router.get("/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    after_sequence: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    try:
        items = await _repository(request).events(run_id, after_sequence=after_sequence)
    except StyleIterationNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return {"items": items}


async def _run_action(
    action: str,
    run_id: str,
    payload: StyleIterationAction,
    request: Request,
    principal: AdminPrincipal,
) -> dict[str, Any]:
    try:
        if action == "cancel":
            result = await _repository(request).cancel(
                run_id,
                actor=principal.username,
                reason=payload.reason,
            )
        else:
            result = await _repository(request).retry(
                run_id,
                actor=principal.username,
                reason=payload.reason,
            )
    except StyleIterationNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except StyleIterationConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await _audit(
        request,
        principal,
        action=action,
        resource_type="style_iteration",
        resource_id=run_id,
    )
    return result


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    payload: StyleIterationAction,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    return await _run_action("cancel", run_id, payload, request, principal)


@router.post("/{run_id}/retry")
async def retry_run(
    run_id: str,
    payload: StyleIterationAction,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    return await _run_action("retry", run_id, payload, request, principal)
