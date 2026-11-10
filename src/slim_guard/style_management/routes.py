"""Style catalog and read-only history of the retired construction workflow."""

from collections.abc import Awaitable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from slim_guard.api.admin_routes import AdminPrincipal, _authenticate

from .builds import BuildRepository
from .contracts import StyleInput
from .corpus import CorpusRepository
from .studio.routes import router as studio_router
from .versions import VersionRepository

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
        raise HTTPException(409, "名称已存在") from exc


@router.get("")
async def styles(request: Request) -> dict[str, Any]:
    return {"items": await CorpusRepository(request.app.state.database).styles()}


@router.post("", status_code=201)
async def create_style(request: Request, payload: StyleInput) -> dict[str, Any]:
    return await invoke(CorpusRepository(request.app.state.database).create_style(payload))


@router.get("/{style_id}/examples")
async def examples(
    request: Request,
    style_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    participation: Literal["", "used", "unused"] = "",
    role: str = "",
) -> dict[str, Any]:
    return await invoke(
        CorpusRepository(request.app.state.database).examples(
            style_id, limit=limit, offset=offset, q=q, participation=participation, role=role
        )
    )


@router.get("/{style_id}/builds")
async def builds(
    request: Request,
    style_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await invoke(BuildRepository(request.app.state.database).runs(style_id, limit, offset))


@router.get("/{style_id}/builds/{run_id}")
async def build_detail(request: Request, style_id: str, run_id: str) -> dict[str, Any]:
    return await invoke(BuildRepository(request.app.state.database).detail(style_id, run_id))


@router.get("/{style_id}/builds/{run_id}/events")
async def build_events(
    request: Request,
    style_id: str,
    run_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    return await invoke(
        BuildRepository(request.app.state.database).events(style_id, run_id, after, limit)
    )


@router.get("/{style_id}/builds/{run_id}/artifacts")
async def build_artifact(
    request: Request,
    style_id: str,
    run_id: str,
    key: str = Query(max_length=150),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await invoke(
        BuildRepository(request.app.state.database).artifact(style_id, run_id, key, limit, offset)
    )


@router.get("/{style_id}/versions")
async def versions(request: Request, style_id: str) -> dict[str, Any]:
    return {"items": await invoke(VersionRepository(request.app.state.database).versions(style_id))}


@router.get("/{style_id}/versions/{version_id}/cases")
async def cases(
    request: Request,
    style_id: str,
    version_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await invoke(
        VersionRepository(request.app.state.database).cases(style_id, version_id, limit, offset)
    )


router.include_router(studio_router)
