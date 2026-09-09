"""Human gates, publication, and atomic runtime activation for Style Profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, field_validator
from sqlalchemy import select

from slim_guard.agents.contracts import ContractModel
from slim_guard.db.models import (
    StyleActivationEventRecord,
    StyleIterationRunRecord,
    StyleProfileRecord,
    StyleProfileVersionRecord,
    StyleRuntimeConfigurationRecord,
    new_uuid,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.style_iterations import (
    StyleIterationConflict,
    StyleIterationRepository,
)
from slim_guard.style_profiles import StyleProfileRepository
from slim_guard.style_reviews import StyleABBundleNotApproved, StyleABReviewRepository


class StylePublishConfirmation(ContractModel):
    privacy_confirmed: bool
    expression_only_confirmed: bool
    evaluation_reviewed: bool


class StyleActivationAction(ContractModel):
    version: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("version", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Style activation text cannot be blank")
        return normalized


class StyleRollbackAction(ContractModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Style rollback reason cannot be blank")
        return normalized


class StyleIterationLifecycle:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.iterations = StyleIterationRepository(database)
        self.reviews = StyleABReviewRepository(database)
        self.profiles = StyleProfileRepository(database)

    async def enriched_run(self, run_id: str) -> dict[str, Any]:
        run = await self.iterations.get_run(run_id, include_artifacts=True)
        run = await self._reconcile_rejected(run)
        source_asset = await self.profiles.get_profile_asset(run["source_version"])
        if source_asset is not None:
            run["source_profile"] = source_asset.to_profile().model_dump(mode="json")
        else:
            source_run = await self.iterations.get_by_target_version(run["source_version"])
            run["source_profile"] = (
                source_run.get("artifacts", {}).get("profile") if source_run else None
            )
        statistics = await self.reviews.statistics(candidate_profile_version=run["target_version"])
        automated_passed = self._automated_passed(run)
        counts = statistics["counts"]
        run["review"] = {
            **statistics,
            "automated_passed": automated_passed,
            "publish_allowed": (
                run["status"] == "ready_for_review"
                and counts["case_count"] > 0
                and counts["pending_case_count"] == 0
                and counts["rejected_case_count"] == 0
                and automated_passed
            ),
        }
        return run

    async def publish(
        self,
        run_id: str,
        *,
        actor: str,
        confirmation: StylePublishConfirmation,
    ) -> dict[str, Any]:
        if not all(
            (
                confirmation.privacy_confirmed,
                confirmation.expression_only_confirmed,
                confirmation.evaluation_reviewed,
            )
        ):
            raise StyleIterationConflict("发布前必须确认隐私、纯表达边界和自动评测")
        run = await self.enriched_run(run_id)
        if run["status"] == "evaluated":
            return run
        if not run["review"]["publish_allowed"]:
            raise StyleIterationConflict("该版本尚未满足全部人工接受和自动评测通过条件")
        bundle = run["artifacts"]["bundle"]
        comparison = run["artifacts"]["comparison"]
        if not isinstance(bundle, dict) or not isinstance(comparison, dict):
            raise StyleIterationConflict("构建产物不完整，不能发布")
        try:
            asset = await self.profiles.import_reviewed_bundle(
                {**bundle, "evaluation": comparison["evaluation"]},
                actor=actor,
                privacy_confirmed=True,
                expression_only_confirmed=True,
                evaluation_reviewed=True,
            )
        except StyleABBundleNotApproved as error:
            raise StyleIterationConflict(str(error)) from error
        return await self.iterations.transition(
            run_id,
            status="evaluated",
            stage="published",
            progress_current=10,
            summary=f"{run['target_version']} 已发布为不可变 evaluated 版本，尚未启用。",
            event_type="version_published",
            metadata={"actor": actor, "version_record_id": asset.record_id},
            values={"published_version_record_id": asset.record_id},
            terminal=True,
        )

    async def _reconcile_rejected(self, run: dict[str, Any]) -> dict[str, Any]:
        if run["status"] != "ready_for_review":
            return run
        statistics = await self.reviews.statistics(candidate_profile_version=run["target_version"])
        counts = statistics["counts"]
        if counts["pending_case_count"] == 0 and counts["rejected_case_count"] > 0:
            return await self.iterations.transition(
                run["run_id"],
                status="rejected",
                stage="review_rejected",
                progress_current=10,
                summary=(
                    f"人工评审完成，其中 {counts['rejected_case_count']} 条被拒绝；"
                    "该版本不会发布，可作为下一版素材。"
                ),
                event_type="version_rejected",
                metadata={"counts": counts},
                terminal=True,
            )
        return run

    @staticmethod
    def _automated_passed(run: Mapping[str, Any]) -> bool:
        comparison = run.get("artifacts", {}).get("comparison")
        if not isinstance(comparison, dict):
            return False
        evaluation = comparison.get("evaluation")
        return bool(
            isinstance(evaluation, dict)
            and evaluation.get("passed") is True
            and not evaluation.get("missing_acts")
            and evaluation.get("results")
            and all(
                item.get("passed") is True and item.get("failure_code") is None
                for item in evaluation["results"]
            )
        )


class StyleRuntimeService:
    def __init__(self, database: Database, *, fallback_version: str) -> None:
        self.database = database
        self.fallback_version = fallback_version
        self.profiles = StyleProfileRepository(database)
        self.iterations = StyleIterationRepository(database)

    async def context(self) -> dict[str, Any]:
        runtime = await self.iterations.runtime_configuration(
            fallback_version=self.fallback_version
        )
        async with self.database.session() as session:
            events = tuple(
                await session.scalars(
                    select(StyleActivationEventRecord)
                    .order_by(StyleActivationEventRecord.runtime_revision.desc())
                    .limit(30)
                )
            )
        return {
            "runtime": runtime,
            "history": [self._event_view(item) for item in events],
        }

    async def activate(
        self,
        payload: StyleActivationAction,
        *,
        actor: str,
    ) -> dict[str, Any]:
        return await self._set_version(
            version=payload.version,
            expected_revision=payload.expected_revision,
            actor=actor,
            reason=payload.reason,
            action="activate",
        )

    async def rollback(
        self,
        payload: StyleRollbackAction,
        *,
        actor: str,
    ) -> dict[str, Any]:
        current = await self.iterations.runtime_configuration(
            fallback_version=self.fallback_version
        )
        target = current["previous_profile_version"]
        if not target:
            raise StyleIterationConflict("当前没有可回滚的上一个版本")
        return await self._set_version(
            version=target,
            expected_revision=payload.expected_revision,
            actor=actor,
            reason=payload.reason,
            action="rollback",
        )

    async def _set_version(
        self,
        *,
        version: str,
        expected_revision: int,
        actor: str,
        reason: str,
        action: str,
    ) -> dict[str, Any]:
        snapshot = await self.profiles.require_published(version)
        now = utc_now()
        async with self.database.session() as session, session.begin():
            runtime = await session.get(
                StyleRuntimeConfigurationRecord,
                "default",
                with_for_update=True,
            )
            if runtime is None:
                raise StyleIterationConflict("运行时风格配置尚未初始化")
            if runtime.revision != expected_revision:
                raise StyleIterationConflict("运行时版本已被其他操作修改，请刷新页面")
            if runtime.active_profile_version == version:
                raise StyleIterationConflict("目标版本已经处于启用状态")
            version_row = await session.scalar(
                select(StyleProfileVersionRecord).where(
                    StyleProfileVersionRecord.version == version
                )
            )
            if version_row is None:
                raise StyleIterationConflict("目标版本不存在")
            profile = await session.scalar(
                select(StyleProfileRecord).where(StyleProfileRecord.id == version_row.profile_id)
            )
            if profile is None:
                raise StyleIterationConflict("目标 Profile 不存在")
            previous = runtime.active_profile_version
            revision = runtime.revision + 1
            profile.active_version_id = version_row.id
            profile.updated_at = now
            runtime.active_profile_version = version
            runtime.previous_profile_version = previous
            runtime.revision = revision
            runtime.updated_by = actor
            runtime.updated_at = now
            session.add(
                StyleActivationEventRecord(
                    id=new_uuid(),
                    profile_id=snapshot.profile.profile_id,
                    previous_version=previous,
                    activated_version=version,
                    action=action,
                    actor=actor,
                    reason=reason.strip(),
                    runtime_revision=revision,
                    created_at=now,
                )
            )
            current_active = tuple(
                await session.scalars(
                    select(StyleIterationRunRecord).where(
                        StyleIterationRunRecord.status == "active"
                    )
                )
            )
            for run in current_active:
                run.status = "evaluated"
                run.stage = "published"
            target_run = await session.scalar(
                select(StyleIterationRunRecord).where(
                    StyleIterationRunRecord.target_version == version
                )
            )
            if target_run is not None:
                target_run.status = "active"
                target_run.stage = "active"
        return await self.context()

    @staticmethod
    def _event_view(row: StyleActivationEventRecord) -> dict[str, Any]:
        created_at = row.created_at
        if created_at.tzinfo is None:
            from datetime import UTC

            created_at = created_at.replace(tzinfo=UTC)
        return {
            "event_id": row.id,
            "profile_id": row.profile_id,
            "previous_version": row.previous_version,
            "activated_version": row.activated_version,
            "action": row.action,
            "actor": row.actor,
            "reason": row.reason,
            "runtime_revision": row.runtime_revision,
            "created_at": created_at.isoformat(),
        }


class StyleRuntimeVersionResolver:
    """Resolve the mutable exact-version pointer once for each workflow Turn."""

    def __init__(self, database: Database, *, fallback_version: str) -> None:
        self.repository = StyleIterationRepository(database)
        self.fallback_version = fallback_version

    async def resolve(self) -> str:
        configuration = await self.repository.runtime_configuration(
            fallback_version=self.fallback_version
        )
        return str(configuration["active_profile_version"])


__all__ = [
    "StyleActivationAction",
    "StyleIterationLifecycle",
    "StylePublishConfirmation",
    "StyleRollbackAction",
    "StyleRuntimeService",
    "StyleRuntimeVersionResolver",
]
