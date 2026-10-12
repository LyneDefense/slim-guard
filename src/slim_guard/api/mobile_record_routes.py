from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from slim_guard.api.mobile_routes import _mobile, _principal
from slim_guard.features.health_records.queries import RecordPage, RecordQuery
from slim_guard.features.health_records.references import RecordError, RecordKind
from slim_guard.features.health_records.views import RecordView
from slim_guard.mobile.auth import MobilePrincipal

router = APIRouter(prefix="/api/mobile/v1/records", tags=["mobile-records"])


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
