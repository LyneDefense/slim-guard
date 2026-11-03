"""Authenticated monitoring endpoints with audited sensitive reads."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from slim_guard.admin.calls.repository import CallMonitorRepository
from slim_guard.api.admin_routes import AdminPrincipal, _audit, _authenticate
from slim_guard.domain.assets.repository import ImageAssetRepository

router = APIRouter(prefix="/api/admin/calls", tags=["call-monitor"])
Principal = Annotated[AdminPrincipal, Depends(_authenticate)]


@router.get("")
async def calls(
    request: Request,
    principal: Principal,
    search: str = Query(default="", max_length=256),
    user_id: str | None = Query(default=None, max_length=128),
    result: Literal["all", "issues", "running"] = "all",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if any(d is not None and d.utcoffset() is None for d in (start, end)) or (
        start and end and start >= end
    ):
        raise HTTPException(422, "时间范围必须带时区，且开始时间早于结束时间")
    data = await CallMonitorRepository(request.app.state.database).list_calls(
        search=search.strip(),
        user_id=user_id,
        result=result,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="call_list",
        resource_id=user_id or "all",
        user_id=user_id,
    )
    return data


@router.get("/{user_id}/traces/{trace_id}")
async def detail(
    request: Request, user_id: str, trace_id: str, principal: Principal
) -> dict[str, Any]:
    data = await CallMonitorRepository(request.app.state.database).detail(
        user_id=user_id, trace_id=trace_id
    )
    if data is None:
        raise HTTPException(404)
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="trace",
        resource_id=trace_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    return data


@router.get("/{user_id}/traces/{trace_id}/images/{asset_id}")
async def image(
    request: Request, user_id: str, trace_id: str, asset_id: str, principal: Principal
) -> Response:
    data = await CallMonitorRepository(request.app.state.database).detail(
        user_id=user_id, trace_id=trace_id
    )
    if data is None or not any(i.get("asset_id") == asset_id for i in data["input"]["images"]):
        raise HTTPException(404)
    asset = await ImageAssetRepository(request.app.state.database).get_for_user(
        asset_id, user_id=user_id, at=datetime.now().astimezone()
    )
    if asset is None:
        raise HTTPException(404)
    await _audit(
        request,
        principal,
        action="view_sensitive",
        resource_type="trace_image",
        resource_id=asset_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    return Response(
        asset.content,
        media_type=asset.ref.mime_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
