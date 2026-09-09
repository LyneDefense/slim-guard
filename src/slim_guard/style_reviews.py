"""Trusted synthetic Style A/B cases and append-only named human reviews."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slim_guard.agents.contracts import (
    CommunicationAct,
    ContractModel,
    ResponsePlan,
    StyledResponse,
)
from slim_guard.db.models import (
    StyleABEvaluationCaseRecord,
    StyleABHumanReviewRecord,
    new_uuid,
    utc_now,
)
from slim_guard.db.session import Database
from slim_guard.style_corpus import StyleEvaluationScenario

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def style_ab_case_key(evaluation_sha256: str, case_id: str) -> str:
    return "style-ab-" + hashlib.sha256(f"{evaluation_sha256}:{case_id}".encode()).hexdigest()[:40]


class StyleABReviewError(RuntimeError):
    pass


class StyleABCaseNotFound(StyleABReviewError):
    pass


class StyleABCaseConflict(StyleABReviewError):
    pass


class StyleABBundleNotApproved(StyleABReviewError):
    pass


class TrustedStyleABSource(ContractModel):
    """Offline-only import authority; never accepted by an HTTP route."""

    source_id: str = Field(min_length=1, max_length=128)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_by: str = Field(min_length=1, max_length=128)
    required_communication_acts: tuple[CommunicationAct, ...] = Field(min_length=1, max_length=16)
    synthetic_confirmed: Literal[True]
    deidentified_confirmed: Literal[True]
    expression_assets_reviewed: Literal[True]
    contains_raw_chat: Literal[False] = False

    @field_validator("source_id", "imported_by")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Trusted source identifiers cannot be blank")
        return normalized

    @field_validator("required_communication_acts")
    @classmethod
    def unique_acts(cls, value: tuple[CommunicationAct, ...]) -> tuple[CommunicationAct, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Required communication acts must be unique")
        return value


class StyleABPairImport(ContractModel):
    """One synthetic plan rendered by two different profile versions."""

    case_id: str = Field(min_length=1, max_length=128)
    source_sample_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario: StyleEvaluationScenario
    response_plan: ResponsePlan
    baseline_response: StyledResponse
    candidate_response: StyledResponse
    baseline_profile_version: str = Field(min_length=1, max_length=128)
    candidate_profile_version: str = Field(min_length=1, max_length=128)
    baseline_generation_model: str = Field(min_length=1, max_length=128)
    candidate_generation_model: str = Field(min_length=1, max_length=128)
    baseline_example_ids: tuple[str, ...] = Field(default=(), max_length=5)
    candidate_example_ids: tuple[str, ...] = Field(default=(), max_length=5)
    candidate_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    automated_evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    automated_judge_model: str = Field(min_length=1, max_length=128)
    automated_judge_status: Literal["not_run", "passed", "failed", "error"]

    @field_validator(
        "case_id",
        "baseline_profile_version",
        "candidate_profile_version",
        "baseline_generation_model",
        "candidate_generation_model",
        "automated_judge_model",
    )
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A/B identifiers cannot be blank")
        return normalized

    @field_validator("baseline_example_ids", "candidate_example_ids")
    @classmethod
    def validate_example_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item or len(item) > 128 for item in normalized):
            raise ValueError("Example IDs must contain 1 to 128 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Example IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if self.baseline_profile_version == self.candidate_profile_version:
            raise ValueError("A/B responses must use different profile versions")
        if self.baseline_response.style_profile_version != self.baseline_profile_version:
            raise ValueError("Baseline response/profile version mismatch")
        if self.candidate_response.style_profile_version != self.candidate_profile_version:
            raise ValueError("Candidate response/profile version mismatch")
        self.baseline_response.validate_against_plan(self.response_plan)
        self.candidate_response.validate_against_plan(self.response_plan)
        return self


class StyleABHumanScore(ContractModel):
    style_match: int = Field(ge=1, le=5, strict=True)
    fidelity: int = Field(ge=1, le=5, strict=True)
    appropriateness: int = Field(ge=1, le=5, strict=True)
    decision: Literal["accept", "reject"]
    comment: str = Field(min_length=1, max_length=2000)
    corrects_review_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Review comment cannot be blank")
        return normalized


class StyleABReviewRepository:
    """Stores immutable synthetic pairs and append-only human score events."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @classmethod
    def pairs_manifest_sha256(
        cls,
        pairs: Sequence[StyleABPairImport],
        *,
        source_id: str,
        required_communication_acts: Sequence[CommunicationAct],
    ) -> str:
        payload = {
            "source_id": source_id,
            "required_communication_acts": sorted(act.value for act in required_communication_acts),
            "pairs": [pair.model_dump(mode="json") for pair in pairs],
        }
        return cls._sha256(payload)

    async def import_pairs(
        self,
        pairs: Sequence[StyleABPairImport],
        *,
        trusted_source: TrustedStyleABSource,
        created_at: datetime | None = None,
    ) -> tuple[str, ...]:
        if not pairs or len(pairs) > 10_000:
            raise ValueError("A/B import must contain 1 to 10000 pairs")
        created_at = self._aware(created_at or utc_now())
        expected_manifest = self.pairs_manifest_sha256(
            pairs,
            source_id=trusted_source.source_id,
            required_communication_acts=trusted_source.required_communication_acts,
        )
        if expected_manifest != trusted_source.manifest_sha256:
            raise ValueError("Trusted A/B source manifest does not match the imported pairs")

        bundle_keys = {
            (
                pair.candidate_bundle_sha256,
                pair.automated_evaluation_sha256,
                pair.candidate_profile_version,
            )
            for pair in pairs
        }
        if len(bundle_keys) != 1:
            raise ValueError("One A/B import must describe exactly one candidate bundle")
        required_acts = {act.value for act in trusted_source.required_communication_acts}
        observed_acts = {pair.response_plan.communication_act.value for pair in pairs}
        if not required_acts.issubset(observed_acts):
            missing = ", ".join(sorted(required_acts - observed_acts))
            raise ValueError(f"A/B import is missing required communication acts: {missing}")
        if len({pair.case_id for pair in pairs}) != len(pairs):
            raise ValueError("A/B case IDs must be unique within an import")

        required_acts_json = self._json(sorted(required_acts))
        imported_ids: list[str] = []
        try:
            async with self.database.session() as session, session.begin():
                existing_rows = tuple(
                    await session.scalars(
                        select(StyleABEvaluationCaseRecord).where(
                            StyleABEvaluationCaseRecord.case_key.in_(
                                [pair.case_id for pair in pairs]
                            )
                        )
                    )
                )
                existing_by_key = {row.case_key: row for row in existing_rows}
                for pair in pairs:
                    values = self._case_values(
                        pair,
                        trusted_source=trusted_source,
                        required_acts_json=required_acts_json,
                        created_at=created_at,
                    )
                    existing = existing_by_key.get(pair.case_id)
                    if existing is not None:
                        if not self._same_import(existing, values):
                            raise StyleABCaseConflict(
                                f"A/B case {pair.case_id} already exists with different data"
                            )
                        imported_ids.append(existing.id)
                        continue
                    row = StyleABEvaluationCaseRecord(id=new_uuid(), **values)
                    session.add(row)
                    await session.flush()
                    imported_ids.append(row.id)
        except IntegrityError as error:
            raise StyleABCaseConflict(
                "A/B import conflicts with existing immutable data"
            ) from error
        return tuple(imported_ids)

    async def list_cases(
        self,
        *,
        limit: int,
        offset: int,
        candidate_profile_version: str | None = None,
        communication_act: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {None, "pending", "accept", "reject"}:
            raise ValueError("Unsupported A/B review decision filter")
        conditions: list[Any] = []
        if candidate_profile_version:
            conditions.append(
                StyleABEvaluationCaseRecord.candidate_profile_version == candidate_profile_version
            )
        if communication_act:
            conditions.append(StyleABEvaluationCaseRecord.communication_act == communication_act)
        async with self.database.session() as session:
            statement = select(StyleABEvaluationCaseRecord)
            if conditions:
                statement = statement.where(*conditions)
            cases = tuple(
                await session.scalars(
                    statement.order_by(
                        StyleABEvaluationCaseRecord.created_at,
                        StyleABEvaluationCaseRecord.id,
                    )
                )
            )
            reviews = await self._reviews_for_cases(session, [row.id for row in cases])
        latest = self._latest_reviews(reviews)
        filtered = [
            row
            for row in cases
            if decision is None
            or (decision == "pending" and row.id not in latest)
            or (row.id in latest and latest[row.id].decision == decision)
        ]
        return {
            "items": [
                self._case_view(row, latest.get(row.id), include_content=False)
                for row in filtered[offset : offset + limit]
            ],
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
        }

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        async with self.database.session() as session:
            row = await session.get(StyleABEvaluationCaseRecord, case_id)
            if row is None:
                return None
            reviews = await self._reviews_for_cases(session, [row.id])
        latest = self._latest_reviews(reviews).get(row.id)
        result = self._case_view(row, latest, include_content=True)
        result["reviews"] = [self._review_view(review) for review in reviews]
        return result

    async def submit_review(
        self,
        *,
        case_id: str,
        actor: str,
        score: StyleABHumanScore,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        actor = actor.strip()
        if not actor or len(actor) > 128:
            raise ValueError("Authenticated review actor is invalid")
        created_at = self._aware(created_at or utc_now())
        try:
            async with self.database.session() as session, session.begin():
                case = await session.get(StyleABEvaluationCaseRecord, case_id)
                if case is None:
                    raise StyleABCaseNotFound(f"A/B case {case_id} does not exist")
                latest = self._latest_reviews(
                    await self._reviews_for_cases(session, [case_id])
                ).get(case_id)
                latest_id = latest.id if latest is not None else None
                if score.corrects_review_id != latest_id:
                    raise StyleABCaseConflict(
                        "Review correction must supersede the current latest review"
                    )
                review = StyleABHumanReviewRecord(
                    id=new_uuid(),
                    case_id=case_id,
                    supersedes_review_id=latest_id,
                    actor=actor,
                    style_match=score.style_match,
                    fidelity=score.fidelity,
                    appropriateness=score.appropriateness,
                    decision=score.decision,
                    comment=score.comment,
                    created_at=created_at,
                )
                session.add(review)
                await session.flush()
                return self._review_view(review)
        except IntegrityError as error:
            raise StyleABCaseConflict(
                "Review was superseded concurrently; reload before correcting"
            ) from error

    async def statistics(
        self,
        *,
        candidate_profile_version: str | None = None,
    ) -> dict[str, Any]:
        conditions: list[Any] = []
        if candidate_profile_version:
            conditions.append(
                StyleABEvaluationCaseRecord.candidate_profile_version == candidate_profile_version
            )
        async with self.database.session() as session:
            statement = select(StyleABEvaluationCaseRecord)
            if conditions:
                statement = statement.where(*conditions)
            cases = tuple(await session.scalars(statement))
            reviews = await self._reviews_for_cases(session, [row.id for row in cases])
        latest = self._latest_reviews(reviews)
        current = [latest[row.id] for row in cases if row.id in latest]
        accepted = sum(review.decision == "accept" for review in current)
        reviewed = len(current)

        def score_summary(field: str) -> dict[str, int | float | None]:
            values = [int(getattr(review, field)) for review in current]
            return {
                "sample_count": len(values),
                "average": sum(values) / len(values) if values else None,
            }

        return {
            "counts": {
                "case_count": len(cases),
                "reviewed_case_count": reviewed,
                "pending_case_count": len(cases) - reviewed,
                "accepted_case_count": accepted,
                "rejected_case_count": reviewed - accepted,
            },
            "rates": {
                "acceptance_rate": accepted / reviewed if reviewed else 0.0,
            },
            "denominators": {
                "acceptance_rate": reviewed,
                "score_averages": reviewed,
            },
            "scores": {
                "style_match": score_summary("style_match"),
                "fidelity": score_summary("fidelity"),
                "appropriateness": score_summary("appropriateness"),
            },
        }

    async def candidate_profile_versions(self) -> tuple[str, ...]:
        """Return imported candidate versions, newest Case import first."""

        async with self.database.session() as session:
            versions = tuple(
                await session.scalars(
                    select(StyleABEvaluationCaseRecord.candidate_profile_version)
                    .order_by(
                        StyleABEvaluationCaseRecord.created_at.desc(),
                        StyleABEvaluationCaseRecord.id.desc(),
                    )
                )
            )
        return tuple(dict.fromkeys(versions))

    async def require_bundle_approval(
        self,
        *,
        bundle_sha256: str,
        evaluation_sha256: str,
        candidate_profile_version: str,
    ) -> dict[str, Any]:
        self._digest(bundle_sha256, field="bundle_sha256")
        self._digest(evaluation_sha256, field="evaluation_sha256")
        async with self.database.session() as session:
            cases = tuple(
                await session.scalars(
                    select(StyleABEvaluationCaseRecord)
                    .where(
                        StyleABEvaluationCaseRecord.candidate_bundle_sha256 == bundle_sha256,
                        StyleABEvaluationCaseRecord.automated_evaluation_sha256
                        == evaluation_sha256,
                        StyleABEvaluationCaseRecord.candidate_profile_version
                        == candidate_profile_version,
                    )
                    .order_by(
                        StyleABEvaluationCaseRecord.created_at,
                        StyleABEvaluationCaseRecord.id,
                    )
                )
            )
            reviews = await self._reviews_for_cases(session, [row.id for row in cases])
        if not cases:
            raise StyleABBundleNotApproved("No A/B cases bind this bundle and evaluation")
        latest = self._latest_reviews(reviews)
        if len(latest) != len(cases) or any(latest[row.id].decision != "accept" for row in cases):
            raise StyleABBundleNotApproved("Every A/B case requires a latest human acceptance")
        if any(row.automated_judge_status != "passed" for row in cases):
            raise StyleABBundleNotApproved("Every A/B case requires a passed automated judge")
        required_sets = {
            tuple(self._string_list(row.required_communication_acts_json)) for row in cases
        }
        if len(required_sets) != 1:
            raise StyleABBundleNotApproved("A/B cases disagree on required communication acts")
        required_acts = set(next(iter(required_sets)))
        observed_acts = {row.communication_act for row in cases}
        if not required_acts.issubset(observed_acts):
            raise StyleABBundleNotApproved("A/B cases do not cover required communication acts")

        case_reviews = [
            {
                "case_id": row.case_key,
                "source_sample_sha256": row.source_sample_sha256,
                "scenario_sha256": row.scenario_sha256,
                "review_id": latest[row.id].id,
                "actor": latest[row.id].actor,
                "style_match": latest[row.id].style_match,
                "fidelity": latest[row.id].fidelity,
                "appropriateness": latest[row.id].appropriateness,
                "reviewed_at": self._aware(latest[row.id].created_at).isoformat(),
            }
            for row in cases
        ]
        receipt: dict[str, Any] = {
            "schema_version": "1",
            "bundle_sha256": bundle_sha256,
            "evaluation_sha256": evaluation_sha256,
            "candidate_profile_version": candidate_profile_version,
            "case_count": len(cases),
            "required_communication_acts": sorted(required_acts),
            "automated_judge_models": sorted({row.automated_judge_model for row in cases}),
            "reviewer_actors": sorted({review["actor"] for review in case_reviews}),
            "reviewed_at": max(str(review["reviewed_at"]) for review in case_reviews),
            "case_reviews": case_reviews,
        }
        receipt["approval_sha256"] = self._sha256(receipt)
        return receipt

    def _case_values(
        self,
        pair: StyleABPairImport,
        *,
        trusted_source: TrustedStyleABSource,
        required_acts_json: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        plan = pair.response_plan.model_dump(mode="json")
        baseline = pair.baseline_response.model_dump(mode="json")
        candidate = pair.candidate_response.model_dump(mode="json")
        return {
            "case_key": pair.case_id,
            "source_kind": "synthetic_evaluation",
            "source_sample_sha256": pair.source_sample_sha256,
            "scenario_json": self._json(pair.scenario.model_dump(mode="json")),
            "scenario_sha256": self._sha256(pair.scenario.model_dump(mode="json")),
            "import_source_id": trusted_source.source_id,
            "import_manifest_sha256": trusted_source.manifest_sha256,
            "response_plan_json": self._json(plan),
            "response_plan_sha256": self._sha256(plan),
            "communication_act": pair.response_plan.communication_act.value,
            "required_communication_acts_json": required_acts_json,
            "baseline_profile_version": pair.baseline_profile_version,
            "candidate_profile_version": pair.candidate_profile_version,
            "baseline_generation_model": pair.baseline_generation_model,
            "candidate_generation_model": pair.candidate_generation_model,
            "baseline_example_ids_json": self._json(list(pair.baseline_example_ids)),
            "candidate_example_ids_json": self._json(list(pair.candidate_example_ids)),
            "baseline_response_json": self._json(baseline),
            "candidate_response_json": self._json(candidate),
            "baseline_response_sha256": self._sha256(baseline),
            "candidate_response_sha256": self._sha256(candidate),
            "candidate_bundle_sha256": pair.candidate_bundle_sha256,
            "automated_evaluation_sha256": pair.automated_evaluation_sha256,
            "automated_judge_model": pair.automated_judge_model,
            "automated_judge_status": pair.automated_judge_status,
            "imported_by": trusted_source.imported_by,
            "created_at": created_at,
        }

    @staticmethod
    def _same_import(
        row: StyleABEvaluationCaseRecord,
        values: Mapping[str, Any],
    ) -> bool:
        return all(
            field == "created_at" or getattr(row, field) == value for field, value in values.items()
        )

    @staticmethod
    async def _reviews_for_cases(
        session: AsyncSession,
        case_ids: Sequence[str],
    ) -> tuple[StyleABHumanReviewRecord, ...]:
        if not case_ids:
            return ()
        return tuple(
            await session.scalars(
                select(StyleABHumanReviewRecord)
                .where(StyleABHumanReviewRecord.case_id.in_(case_ids))
                .order_by(
                    StyleABHumanReviewRecord.created_at,
                    StyleABHumanReviewRecord.id,
                )
            )
        )

    @staticmethod
    def _latest_reviews(
        reviews: Sequence[StyleABHumanReviewRecord],
    ) -> dict[str, StyleABHumanReviewRecord]:
        latest: dict[str, StyleABHumanReviewRecord] = {}
        # The explicit correction chain defines recency, not wall-clock ordering.
        # Clock adjustments or equal timestamps must not resurrect an older acceptance.
        superseded = {review.supersedes_review_id for review in reviews}
        for review in reviews:
            if review.id in superseded:
                continue
            if review.case_id in latest:
                raise StyleABCaseConflict("Human review history has multiple current branches")
            latest[review.case_id] = review
        return latest

    @classmethod
    def _review_view(cls, review: StyleABHumanReviewRecord) -> dict[str, Any]:
        return {
            "review_id": review.id,
            "case_id": review.case_id,
            "supersedes_review_id": review.supersedes_review_id,
            "actor": review.actor,
            "style_match": review.style_match,
            "fidelity": review.fidelity,
            "appropriateness": review.appropriateness,
            "decision": review.decision,
            "comment": review.comment,
            "created_at": cls._aware(review.created_at),
        }

    @classmethod
    def _case_view(
        cls,
        row: StyleABEvaluationCaseRecord,
        latest: StyleABHumanReviewRecord | None,
        *,
        include_content: bool,
    ) -> dict[str, Any]:
        scenario = cls._object(row.scenario_json)
        result: dict[str, Any] = {
            "case_id": row.id,
            "case_key": row.case_key,
            "source_kind": row.source_kind,
            "source_sample_sha256": row.source_sample_sha256,
            "scenario_title": scenario.get("title", "合成评估场景"),
            "scenario_sha256": row.scenario_sha256,
            "response_plan_sha256": row.response_plan_sha256,
            "communication_act": row.communication_act,
            "required_communication_acts": cls._string_list(row.required_communication_acts_json),
            "baseline": {
                "profile_version": row.baseline_profile_version,
                "generation_model": row.baseline_generation_model,
                "example_ids": cls._string_list(row.baseline_example_ids_json),
                "response_sha256": row.baseline_response_sha256,
            },
            "candidate": {
                "profile_version": row.candidate_profile_version,
                "generation_model": row.candidate_generation_model,
                "example_ids": cls._string_list(row.candidate_example_ids_json),
                "response_sha256": row.candidate_response_sha256,
            },
            "candidate_bundle_sha256": row.candidate_bundle_sha256,
            "automated_judge": {
                "status": row.automated_judge_status,
                "model": row.automated_judge_model,
                "evaluation_sha256": row.automated_evaluation_sha256,
            },
            "latest_human_review": (cls._review_view(latest) if latest is not None else None),
            "created_at": cls._aware(row.created_at),
        }
        if include_content:
            result["scenario"] = scenario
            result["response_plan"] = cls._object(row.response_plan_json)
            result["baseline"]["response"] = cls._object(row.baseline_response_json)
            result["candidate"]["response"] = cls._object(row.candidate_response_json)
        return result

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _sha256(cls, value: Any) -> str:
        return hashlib.sha256(cls._json(value).encode()).hexdigest()

    @staticmethod
    def _digest(value: str, *, field: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError(f"{field} must be lowercase SHA-256 hex")
        return value

    @staticmethod
    def _object(value: str) -> dict[str, Any]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise StyleABCaseConflict("Stored A/B object is invalid")
        return decoded

    @staticmethod
    def _string_list(value: str) -> list[str]:
        decoded = json.loads(value)
        if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
            raise StyleABCaseConflict("Stored A/B string list is invalid")
        return decoded

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "StyleABBundleNotApproved",
    "StyleABCaseConflict",
    "StyleABCaseNotFound",
    "StyleABHumanScore",
    "StyleABPairImport",
    "StyleABReviewError",
    "StyleABReviewRepository",
    "TrustedStyleABSource",
    "style_ab_case_key",
]
