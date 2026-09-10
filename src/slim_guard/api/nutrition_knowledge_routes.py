from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal, cast
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from slim_guard.api.admin_routes import AdminPrincipal, _audit, _authenticate
from slim_guard.config import Settings
from slim_guard.nutrition_rag.repository import (
    NutritionAsset,
    NutritionRagConflict,
    NutritionRagGovernanceError,
    NutritionRagNotFound,
    NutritionRagRepository,
)
from slim_guard.nutrition_rag.retrieval import HybridNutritionRagService
from slim_guard.nutrition_rag.storage import NutritionObjectStore

router = APIRouter(
    prefix="/api/admin/nutrition-knowledge",
    tags=["admin-nutrition-knowledge"],
)


class SourceImportMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    publisher: str = Field(min_length=1, max_length=256)
    published_at: date | None = None
    source_url: HttpUrl | None = None
    language: str = Field(default="zh-CN", min_length=1, max_length=32)
    tags: tuple[str, ...] = Field(default=(), max_length=64)
    applicability: tuple[str, ...] = Field(default=(), max_length=32)


class JsonSourceImport(SourceImportMetadata):
    method: Literal["pasted_text", "url"]
    content: str | None = Field(default=None, max_length=2_000_000)
    remote_url: HttpUrl | None = None
    filename: str = Field(default="pasted-nutrition-source.md", min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_method_input(self) -> JsonSourceImport:
        if self.method == "pasted_text" and not (self.content and self.content.strip()):
            raise ValueError("Pasted text import requires content")
        if self.method == "url" and self.remote_url is None:
            raise ValueError("URL import requires remote_url")
        return self


class SourceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: Literal["content", "applicability", "rights"]
    decision: Literal["approve", "reject", "revoke"]
    attestations: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=2000)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ReleaseCreateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=128)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)


class ReleaseReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class ReleaseEvaluateRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128)


class ReleaseActivationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class RuntimeRollbackRequest(BaseModel):
    release_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class EvaluationCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=128)
    query_plan_input: dict[str, Any]
    expected_source_keys: tuple[str, ...] = Field(default=(), max_length=64)
    expected_chunk_concepts: tuple[str, ...] = Field(default=(), max_length=64)
    forbidden_source_keys: tuple[str, ...] = Field(default=(), max_length=64)
    expected_outcome: Literal["evidence", "insufficient"]


class EvaluationDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=128)
    cases: tuple[EvaluationCaseRequest, ...] = Field(min_length=1, max_length=1000)


class RetrievalLabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    release_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_results: int = Field(default=10, ge=1, le=20)
    metadata_filter: dict[str, Any] = Field(default_factory=dict)


def _repository(request: Request) -> NutritionRagRepository:
    return cast(NutritionRagRepository, request.app.state.nutrition_rag)


def _object_store(request: Request) -> NutritionObjectStore:
    value = getattr(request.app.state, "nutrition_object_store", None)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tencent COS is not configured",
        )
    return cast(NutritionObjectStore, value)


def _retrieval(request: Request) -> HybridNutritionRagService:
    value = getattr(request.app.state, "nutrition_knowledge", None)
    if not isinstance(value, HybridNutritionRagService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Nutrition Hybrid RAG management service is not configured",
        )
    return value


def _require_csrf(request: Request) -> None:
    if request.headers.get("X-SlimGuard-CSRF") != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF header required")


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value or len(value) > 200 or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A safe Idempotency-Key header is required",
        )
    return value


def _handle_control_error(error: Exception) -> HTTPException:
    if isinstance(error, NutritionRagNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, (NutritionRagConflict, NutritionRagGovernanceError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@router.get("/dashboard")
async def dashboard(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    result = await _repository(request).dashboard()
    settings = cast(Settings, request.app.state.settings)
    result["capabilities"] = {
        "cos_configured": settings.tencent_cos_is_configured,
        "worker_enabled": settings.nutrition_knowledge_worker_enabled,
        "rag_engine": settings.nutrition_rag_engine,
        "embedding_model": settings.nutrition_embedding_model,
        "rerank_model": settings.nutrition_rerank_model,
    }
    return result


@router.get("/sources")
async def list_sources(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    source_status: str | None = Query(default=None, alias="status", max_length=32),
    search: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    try:
        return await _repository(request).list_sources(
            status=source_status, search=search, limit=limit, offset=offset
        )
    except ValueError as error:
        raise _handle_control_error(error) from error


@router.get("/sources/{source_id}")
async def get_source(
    source_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).get_source_detail(source_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    await _audit(
        request,
        principal,
        action="view",
        resource_type="nutrition_source",
        resource_id=source_id,
    )
    return result


@router.get("/sources/{source_id}/sections")
async def source_sections(
    source_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    return await _repository(request).list_source_sections(source_id, limit=limit, offset=offset)


@router.get("/sources/{source_id}/chunks")
async def source_chunks(
    source_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    return await _repository(request).list_source_chunks(
        source_id, kind=kind, limit=limit, offset=offset
    )


@router.get("/sources/{source_id}/asset")
async def download_source_asset(
    source_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> Response:
    detail = await _repository(request).get_source_detail(source_id)
    if detail is None or detail["asset"] is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source asset not found")
    asset = detail["asset"]
    settings = cast(Settings, request.app.state.settings)
    content = await _object_store(request).get(
        key=asset["storage_key"], max_bytes=settings.nutrition_knowledge_max_upload_bytes
    )
    await _audit(
        request,
        principal,
        action="download",
        resource_type="nutrition_source_asset",
        resource_id=asset["id"],
    )
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", asset["original_filename"])[-200:]
    return Response(
        content=content,
        media_type=asset["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sources/imports", status_code=status.HTTP_202_ACCEPTED)
async def import_source(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].casefold()
        if media_type == "multipart/form-data":
            metadata, content, filename, document_media_type = await _multipart_import(request)
            asset = await _store_asset(
                request=request,
                content=content,
                filename=filename,
                media_type=document_media_type,
                source_method="upload",
                source_url=str(metadata.source_url) if metadata.source_url else None,
                actor=principal.username,
            )
            job_input = _metadata_dict(metadata) | {"asset_id": asset.id}
            subject_type, subject_id = "asset", asset.id
            identity = asset.sha256
        elif media_type == "application/json":
            payload = JsonSourceImport.model_validate(await request.json())
            metadata = SourceImportMetadata.model_validate(
                payload.model_dump(include=set(SourceImportMetadata.model_fields))
            )
            if payload.method == "pasted_text":
                content = cast(str, payload.content).encode()
                asset = await _store_asset(
                    request=request,
                    content=content,
                    filename=payload.filename,
                    media_type="text/markdown",
                    source_method="pasted_text",
                    source_url=str(payload.source_url) if payload.source_url else None,
                    actor=principal.username,
                )
                job_input = _metadata_dict(metadata) | {"asset_id": asset.id}
                subject_type, subject_id = "asset", asset.id
                identity = asset.sha256
            else:
                remote_url = str(payload.remote_url)
                _validate_remote_url_for_storage(remote_url)
                job_input = _metadata_dict(metadata) | {
                    "remote_url": remote_url,
                    "source_url": str(payload.source_url or payload.remote_url),
                }
                subject_type, subject_id = "remote_url", None
                identity = hashlib.sha256(remote_url.encode()).hexdigest()
        else:
            raise ValueError("Import must use multipart/form-data or application/json")
        job = await _repository(request).enqueue_job(
            job_type="ingest",
            subject_type=subject_type,
            subject_id=subject_id,
            input=job_input,
            idempotency_key=(f"ingest:{identity}:{metadata.source_key}:{metadata.version}")[:256],
            created_by=principal.username,
        )
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid nutrition source import",
        ) from error
    await _audit(
        request,
        principal,
        action="import",
        resource_type="nutrition_knowledge_job",
        resource_id=job.id,
    )
    return {"job": asdict(job)}


@router.post("/sources/{source_id}/reviews")
async def review_source(
    source_id: str,
    payload: SourceReviewRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        review = await _repository(request).append_source_review(
            source_id=source_id,
            review_type=payload.review_type,
            decision=payload.decision,
            actor=principal.username,
            attestations=payload.attestations,
            reason=payload.reason,
        )
        if review.approved:
            await _repository(request).mark_source_approved(source_id)
    except (
        NutritionRagNotFound,
        NutritionRagConflict,
        NutritionRagGovernanceError,
        ValueError,
    ) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action=f"review_{payload.decision}",
        resource_type="nutrition_source",
        resource_id=source_id,
    )
    detail = await _repository(request).get_source_detail(source_id)
    return cast(dict[str, Any], detail)


@router.post("/sources/{source_id}/retire")
async def retire_source(
    source_id: str,
    payload: ReasonRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, bool]:
    del csrf
    try:
        await _repository(request).retire_source(
            source_id, actor=principal.username, reason=payload.reason
        )
    except (NutritionRagNotFound, NutritionRagGovernanceError, ValueError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="retire",
        resource_type="nutrition_source",
        resource_id=source_id,
    )
    return {"retired": True}


@router.get("/jobs")
async def list_jobs(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    job_status: Annotated[list[str] | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    del principal
    jobs = await _repository(request).list_jobs(
        statuses=job_status or (), limit=limit, offset=offset
    )
    return {"items": [asdict(job) for job in jobs], "limit": limit, "offset": offset}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    job = await _repository(request).get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return {
        "job": asdict(job),
        "events": [asdict(item) for item in await _repository(request).list_job_events(job_id)],
    }


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    return {"items": [asdict(item) for item in await _repository(request).list_job_events(job_id)]}


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        job = await _repository(request).retry_job(job_id, actor=principal.username)
    except (NutritionRagNotFound, NutritionRagGovernanceError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="retry",
        resource_type="nutrition_knowledge_job",
        resource_id=job_id,
    )
    return {"job": asdict(job)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        job = await _repository(request).cancel_job(job_id, actor=principal.username)
    except (NutritionRagNotFound, NutritionRagGovernanceError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="cancel",
        resource_type="nutrition_knowledge_job",
        resource_id=job_id,
    )
    return {"job": asdict(job)}


@router.get("/releases")
async def list_releases(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    return {"items": [asdict(item) for item in await _repository(request).list_releases()]}


@router.get("/releases/{release_id}")
async def get_release(
    release_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    release = await _repository(request).get_release(release_id)
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release not found")
    return {"release": asdict(release)}


@router.post("/releases")
async def create_release(
    payload: ReleaseCreateRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
    idempotency_key: Annotated[str, Depends(_idempotency_key)],
) -> dict[str, Any]:
    del csrf, idempotency_key
    try:
        release = await _repository(request).create_release(
            version=payload.version,
            source_ids=payload.source_ids,
            created_by=principal.username,
        )
        release = await _repository(request).complete_release_index_check(release.id)
    except (
        NutritionRagNotFound,
        NutritionRagConflict,
        NutritionRagGovernanceError,
        ValueError,
    ) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="create",
        resource_type="nutrition_corpus_release",
        resource_id=release.id,
    )
    return {"release": asdict(release)}


@router.post("/releases/{release_id}/evaluate", status_code=status.HTTP_202_ACCEPTED)
async def evaluate_release(
    release_id: str,
    payload: ReleaseEvaluateRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
    idempotency_key: Annotated[str, Depends(_idempotency_key)],
) -> dict[str, Any]:
    del csrf
    try:
        run_id, job = await _repository(request).create_evaluation_run(
            release_id=release_id,
            dataset_id=payload.dataset_id,
            created_by=principal.username,
            idempotency_key=idempotency_key,
        )
    except (NutritionRagNotFound, NutritionRagGovernanceError, ValueError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="evaluate",
        resource_type="nutrition_corpus_release",
        resource_id=release_id,
    )
    return {"evaluation_run_id": run_id, "job": asdict(job)}


@router.post("/releases/{release_id}/reviews")
async def review_release(
    release_id: str,
    payload: ReleaseReviewRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        release = await _repository(request).review_release(
            release_id,
            decision=payload.decision,
            actor=principal.username,
            reason=payload.reason,
        )
    except (NutritionRagNotFound, NutritionRagGovernanceError, ValueError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action=f"review_{payload.decision}",
        resource_type="nutrition_corpus_release",
        resource_id=release_id,
    )
    return {"release": asdict(release)}


@router.post("/releases/{release_id}/activate")
async def activate_release(
    release_id: str,
    payload: ReleaseActivationRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
    idempotency_key: Annotated[str, Depends(_idempotency_key)],
) -> dict[str, Any]:
    del csrf, idempotency_key
    try:
        runtime = await _repository(request).activate_release(
            release_id,
            actor=principal.username,
            reason=payload.reason,
        )
    except (NutritionRagNotFound, NutritionRagGovernanceError, ValueError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="activate",
        resource_type="nutrition_corpus_release",
        resource_id=release_id,
    )
    return {"runtime": asdict(runtime)}


@router.get("/runtime")
async def runtime(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    return {"runtime": asdict(await _repository(request).get_runtime())}


@router.post("/runtime/rollback")
async def rollback_runtime(
    payload: RuntimeRollbackRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
    idempotency_key: Annotated[str, Depends(_idempotency_key)],
) -> dict[str, Any]:
    del csrf, idempotency_key
    try:
        runtime_state = await _repository(request).activate_release(
            payload.release_id,
            actor=principal.username,
            reason=payload.reason,
            rollback=True,
        )
    except (NutritionRagNotFound, NutritionRagGovernanceError, ValueError) as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="rollback",
        resource_type="nutrition_corpus_runtime",
        resource_id=payload.release_id,
    )
    return {"runtime": asdict(runtime_state)}


@router.post("/retrieval-lab/runs")
async def retrieval_lab(
    payload: RetrievalLabRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    result = dict(
        await _retrieval(request).search(
            query=payload.query,
            max_results=payload.max_results,
            metadata_filter=payload.metadata_filter,
            retrieved_in_invocation_id=f"admin-lab-{principal.username}",
            release_id=payload.release_id,
        )
    )
    run_id = result.get("retrieval_run_id")
    await _audit(
        request,
        principal,
        action="run",
        resource_type="nutrition_retrieval_lab",
        resource_id=str(run_id or "no-run"),
    )
    return result


@router.get("/retrieval-lab/runs/{run_id}")
async def retrieval_lab_run(
    run_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    result = await _repository(request).get_retrieval_run(run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Retrieval run not found",
        )
    await _audit(
        request,
        principal,
        action="view",
        resource_type="nutrition_retrieval_lab",
        resource_id=run_id,
    )
    return result


@router.get("/evaluation-datasets")
async def evaluation_datasets(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    return {
        "items": [asdict(item) for item in await _repository(request).list_evaluation_datasets()]
    }


@router.post("/evaluation-datasets")
async def create_evaluation_dataset(
    payload: EvaluationDatasetRequest,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    csrf: Annotated[None, Depends(_require_csrf)],
) -> dict[str, Any]:
    del csrf
    try:
        dataset = await _repository(request).create_evaluation_dataset(
            version=payload.version,
            cases=tuple(item.model_dump(mode="json") for item in payload.cases),
            created_by=principal.username,
        )
    except ValueError as error:
        raise _handle_control_error(error) from error
    await _audit(
        request,
        principal,
        action="create",
        resource_type="nutrition_evaluation_dataset",
        resource_id=dataset.id,
    )
    return {"dataset": asdict(dataset)}


@router.get("/evaluation-runs")
async def evaluation_runs(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    del principal
    return {"items": list(await _repository(request).list_evaluation_runs(limit=limit))}


@router.get("/evaluation-runs/{run_id}")
async def evaluation_run(
    run_id: str,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(_authenticate)],
) -> dict[str, Any]:
    del principal
    result = await _repository(request).get_evaluation_run(run_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation run not found"
        )
    return result


async def _multipart_import(
    request: Request,
) -> tuple[SourceImportMetadata, bytes, str, str]:
    settings = cast(Settings, request.app.state.settings)
    length = request.headers.get("content-length")
    if (
        length
        and length.isdigit()
        and int(length) > settings.nutrition_knowledge_max_upload_bytes + 64_000
    ):
        raise ValueError("Upload is too large")
    form = await request.form(max_files=1, max_fields=5)
    metadata_raw = form.get("metadata")
    uploaded = form.get("file")
    if not isinstance(metadata_raw, str) or not isinstance(uploaded, UploadFile):
        raise ValueError("Multipart import requires metadata and file")
    metadata = SourceImportMetadata.model_validate_json(metadata_raw)
    content = await uploaded.read(settings.nutrition_knowledge_max_upload_bytes + 1)
    await uploaded.close()
    if not content or len(content) > settings.nutrition_knowledge_max_upload_bytes:
        raise ValueError("Upload is empty or too large")
    filename = (uploaded.filename or "nutrition-source.bin")[:512]
    media_type = uploaded.content_type or "application/octet-stream"
    return metadata, content, filename, media_type


async def _store_asset(
    *,
    request: Request,
    content: bytes,
    filename: str,
    media_type: str,
    source_method: str,
    source_url: str | None,
    actor: str,
) -> NutritionAsset:
    settings = cast(Settings, request.app.state.settings)
    if not content or len(content) > settings.nutrition_knowledge_max_upload_bytes:
        raise ValueError("Nutrition source is empty or too large")
    digest = hashlib.sha256(content).hexdigest()
    store = _object_store(request)
    key = store.object_key(sha256=digest, filename=filename)
    stored = await store.put(
        key=key,
        content=content,
        sha256=digest,
        media_type=media_type,
    )
    return await _repository(request).create_asset(
        stored=stored,
        original_filename=filename,
        source_method=source_method,
        source_url=source_url,
        created_by=actor,
    )


def _metadata_dict(value: SourceImportMetadata) -> dict[str, Any]:
    result = value.model_dump(mode="json")
    if result["source_url"] is not None:
        result["source_url"] = str(result["source_url"])
    return result


def _validate_remote_url_for_storage(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Remote URL is invalid")
    sensitive = re.compile(r"token|secret|signature|credential|access[_-]?key", re.I)
    if any(sensitive.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("Remote URL must not contain persistent credentials")


__all__ = ["router"]
