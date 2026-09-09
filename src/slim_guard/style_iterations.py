"""Durable control plane for self-service Style Profile iteration."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from slim_guard.agents.contracts import ContractModel
from slim_guard.db.models import (
    StyleABEvaluationCaseRecord,
    StyleIterationEventRecord,
    StyleIterationInputRecord,
    StyleIterationRunRecord,
    StyleProfileVersionRecord,
    StyleRuntimeConfigurationRecord,
    new_uuid,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.style_feedback import StyleCorrectionFeedbackRepository
from slim_guard.style_iteration_sources import (
    collect_style_iteration_sources,
    style_iteration_digest,
)
from slim_guard.style_reviews import StyleABReviewRepository

StyleIterationStatus = Literal[
    "queued",
    "validating",
    "freezing_inputs",
    "classifying",
    "building_profile",
    "generating_cases",
    "evaluating",
    "importing_review",
    "ready_for_review",
    "needs_action",
    "failed_transient",
    "failed_terminal",
    "rejected",
    "evaluated",
    "active",
    "cancelled",
]

_PROCESSING_STATUSES = frozenset(
    {
        "validating",
        "freezing_inputs",
        "classifying",
        "building_profile",
        "generating_cases",
        "evaluating",
        "importing_review",
    }
)
_OPEN_STATUSES = frozenset(
    {
        "queued",
        *_PROCESSING_STATUSES,
        "ready_for_review",
        "needs_action",
        "failed_transient",
    }
)
_TERMINAL_STATUSES = frozenset(
    {"failed_terminal", "rejected", "evaluated", "active", "cancelled"}
)
_VERSION = re.compile(r"^(?P<profile>.+)_v(?P<number>[1-9][0-9]*)$")


class StyleIterationCreate(ContractModel):
    source_profile_version: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)
    reviewed_inputs_confirmed: Literal[True]


class StyleIterationAction(ContractModel):
    reason: str = Field(min_length=1, max_length=1000)


class StyleIterationConflict(RuntimeError):
    pass


class StyleIterationNotFound(RuntimeError):
    pass


class StyleIterationRepository:
    """Owns version allocation, task leases, immutable inputs, and progress events."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def context(
        self,
        *,
        fallback_version: str,
        model_configured: bool,
    ) -> dict[str, Any]:
        versions = await StyleABReviewRepository(self.database).candidate_profile_versions()
        runtime = await self.runtime_configuration(fallback_version=fallback_version)
        open_run = await self.open_run()
        source_version = versions[0] if versions else fallback_version
        eligibility = await self.build_eligibility(source_version)
        return {
            "candidate_profile_versions": list(versions),
            "suggested_source_version": source_version,
            "runtime": runtime,
            "open_run": open_run,
            "model_configured": model_configured,
            "build_eligibility": eligibility,
        }

    async def build_eligibility(self, source_version: str) -> dict[str, Any]:
        stats = await StyleABReviewRepository(self.database).statistics(
            candidate_profile_version=source_version
        )
        feedback = await StyleCorrectionFeedbackRepository(self.database).list(
            limit=1,
            offset=0,
            profile_version=source_version,
        )
        counts = stats["counts"]
        reasons: list[str] = []
        if counts["pending_case_count"]:
            reasons.append(f"还有 {counts['pending_case_count']} 条 A/B Case 未评分")
        if counts["rejected_case_count"] == 0 and feedback["total"] == 0:
            reasons.append("没有拒绝评分或新增风格纠正")
        return {
            "eligible": not reasons,
            "reasons": reasons,
            "counts": {
                **counts,
                "style_correction_count": feedback["total"],
            },
        }

    async def create_run(
        self,
        payload: StyleIterationCreate,
        *,
        actor: str,
        model_configured: bool,
    ) -> dict[str, Any]:
        reviewer = self._text(actor, field="actor")
        source_version = self._text(
            payload.source_profile_version, field="source_profile_version"
        )
        identity = _VERSION.fullmatch(source_version)
        if identity is None:
            raise ValueError("Source profile version must end in _v<number>")
        if not model_configured:
            raise StyleIterationConflict("Style iteration model is not configured")
        existing = await self._by_idempotency(reviewer, payload.idempotency_key)
        if existing is not None:
            if existing["source_version"] != source_version:
                raise StyleIterationConflict("Idempotency key belongs to another source version")
            return existing
        eligibility = await self.build_eligibility(source_version)
        if not eligibility["eligible"]:
            raise StyleIterationConflict("；".join(eligibility["reasons"]))

        profile_id = identity.group("profile")
        now = utc_now()
        try:
            async with self.database.session() as session, session.begin():
                existing_row = await session.scalar(
                    select(StyleIterationRunRecord).where(
                        StyleIterationRunRecord.created_by == reviewer,
                        StyleIterationRunRecord.idempotency_key == payload.idempotency_key,
                    )
                )
                if existing_row is not None:
                    return self._run_view(existing_row)
                open_row = await session.scalar(
                    select(StyleIterationRunRecord).where(
                        StyleIterationRunRecord.profile_id == profile_id,
                        StyleIterationRunRecord.status.in_(_OPEN_STATUSES),
                    )
                )
                if open_row is not None:
                    raise StyleIterationConflict(
                        f"{profile_id} 已有未结束的构建 {open_row.target_version}"
                    )
                versions = list(
                    await session.scalars(
                        select(StyleABEvaluationCaseRecord.candidate_profile_version)
                    )
                )
                versions.extend(
                    await session.scalars(select(StyleProfileVersionRecord.version))
                )
                versions.extend(
                    await session.scalars(select(StyleIterationRunRecord.target_version))
                )
                current_number = int(identity.group("number"))
                for version in versions:
                    match = _VERSION.fullmatch(version)
                    if match is not None and match.group("profile") == profile_id:
                        current_number = max(current_number, int(match.group("number")))
                run = StyleIterationRunRecord(
                    id=new_uuid(),
                    profile_id=profile_id,
                    source_version=source_version,
                    target_version=f"{profile_id}_v{current_number + 1}",
                    status="queued",
                    stage="queued",
                    progress_current=0,
                    progress_total=10,
                    created_by=reviewer,
                    idempotency_key=payload.idempotency_key,
                    created_at=now,
                )
                session.add(run)
                await session.flush()
                session.add(
                    StyleIterationEventRecord(
                        run_id=run.id,
                        sequence=1,
                        event_type="run_created",
                        stage="queued",
                        status="queued",
                        public_summary=(
                            f"已创建 {run.target_version} 构建任务，等待冻结来源素材。"
                        ),
                        technical_metadata_json=self._json(
                            {
                                "source_version": source_version,
                                "target_version": run.target_version,
                                "counts": eligibility["counts"],
                            }
                        ),
                        created_at=now,
                    )
                )
                await session.flush()
                return self._run_view(run)
        except IntegrityError as error:
            duplicate = await self._by_idempotency(reviewer, payload.idempotency_key)
            if duplicate is not None:
                return duplicate
            raise StyleIterationConflict("Another style build was created concurrently") from error

    async def list_runs(self, *, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid iteration pagination")
        async with self.database.session() as session:
            total = int(await session.scalar(select(func.count(StyleIterationRunRecord.id))) or 0)
            rows = tuple(
                await session.scalars(
                    select(StyleIterationRunRecord)
                    .order_by(
                        StyleIterationRunRecord.created_at.desc(),
                        StyleIterationRunRecord.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
        return {
            "items": [self._run_view(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_run(self, run_id: str, *, include_artifacts: bool = True) -> dict[str, Any]:
        async with self.database.session() as session:
            row = await session.get(StyleIterationRunRecord, run_id)
        if row is None:
            raise StyleIterationNotFound("Style iteration not found")
        return self._run_view(row, include_artifacts=include_artifacts)

    async def open_run(self) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(StyleIterationRunRecord)
                .where(StyleIterationRunRecord.status.in_(_OPEN_STATUSES))
                .order_by(StyleIterationRunRecord.created_at.desc())
            )
        return self._run_view(row) if row is not None else None

    async def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, Any], ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        await self.get_run(run_id, include_artifacts=False)
        async with self.database.session() as session:
            rows = tuple(
                await session.scalars(
                    select(StyleIterationEventRecord)
                    .where(
                        StyleIterationEventRecord.run_id == run_id,
                        StyleIterationEventRecord.sequence > after_sequence,
                    )
                    .order_by(StyleIterationEventRecord.sequence)
                )
            )
        return tuple(self._event_view(row) for row in rows)

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease: timedelta,
    ) -> dict[str, Any] | None:
        owner = self._text(worker_id, field="worker_id")
        if lease <= timedelta(seconds=5):
            raise ValueError("Style iteration lease must exceed five seconds")
        now = utc_now()
        recoverable = or_(
            StyleIterationRunRecord.status == "queued",
            (
                StyleIterationRunRecord.status.in_(_PROCESSING_STATUSES)
                & (StyleIterationRunRecord.lease_expires_at < now)
            ),
        )
        async with self.database.session() as session, session.begin():
            row = await session.scalar(
                select(StyleIterationRunRecord)
                .where(recoverable)
                .order_by(StyleIterationRunRecord.created_at)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            recovered = row.status != "queued"
            row.status = "validating"
            row.stage = "validating"
            row.lease_owner = owner
            row.lease_expires_at = now + lease
            row.heartbeat_at = now
            row.started_at = row.started_at or now
            await self._append_event_in_session(
                session,
                row,
                event_type="run_recovered" if recovered else "run_started",
                summary=(
                    "构建任务租约已过期，正在从冻结产物安全恢复。"
                    if recovered
                    else "构建任务已开始，正在校验来源版本。"
                ),
                metadata={"worker_id": owner},
            )
            await session.flush()
            return self._run_view(row, include_artifacts=True)

    async def heartbeat(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease: timedelta,
    ) -> None:
        now = utc_now()
        async with self.database.session() as session, session.begin():
            row = await session.get(StyleIterationRunRecord, run_id)
            if (
                row is None
                or row.lease_owner != worker_id
                or row.status not in _PROCESSING_STATUSES
            ):
                return
            row.heartbeat_at = now
            row.lease_expires_at = now + lease

    async def transition(
        self,
        run_id: str,
        *,
        status: StyleIterationStatus,
        stage: str,
        progress_current: int,
        summary: str,
        event_type: str = "stage_completed",
        metadata: Mapping[str, Any] | None = None,
        values: Mapping[str, Any] | None = None,
        terminal: bool = False,
    ) -> dict[str, Any]:
        async with self.database.session() as session, session.begin():
            row = await session.get(StyleIterationRunRecord, run_id)
            if row is None:
                raise StyleIterationNotFound("Style iteration not found")
            if row.status == "cancelled":
                raise StyleIterationConflict("Style iteration was cancelled")
            row.status = status
            row.stage = stage
            row.progress_current = min(max(progress_current, 0), row.progress_total)
            for field, value in (values or {}).items():
                if field not in {
                    "source_sha256",
                    "profile_sha256",
                    "bundle_sha256",
                    "cases_sha256",
                    "evaluation_sha256",
                    "generation_model",
                    "judge_model",
                    "input_snapshot_json",
                    "classification_json",
                    "profile_json",
                    "bundle_json",
                    "cases_json",
                    "comparison_json",
                    "failure_code",
                    "failure_summary",
                    "published_version_record_id",
                }:
                    raise ValueError(f"Unsupported iteration field: {field}")
                setattr(row, field, value)
            if terminal:
                row.completed_at = utc_now()
                row.lease_owner = None
                row.lease_expires_at = None
            await self._append_event_in_session(
                session,
                row,
                event_type=event_type,
                summary=summary,
                metadata=metadata or {},
            )
            await session.flush()
            return self._run_view(row, include_artifacts=True)

    async def freeze_inputs(self, run_id: str) -> dict[str, Any]:
        run = await self.get_run(run_id, include_artifacts=True)
        snapshot = await collect_style_iteration_sources(
            self.database,
            source_profile_version=run["source_version"],
        )
        return await self.transition(
            run_id,
            status="freezing_inputs",
            stage="freezing_inputs",
            progress_current=2,
            summary=(
                "已冻结 "
                f"{snapshot['counts']['reviewed_a_b_cases']} 条实名 A/B 和 "
                f"{snapshot['counts']['style_corrections']} 条风格纠正。"
            ),
            metadata={
                "source_sha256": snapshot["source_sha256"],
                "counts": snapshot["counts"],
            },
            values={
                "source_sha256": snapshot["source_sha256"],
                "input_snapshot_json": self._json(snapshot),
            },
        )

    async def store_classified_inputs(
        self,
        run_id: str,
        *,
        classifications: Sequence[Mapping[str, Any]],
    ) -> None:
        run = await self.get_run(run_id, include_artifacts=True)
        snapshot = run.get("artifacts", {}).get("input_snapshot")
        if not isinstance(snapshot, dict):
            raise StyleIterationConflict("Style iteration inputs are not frozen")
        by_source = {
            (item["source_kind"], item["source_id"]): item for item in classifications
        }
        frozen: list[tuple[str, str, dict[str, Any]]] = []
        for item in snapshot["sources"]["a_b_reviews"]:
            frozen.append(
                ("ab_review", item["latest_human_review"]["review_id"], item)
            )
        for item in snapshot["sources"]["style_corrections"]:
            frozen.append(("style_feedback", item["feedback_id"], item))
        async with self.database.session() as session, session.begin():
            existing = set(
                await session.scalars(
                    select(StyleIterationInputRecord.source_id).where(
                        StyleIterationInputRecord.run_id == run_id
                    )
                )
            )
            for source_kind, source_id, item in frozen:
                if source_id in existing:
                    continue
                classification = by_source.get((source_kind, source_id))
                if classification is None:
                    raise ValueError(f"Missing classification for {source_kind}:{source_id}")
                session.add(
                    StyleIterationInputRecord(
                        run_id=run_id,
                        source_kind=source_kind,
                        source_id=source_id,
                        source_sha256=style_iteration_digest(item),
                        classification=classification["classification"],
                        classification_summary=classification["summary"],
                        payload_json=self._json(item),
                        derived_case_ids_json=self._json(
                            classification.get("derived_case_ids", [])
                        ),
                    )
                )

    async def cancel(self, run_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        reviewer = self._text(actor, field="actor")
        summary = self._text(reason, field="reason", maximum=1000)
        run = await self.get_run(run_id, include_artifacts=False)
        if run["status"] in _TERMINAL_STATUSES:
            raise StyleIterationConflict("Completed style iteration cannot be cancelled")
        return await self.transition(
            run_id,
            status="cancelled",
            stage="cancelled",
            progress_current=run["progress"]["current"],
            summary="构建任务已由管理员取消。",
            event_type="run_cancelled",
            metadata={"actor": reviewer, "reason": summary},
            terminal=True,
        )

    async def retry(self, run_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        reviewer = self._text(actor, field="actor")
        summary = self._text(reason, field="reason", maximum=1000)
        async with self.database.session() as session, session.begin():
            row = await session.get(StyleIterationRunRecord, run_id)
            if row is None:
                raise StyleIterationNotFound("Style iteration not found")
            if row.status != "failed_transient":
                raise StyleIterationConflict("Only transient failures can be retried")
            row.status = "queued"
            row.stage = "queued"
            row.lease_owner = None
            row.lease_expires_at = None
            row.failure_code = None
            row.failure_summary = None
            await self._append_event_in_session(
                session,
                row,
                event_type="run_retry_queued",
                summary="管理员已将临时失败任务重新加入队列。",
                metadata={"actor": reviewer, "reason": summary},
            )
            await session.flush()
            return self._run_view(row)

    async def runtime_configuration(self, *, fallback_version: str) -> dict[str, Any]:
        async with self.database.session() as session:
            row = await session.get(StyleRuntimeConfigurationRecord, "default")
        if row is None:
            return {
                "active_profile_version": fallback_version,
                "previous_profile_version": None,
                "revision": 0,
                "updated_by": "environment-fallback",
                "updated_at": None,
            }
        return {
            "active_profile_version": row.active_profile_version,
            "previous_profile_version": row.previous_profile_version,
            "revision": row.revision,
            "updated_by": row.updated_by,
            "updated_at": self._aware(row.updated_at).isoformat(),
        }

    async def _by_idempotency(self, actor: str, key: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(StyleIterationRunRecord).where(
                    StyleIterationRunRecord.created_by == actor,
                    StyleIterationRunRecord.idempotency_key == key,
                )
            )
        return self._run_view(row) if row is not None else None

    async def _append_event_in_session(
        self,
        session: Any,
        run: StyleIterationRunRecord,
        *,
        event_type: str,
        summary: str,
        metadata: Mapping[str, Any],
    ) -> None:
        sequence = int(
            await session.scalar(
                select(func.max(StyleIterationEventRecord.sequence)).where(
                    StyleIterationEventRecord.run_id == run.id
                )
            )
            or 0
        ) + 1
        session.add(
            StyleIterationEventRecord(
                run_id=run.id,
                sequence=sequence,
                event_type=self._text(event_type, field="event_type", maximum=64),
                stage=run.stage,
                status=run.status,
                public_summary=self._text(summary, field="summary", maximum=1000),
                technical_metadata_json=self._json(metadata),
            )
        )

    @classmethod
    def _run_view(
        cls,
        row: StyleIterationRunRecord,
        *,
        include_artifacts: bool = False,
    ) -> dict[str, Any]:
        result = {
            "run_id": row.id,
            "profile_id": row.profile_id,
            "source_version": row.source_version,
            "target_version": row.target_version,
            "status": row.status,
            "stage": row.stage,
            "progress": {
                "current": row.progress_current,
                "total": row.progress_total,
            },
            "hashes": {
                "source_sha256": row.source_sha256,
                "profile_sha256": row.profile_sha256,
                "bundle_sha256": row.bundle_sha256,
                "cases_sha256": row.cases_sha256,
                "evaluation_sha256": row.evaluation_sha256,
            },
            "models": {
                "generation": row.generation_model,
                "judge": row.judge_model,
            },
            "created_by": row.created_by,
            "failure": (
                {"code": row.failure_code, "summary": row.failure_summary}
                if row.failure_code
                else None
            ),
            "published_version_record_id": row.published_version_record_id,
            "created_at": cls._aware(row.created_at).isoformat(),
            "started_at": cls._optional_time(row.started_at),
            "completed_at": cls._optional_time(row.completed_at),
        }
        if include_artifacts:
            result["artifacts"] = {
                "input_snapshot": cls._load(row.input_snapshot_json),
                "classification": cls._load(row.classification_json),
                "profile": cls._load(row.profile_json),
                "bundle": cls._load(row.bundle_json),
                "cases": cls._load(row.cases_json),
                "comparison": cls._load(row.comparison_json),
            }
        return result

    @classmethod
    def _event_view(cls, row: StyleIterationEventRecord) -> dict[str, Any]:
        return {
            "event_id": row.id,
            "sequence": row.sequence,
            "event_type": row.event_type,
            "stage": row.stage,
            "status": row.status,
            "summary": row.public_summary,
            "technical_metadata": cls._load(row.technical_metadata_json) or {},
            "created_at": cls._aware(row.created_at).isoformat(),
        }

    @staticmethod
    def _load(value: str | None) -> Any:
        return json.loads(value) if value else None

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _text(value: str, *, field: str, maximum: int = 128) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"{field} must contain 1 to {maximum} characters")
        return normalized

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value

    @classmethod
    def _optional_time(cls, value: datetime | None) -> str | None:
        return cls._aware(value).isoformat() if value is not None else None


__all__ = [
    "StyleIterationAction",
    "StyleIterationConflict",
    "StyleIterationCreate",
    "StyleIterationNotFound",
    "StyleIterationRepository",
    "StyleIterationStatus",
]
