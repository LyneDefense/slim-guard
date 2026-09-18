"""One style management API: material, build process, and final-version review."""

from collections.abc import Awaitable
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError

from slim_guard.api.admin_routes import AdminPrincipal, _authenticate
from slim_guard.expression_style.trainer.contracts import BuildBudget

from .builds import BuildRepository
from .contracts import ExampleInput, ReviewInput, StyleInput
from .corpus import CorpusRepository
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
        raise HTTPException(409, "名称已存在或与其他构建冲突") from exc


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


@router.post("/{style_id}/examples", status_code=201)
async def append(
    request: Request,
    style_id: str,
    payload: ExampleInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(
        CorpusRepository(request.app.state.database).append(style_id, payload, principal.username)
    )


@router.put("/{style_id}/examples/{example_id}")
async def edit(
    request: Request, style_id: str, example_id: str, payload: ExampleInput
) -> dict[str, Any]:
    return await invoke(
        CorpusRepository(request.app.state.database).edit(style_id, example_id, payload)
    )


@router.delete("/{style_id}/examples/{example_id}")
async def delete_example(request: Request, style_id: str, example_id: str) -> dict[str, str]:
    return await invoke(CorpusRepository(request.app.state.database).delete(style_id, example_id))


@router.get("/{style_id}/builds")
async def builds(
    request: Request,
    style_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return await invoke(BuildRepository(request.app.state.database).runs(style_id, limit, offset))


@router.post("/{style_id}/builds", status_code=202)
async def build(
    request: Request, style_id: str, principal: Annotated[AdminPrincipal, Depends(access)]
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.style_iteration_worker_enabled or not settings.zhipu_is_configured:
        raise HTTPException(409, "风格构建 worker 或模型尚未配置")
    key = request.headers.get("Idempotency-Key")
    if key is not None and (not key.strip() or len(key) > 128):
        raise HTTPException(422, "无效幂等键")
    return await invoke(
        BuildRepository(request.app.state.database).build(
            style_id,
            principal.username,
            model=settings.zhipu_text_model,
            request_key=key,
            budget=BuildBudget(
                max_rounds=settings.style_training_max_rounds,
                max_calls=settings.style_training_max_calls,
                max_tokens=settings.style_training_max_tokens,
                max_seconds=settings.style_training_max_seconds,
            ),
        )
    )


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


@router.post("/{style_id}/builds/{run_id}/{action}")
async def build_action(
    request: Request, style_id: str, run_id: str, action: Literal["cancel", "resume"]
) -> dict[str, Any]:
    return await invoke(
        BuildRepository(request.app.state.database).action(style_id, run_id, action)
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


@router.post("/{style_id}/reviews/{case_id}")
async def review(
    request: Request,
    style_id: str,
    case_id: str,
    payload: ReviewInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(
        VersionRepository(request.app.state.database).review(
            style_id, case_id, payload, principal.username
        )
    )


@router.post("/{style_id}/versions/{version_id}/{action}")
async def version_action(
    request: Request, style_id: str, version_id: str, action: Literal["publish", "activate"]
) -> dict[str, Any]:
    return await invoke(
        VersionRepository(request.app.state.database).action(style_id, version_id, action)
    )
