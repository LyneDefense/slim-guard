from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from slim_guard.api.mobile_routes import _mobile, _principal
from slim_guard.features.health_records.meal.editing import MealEditRequest
from slim_guard.features.health_records.queries import RecordPage, RecordQuery
from slim_guard.features.health_records.references import RecordConflict, RecordError, RecordKind
from slim_guard.features.health_records.views import RecordView
from slim_guard.mobile.auth import MobilePrincipal

router = APIRouter(prefix="/api/mobile/v1/records", tags=["mobile-records"])


@router.patch("/meal/{record_id}", response_model=RecordView)
async def edit_meal(
    request: Request,
    record_id: str,
    body: MealEditRequest,
    principal: Annotated[MobilePrincipal, Depends(_principal)],
) -> RecordView:
    records = _mobile(request).records
    try:
        await records.get(principal.user_id, "meal", record_id)
    except RecordError as exc:
        raise HTTPException(404, "餐食记录不存在") from exc
    try:
        return await records.edit_meal(principal.user_id, record_id, body)
    except RecordConflict as exc:
        raise HTTPException(
            409,
            detail={
                "code": "record_conflict",
                "message": "这条记录已更新，请重新打开后修改。",
            },
        ) from exc
    except RecordError as exc:
        raise HTTPException(
            422,
            detail={
                "code": "invalid_meal_edit",
                "message": str(exc),
            },
        ) from exc


@router.get("", response_model=RecordPage)
async def records(
    request: Request,
    query: Annotated[RecordQuery, Query()],
    principal: Annotated[MobilePrincipal, Depends(_principal)],
) -> RecordPage:
    try:
        return await _mobile(request).records.search(principal.user_id, query)
    except RecordError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{kind}/{record_id}", response_model=RecordView)
async def record(
    request: Request,
    kind: RecordKind,
    record_id: str,
    principal: Annotated[MobilePrincipal, Depends(_principal)],
) -> RecordView:
    try:
        return await _mobile(request).records.get(principal.user_id, kind, record_id)
    except RecordError as exc:
        raise HTTPException(404, str(exc)) from exc
