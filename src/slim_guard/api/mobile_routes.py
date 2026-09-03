from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, TypeVar, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from slim_guard.config import Settings
from slim_guard.mobile.auth import (
    IssuedMobileTokens,
    MobileAuthError,
    MobileAuthService,
    MobilePrincipal,
)
from slim_guard.mobile.contracts import (
    AuthTokenView,
    ChatHistoryView,
    ChatRequest,
    ChatResponse,
    MemoryView,
    MobileUserView,
    OtpChallengeView,
    OtpRequest,
    OtpVerifyRequest,
    ProfileUpdateRequest,
    ProgressView,
    RefreshRequest,
    RoutineUpdateRequest,
    RoutineView,
    TodayView,
)
from slim_guard.mobile.service import MobileApplicationService, MobileServiceError

router = APIRouter(prefix="/api/mobile/v1", tags=["mobile"])
T = TypeVar("T")


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _auth(request: Request) -> MobileAuthService:
    service = cast(MobileAuthService | None, request.app.state.mobile_auth)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "mobile_api_unavailable", "message": "Mobile API is unavailable"},
        )
    return service


def _mobile(request: Request) -> MobileApplicationService:
    service = cast(MobileApplicationService | None, request.app.state.mobile_service)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "mobile_api_unavailable", "message": "Mobile API is unavailable"},
        )
    return service


async def _principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> MobilePrincipal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "Sign in is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await _auth(request).authenticate(
            authorization.removeprefix("Bearer ").strip(),
            now=datetime.now(UTC),
        )
    except MobileAuthError as exc:
        raise _auth_http_error(exc) from exc


Principal = Annotated[MobilePrincipal, Depends(_principal)]


@router.post("/auth/otp/request", response_model=OtpChallengeView)
async def request_otp(payload: OtpRequest, request: Request) -> OtpChallengeView:
    try:
        challenge = await _auth(request).request_otp(payload.phone, now=datetime.now(UTC))
    except MobileAuthError as exc:
        raise _auth_http_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "otp_delivery_failed", "message": "Unable to send login code"},
        ) from exc
    settings = _settings(request)
    return OtpChallengeView(
        challenge_id=challenge.id,
        expires_in_seconds=challenge.expires_in_seconds,
        retry_after_seconds=challenge.retry_after_seconds,
        debug_code=(
            challenge.code
            if settings.app_env != "production" and settings.mobile_dev_otp_enabled
            else None
        ),
    )


@router.post("/auth/otp/verify", response_model=AuthTokenView)
async def verify_otp(payload: OtpVerifyRequest, request: Request) -> AuthTokenView:
    try:
        issued = await _auth(request).verify_otp(
            challenge_id=payload.challenge_id,
            code=payload.code,
            device_label=payload.device_label,
            now=datetime.now(UTC),
        )
    except MobileAuthError as exc:
        raise _auth_http_error(exc) from exc
    return await _token_view(issued, _mobile(request))


@router.post("/auth/refresh", response_model=AuthTokenView)
async def refresh(payload: RefreshRequest, request: Request) -> AuthTokenView:
    try:
        issued = await _auth(request).refresh(payload.refresh_token, now=datetime.now(UTC))
    except MobileAuthError as exc:
        raise _auth_http_error(exc) from exc
    return await _token_view(issued, _mobile(request))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: Principal, request: Request) -> None:
    await _auth(request).logout(principal, now=datetime.now(UTC))


@router.get("/me", response_model=MobileUserView)
async def me(principal: Principal, request: Request) -> MobileUserView:
    return await _mobile_call(_mobile(request).user(principal.user_id))


@router.patch("/me", response_model=MobileUserView)
async def update_me(
    payload: ProfileUpdateRequest,
    principal: Principal,
    request: Request,
) -> MobileUserView:
    return await _mobile_call(
        _mobile(request).update_profile(principal.user_id, nickname=payload.nickname)
    )


@router.post("/chat/messages", response_model=ChatResponse)
async def send_chat_message(
    payload: ChatRequest,
    principal: Principal,
    request: Request,
) -> ChatResponse:
    return await _mobile_call(_mobile(request).chat(principal.user_id, payload))


@router.get("/chat/requests/{idempotency_key}", response_model=ChatResponse)
async def get_chat_request(
    idempotency_key: str,
    principal: Principal,
    request: Request,
) -> ChatResponse:
    return await _mobile_call(
        _mobile(request).chat_request(principal.user_id, idempotency_key)
    )


@router.get("/chat/messages", response_model=ChatHistoryView)
async def chat_history(
    principal: Principal,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> ChatHistoryView:
    return await _mobile_call(_mobile(request).history(principal.user_id, limit=limit))


@router.get("/today", response_model=TodayView)
async def today(principal: Principal, request: Request) -> TodayView:
    return await _mobile_call(
        _mobile(request).today(principal.user_id, now=datetime.now(UTC))
    )


@router.get("/progress", response_model=ProgressView)
async def progress(
    principal: Principal,
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
) -> ProgressView:
    return await _mobile_call(_mobile(request).progress(principal.user_id, limit=limit))


@router.get("/memories", response_model=list[MemoryView])
async def memories(principal: Principal, request: Request) -> list[MemoryView]:
    return await _mobile_call(_mobile(request).memories(principal.user_id))


@router.get("/routine", response_model=RoutineView)
async def routine(principal: Principal, request: Request) -> RoutineView:
    return await _mobile_call(_mobile(request).routine(principal.user_id))


@router.put("/routine", response_model=RoutineView)
async def update_routine(
    payload: RoutineUpdateRequest,
    principal: Principal,
    request: Request,
) -> RoutineView:
    return await _mobile_call(
        _mobile(request).update_routine(principal.user_id, payload)
    )


async def _token_view(
    issued: IssuedMobileTokens,
    mobile: MobileApplicationService,
) -> AuthTokenView:
    user = await mobile.user(issued.user_id)
    return AuthTokenView(
        access_token=issued.access_token,
        expires_in_seconds=issued.access_expires_in_seconds,
        refresh_token=issued.refresh_token,
        user=user,
    )


async def _mobile_call(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except MobileServiceError as exc:
        code_status = {
            "user_not_found": status.HTTP_404_NOT_FOUND,
            "request_not_found": status.HTTP_404_NOT_FOUND,
            "idempotency_key_reused": status.HTTP_409_CONFLICT,
            "agent_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
            "mobile_agent_failed": status.HTTP_502_BAD_GATEWAY,
            "invalid_image": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_image_size": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        }
        raise HTTPException(
            status_code=code_status.get(exc.code, status.HTTP_400_BAD_REQUEST),
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _auth_http_error(exc: MobileAuthError) -> HTTPException:
    code_status = {
        "invalid_phone": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "otp_too_frequent": status.HTTP_429_TOO_MANY_REQUESTS,
        "otp_rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
        "otp_delivery_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
        "otp_not_found": status.HTTP_404_NOT_FOUND,
        "otp_invalid": status.HTTP_401_UNAUTHORIZED,
        "otp_expired": status.HTTP_401_UNAUTHORIZED,
        "otp_not_active": status.HTTP_401_UNAUTHORIZED,
        "invalid_access_token": status.HTTP_401_UNAUTHORIZED,
        "access_token_expired": status.HTTP_401_UNAUTHORIZED,
        "session_inactive": status.HTTP_401_UNAUTHORIZED,
        "invalid_refresh_token": status.HTTP_401_UNAUTHORIZED,
        "refresh_token_expired": status.HTTP_401_UNAUTHORIZED,
        "account_unavailable": status.HTTP_403_FORBIDDEN,
    }
    return HTTPException(
        status_code=code_status.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": str(exc)},
        headers={"WWW-Authenticate": "Bearer"}
        if code_status.get(exc.code) == status.HTTP_401_UNAUTHORIZED
        else None,
    )
