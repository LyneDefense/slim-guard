"""Authenticated role editing and isolated model actions, without a build worker."""

from collections.abc import Awaitable
from typing import Annotated, Any, TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from slim_guard.api.admin_routes import AdminPrincipal, _authenticate
from slim_guard.expression_style.persona import compile_persona

from .contracts import (
    CheckInput,
    ContentInput,
    DraftInput,
    PreviewInput,
    PublishInput,
    RestoreInput,
)
from .errors import DraftConflictError
from .ports import StudioModelError, StyleStudioModelPort
from .repository import StudioRepository

T = TypeVar("T")


def access(
    request: Request, principal: Annotated[AdminPrincipal, Depends(_authenticate)]
) -> AdminPrincipal:
    if request.method != "GET" and request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(403, "CSRF header required")
    return principal


router = APIRouter(prefix="/{style_id}/studio", dependencies=[Depends(access)])


async def invoke(call: Awaitable[T]) -> T:
    try:
        return await call
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DraftConflictError as exc:
        raise HTTPException(409, str(exc), headers={"X-SlimGuard-Error": "draft_conflict"}) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            409,
            "草稿已被其他操作更新，请载入最新草稿后重试",
            headers={"X-SlimGuard-Error": "draft_conflict"},
        ) from exc
    except StudioModelError as exc:
        raise HTTPException(502, str(exc)) from exc


def repository(request: Request) -> StudioRepository:
    return StudioRepository(request.app.state.database)


def model(request: Request) -> StyleStudioModelPort:
    service = getattr(request.app.state, "style_studio_model", None)
    if service is None:
        raise HTTPException(503, "试聊模型尚未配置，仍可保存草稿和查看历史版本")
    return cast(StyleStudioModelPort, service)


@router.get("")
async def studio(request: Request, style_id: str) -> dict[str, Any]:
    return await invoke(repository(request).studio(style_id))


@router.put("/draft")
async def save(
    request: Request,
    style_id: str,
    payload: DraftInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(repository(request).save(style_id, payload, principal.username))


@router.post("/preview")
async def preview(request: Request, style_id: str, payload: PreviewInput) -> dict[str, Any]:
    await invoke(repository(request).ensure_style(style_id))

    async def run() -> dict[str, Any]:
        compile_persona(payload.content)
        result = await model(request).preview(
            content=payload.content, user_input=payload.user_input, history=payload.history
        )
        return {**result, "content_hash": payload.content.content_hash}

    return await invoke(run())


@router.post("/organize")
async def organize(request: Request, style_id: str, payload: ContentInput) -> dict[str, Any]:
    await invoke(repository(request).ensure_style(style_id))
    return await invoke(model(request).organize(content=payload.content))


@router.post("/check")
async def check(
    request: Request,
    style_id: str,
    payload: CheckInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    repo = repository(request)
    content = await invoke(repo.checked_content(style_id, payload))
    service = model(request)

    async def run() -> dict[str, Any]:
        compile_persona(content)
        signature = service.signature
        result = await service.check(content=content)
        return await repo.record_check(style_id, payload, result, signature, principal.username)

    return await invoke(run())


@router.post("/publish")
async def publish(
    request: Request,
    style_id: str,
    payload: PublishInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(
        repository(request).publish(style_id, payload, model(request).signature, principal.username)
    )


@router.get("/versions")
async def versions(request: Request, style_id: str) -> dict[str, Any]:
    return await invoke(repository(request).versions(style_id))


@router.get("/legacy-examples")
async def legacy_examples(request: Request, style_id: str) -> dict[str, Any]:
    return await invoke(repository(request).legacy_examples(style_id))


@router.post("/restore")
async def restore(
    request: Request,
    style_id: str,
    payload: RestoreInput,
    principal: Annotated[AdminPrincipal, Depends(access)],
) -> dict[str, Any]:
    return await invoke(repository(request).restore(style_id, payload, principal.username))
