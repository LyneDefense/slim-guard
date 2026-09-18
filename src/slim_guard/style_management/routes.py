from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from slim_guard.api.admin_routes import AdminPrincipal, _authenticate

from .contracts import ExampleInput, ExampleState, ReviewInput, StyleInput
from .repository import Repository

T = TypeVar("T")


def access(
    request: Request, principal: Annotated[AdminPrincipal, Depends(_authenticate)]
) -> AdminPrincipal:
    if request.method != "GET" and request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(403, "CSRF header required")
    return principal


router = APIRouter(
    prefix="/api/admin/styles", tags=["expression-style"], dependencies=[Depends(access)]
)


async def invoke(call: Awaitable[T]) -> T:
    try:
        return await call
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(409, "名称已存在或操作与当前构建冲突") from exc


def repo(request: Request) -> Repository:
    return Repository(request.app.state.database)


@router.get("")
async def styles(request: Request) -> dict[str, Any]:
    return {"items": await repo(request).styles()}


@router.post("", status_code=201)
async def create_style(request: Request, payload: StyleInput) -> dict[str, Any]:
    return await invoke(repo(request).create_style(payload))


@router.get("/{style_id}/examples")
async def examples(
    request: Request,
    style_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    category: str = "",
    source: str = "",
    status: str = "",
) -> dict[str, Any]:
    return await invoke(
        repo(request).examples(
            style_id,
            limit=limit,
            offset=offset,
            q=q,
            category=category,
            source=source,
            status=status,
        )
    )


@router.post("/{style_id}/examples", status_code=201)
async def append(
    request: Request,
    style_id: str,
    payload: ExampleInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(repo(request).append(style_id, payload, principal.username))


@router.patch("/{style_id}/examples/{example_id}")
async def example_state(
    request: Request, style_id: str, example_id: str, payload: ExampleState
) -> dict[str, Any]:
    return await invoke(repo(request).example_state(style_id, example_id, payload))


@router.get("/{style_id}/versions")
async def versions(request: Request, style_id: str) -> dict[str, Any]:
    return {"items": await invoke(repo(request).versions(style_id))}


@router.post("/{style_id}/versions", status_code=202)
async def build(
    request: Request, style_id: str, principal: Annotated[AdminPrincipal, Depends(access)]
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.style_iteration_worker_enabled or not settings.zhipu_is_configured:
        raise HTTPException(409, "风格构建 worker 或模型尚未配置")
    return await invoke(repo(request).build(style_id, principal.username))


@router.get("/{style_id}/versions/{version_id}/cases")
async def cases(
    request: Request,
    style_id: str,
    version_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await invoke(repo(request).cases(style_id, version_id, limit, offset))


@router.post("/{style_id}/reviews/{case_id}")
async def review(
    request: Request,
    style_id: str,
    case_id: str,
    payload: ReviewInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(repo(request).review(style_id, case_id, payload, principal.username))


@router.post("/{style_id}/versions/{version_id}/{action}")
async def action(
    request: Request,
    style_id: str,
    version_id: str,
    action: Literal["publish", "activate", "retry"],
) -> dict[str, Any]:
    return await invoke(repo(request).action(style_id, version_id, action))
