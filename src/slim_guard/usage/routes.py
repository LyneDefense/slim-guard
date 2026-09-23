"""Authenticated admin read API; per-user scopes are mandatory on detail queries."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from slim_guard.api.admin_routes import _authenticate
from slim_guard.usage.repository import UsageRepository

router = APIRouter(prefix="/api/admin/usage", tags=["usage"], dependencies=[Depends(_authenticate)])
PageSize = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def dates(start: datetime | None, end: datetime | None) -> None:
    if any(d is not None and d.utcoffset() is None for d in (start, end)):
        raise HTTPException(422, "日期必须带时区")
    if start and end and start >= end:
        raise HTTPException(422, "开始日期必须早于结束日期")


@router.get("/users")
async def users(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: PageSize = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    dates(start, end)
    return await UsageRepository(request.app.state.database).users(
        start=start, end=end, limit=limit, offset=offset
    )


@router.get("/users/{user_id}/requests")
async def requests(
    request: Request,
    user_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: PageSize = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    dates(start, end)
    return await UsageRepository(request.app.state.database).requests(
        user_id=user_id, start=start, end=end, limit=limit, offset=offset
    )


@router.get("/users/{user_id}/requests/{request_id}")
async def detail(
    request: Request, user_id: str, request_id: str, limit: PageSize = 50, offset: Offset = 0
) -> dict[str, Any]:
    return await UsageRepository(request.app.state.database).detail(
        user_id=user_id, request_id=request_id, limit=limit, offset=offset
    )


@router.get("/jobs")
async def jobs(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: PageSize = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    dates(start, end)
    return await UsageRepository(request.app.state.database).requests(
        user_id=None, start=start, end=end, limit=limit, offset=offset
    )


@router.get("/jobs/{job_id}")
async def job(
    request: Request, job_id: str, limit: PageSize = 50, offset: Offset = 0
) -> dict[str, Any]:
    return await UsageRepository(request.app.state.database).detail(
        user_id=None, request_id=job_id, limit=limit, offset=offset
    )
