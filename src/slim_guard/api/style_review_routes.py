"""Authenticated review of previously imported synthetic style comparisons."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from slim_guard.agents.contracts import CommunicationAct
from slim_guard.api.admin_routes import AdminPrincipal, _audit, _authenticate
from slim_guard.db.session import Database
from slim_guard.style_reviews import (
    StyleABCaseConflict,
    StyleABCaseNotFound,
    StyleABHumanScore,
    StyleABReviewRepository,
)

router = APIRouter(prefix="/api/admin/style-ab", tags=["admin-style-review"])


def _repository(request: Request) -> StyleABReviewRepository:
    return StyleABReviewRepository(cast(Database, request.app.state.database))


def _require_csrf(request: Request) -> None:
    # A custom header is not sendable by a cross-origin HTML form. The existing
    # admin login cookie is also HttpOnly and SameSite=Strict.
    if request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF header required")


@router.get("/cases")
async def list_cases(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    candidate_profile_version: str | None = Query(default=None, min_length=1, max_length=128),
    communication_act: Annotated[CommunicationAct | None, Query()] = None,
    decision: Literal["pending", "accept", "reject"] | None = Query(default=None),
) -> dict[str, Any]:
    del principal
    return await _repository(request).list_cases(
        limit=limit,
        offset=offset,
        candidate_profile_version=candidate_profile_version,
        communication_act=communication_act.value if communication_act is not None else None,
        decision=decision,
    )


@router.get("/statistics")
async def statistics(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    candidate_profile_version: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict[str, Any]:
    del principal
    return await _repository(request).statistics(
        candidate_profile_version=candidate_profile_version
    )


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).get_case(case_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="A/B case not found")
    await _audit(
        request,
        principal,
        action="view",
        resource_type="style_ab_case",
        resource_id=case_id,
    )
    return result


@router.post("/cases/{case_id}/reviews", status_code=status.HTTP_201_CREATED)
async def append_review(
    case_id: str,
    payload: StyleABHumanScore,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        result = await _repository(request).submit_review(
            case_id=case_id,
            actor=principal.username,
            score=payload,
        )
    except StyleABCaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="A/B case not found"
        ) from error
    except StyleABCaseConflict as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review changed; reload the latest review before submitting a correction",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid review"
        ) from error
    await _audit(
        request,
        principal,
        action="append_review",
        resource_type="style_ab_case",
        resource_id=case_id,
    )
    return result
