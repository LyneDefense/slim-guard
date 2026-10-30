from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from slim_guard.api.mobile_routes import _principal
from slim_guard.features.daily_insights.contracts import DailyInsightsView
from slim_guard.features.daily_insights.service import DailyInsightsService
from slim_guard.mobile.auth import MobilePrincipal

router = APIRouter(prefix="/api/mobile/v1/daily-insights", tags=["daily-insights"])


def _service(request: Request) -> DailyInsightsService:
    service = getattr(request.app.state, "daily_insights", None)
    if not isinstance(service, DailyInsightsService):
        raise HTTPException(503, "今日总结暂不可用")
    return service


@router.get("", response_model=DailyInsightsView)
async def daily_insights(
    request: Request,
    principal: Annotated[MobilePrincipal, Depends(_principal)],
) -> DailyInsightsView:
    return await _service(request).get(principal.user_id)


@router.post("/retry", response_model=DailyInsightsView)
async def retry_daily_insights(
    request: Request,
    principal: Annotated[MobilePrincipal, Depends(_principal)],
) -> DailyInsightsView:
    return await _service(request).get(principal.user_id, retry=True)
