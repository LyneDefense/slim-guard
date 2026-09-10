from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from slim_guard.db.models import (
    NutritionCorpusReleaseRecord,
    NutritionEvaluationCaseRecord,
    NutritionEvaluationResultRecord,
    NutritionEvaluationRunRecord,
    NutritionKnowledgeSourceRecord,
    new_uuid,
    utc_now,
)
from slim_guard.nutrition_rag.repository import (
    NutritionJob,
    NutritionRagConflict,
    NutritionRagNotFound,
    NutritionRagRepository,
)
from slim_guard.nutrition_rag.retrieval import HybridNutritionRagService


@dataclass(frozen=True, slots=True)
class _CaseScore:
    passed: bool
    first_expected_rank: int | None
    expected_count: int
    recalled_at_5: int
    recalled_at_10: int
    insufficient_correct: bool | None
    forbidden_leak: bool
    citation_integrity: bool
    details: dict[str, Any]


class NutritionEvaluationService:
    """Runs frozen retrieval cases against an immutable candidate release."""

    def __init__(
        self,
        *,
        repository: NutritionRagRepository,
        retrieval: HybridNutritionRagService,
    ) -> None:
        self.repository = repository
        self.database = repository.database
        self.retrieval = retrieval

    async def execute(
        self,
        job: NutritionJob,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        run_id = job.input.get("evaluation_run_id")
        if not isinstance(run_id, str):
            raise ValueError("Evaluation job has no run ID")
        async with self.database.session() as session, session.begin():
            run = await session.get(NutritionEvaluationRunRecord, run_id, with_for_update=True)
            if run is None:
                raise NutritionRagNotFound("Evaluation run does not exist")
            release = await session.get(NutritionCorpusReleaseRecord, run.release_id)
            if release is None:
                raise NutritionRagNotFound("Evaluation release does not exist")
            cases = tuple(
                await session.scalars(
                    select(NutritionEvaluationCaseRecord)
                    .where(NutritionEvaluationCaseRecord.dataset_id == run.dataset_id)
                    .order_by(NutritionEvaluationCaseRecord.case_key)
                )
            )
            if not cases:
                raise NutritionRagConflict("Evaluation dataset contains no cases")
            run.status = "running"
        await self.repository.complete_release_index_check(release.id)
        await self.repository.update_job_progress(
            job.id,
            worker_id=worker_id,
            stage="evaluating",
            message=f"开始运行 {len(cases)} 条冻结检索用例",
            completed_items=0,
            total_items=len(cases),
            lease_seconds=lease_seconds,
        )
        scores: list[_CaseScore] = []
        result_rows: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            query_plan = _object(case.query_plan_input_json)
            query = query_plan.get("query")
            metadata_filter = query_plan.get("metadata_filter", {})
            if not isinstance(query, str) or not isinstance(metadata_filter, dict):
                raise NutritionRagConflict("Evaluation case query plan is invalid")
            result = await self.retrieval.search(
                query=query,
                max_results=10,
                metadata_filter=metadata_filter,
                retrieved_in_invocation_id=f"evaluation-{run_id}-{case.id}",
                release_id=release.id,
            )
            score = await self._score_case(case=case, result=result)
            scores.append(score)
            result_rows.append(
                {
                    "id": new_uuid(),
                    "run_id": run_id,
                    "case_id": case.id,
                    "retrieval_run_id": result.get("retrieval_run_id"),
                    "passed": score.passed,
                    "rank": score.first_expected_rank,
                    "details_json": _json(score.details),
                }
            )
            await self.repository.update_job_progress(
                job.id,
                worker_id=worker_id,
                stage="evaluating",
                message=f"已完成 {index}/{len(cases)} 条评测",
                completed_items=index,
                total_items=len(cases),
                lease_seconds=lease_seconds,
            )
        metrics = self._metrics(scores)
        result_sha256 = hashlib.sha256(_json(result_rows).encode()).hexdigest()
        async with self.database.session() as session, session.begin():
            run = await session.get(NutritionEvaluationRunRecord, run_id, with_for_update=True)
            release = (
                await session.get(
                    NutritionCorpusReleaseRecord, job.input.get("release_id") or run.release_id
                )
                if run is not None
                else None
            )
            if run is None or release is None:
                raise NutritionRagNotFound("Evaluation run or release disappeared")
            for values in result_rows:
                session.add(NutritionEvaluationResultRecord(**values))
            run.status = "succeeded"
            run.metrics_json = _json(metrics)
            run.result_sha256 = result_sha256
            run.completed_at = utc_now()
            release.evaluation_run_id = run.id
            release.status = "review_ready" if metrics["gates_passed"] else "rejected"
        await self.repository.complete_job(
            job.id,
            worker_id=worker_id,
            output={
                "evaluation_run_id": run_id,
                "release_id": release.id,
                "metrics": metrics,
                "result_sha256": result_sha256,
            },
            message=(
                "离线检索评测通过，版本等待人工批准"
                if metrics["gates_passed"]
                else "离线检索评测完成，但发布门槛未通过"
            ),
        )

    async def fail(self, job: NutritionJob) -> None:
        run_id = job.input.get("evaluation_run_id")
        if not isinstance(run_id, str):
            return
        async with self.database.session() as session, session.begin():
            run = await session.get(NutritionEvaluationRunRecord, run_id, with_for_update=True)
            if run is not None and run.status in {"queued", "running"}:
                run.status = "failed"
                run.completed_at = utc_now()

    async def _score_case(
        self,
        *,
        case: NutritionEvaluationCaseRecord,
        result: Any,
    ) -> _CaseScore:
        if not isinstance(result, dict):
            raise NutritionRagConflict("Evaluation retrieval result is invalid")
        raw_candidates = result.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise NutritionRagConflict("Evaluation candidates are invalid")
        candidates = [item for item in raw_candidates if isinstance(item, dict)]
        source_ids = tuple(
            dict.fromkeys(
                item.get("source_id")
                for item in candidates
                if isinstance(item.get("source_id"), str)
            )
        )
        async with self.database.session() as session:
            keys = dict(
                (
                    await session.execute(
                        select(
                            NutritionKnowledgeSourceRecord.id,
                            NutritionKnowledgeSourceRecord.source_key,
                        ).where(NutritionKnowledgeSourceRecord.id.in_(source_ids))
                    )
                )
                .tuples()
                .all()
            )
        ranked_keys = [_source_key(item, keys) for item in candidates]
        adopted = [
            item for item in candidates if item.get("adoption_status") in {"adopted", "selected"}
        ]
        expected = _strings(case.expected_source_keys_json)
        forbidden = set(_strings(case.forbidden_source_keys_json))
        concepts = _strings(case.expected_chunk_concepts_json)
        expected_ranks = [
            index for index, source_key in enumerate(ranked_keys, start=1) if source_key in expected
        ]
        first_rank = min(expected_ranks) if expected_ranks else None
        recalled_5 = len(set(ranked_keys[:5]).intersection(expected))
        recalled_10 = len(set(ranked_keys[:10]).intersection(expected))
        forbidden_leak = bool(forbidden.intersection(_source_key(item, keys) for item in adopted))
        body = "\n".join(str(item.get("content", "")) for item in candidates[:10]).casefold()
        concepts_found = all(concept.casefold() in body for concept in concepts)
        citation_integrity = all(
            isinstance(item.get("content"), str)
            and hashlib.sha256(item["content"].encode()).hexdigest() == item.get("content_sha256")
            for item in candidates
        )
        if case.expected_outcome == "insufficient":
            insufficient_correct: bool | None = not adopted
            passed = bool(insufficient_correct and not forbidden_leak and citation_integrity)
        else:
            insufficient_correct = None
            passed = (
                (not expected or bool(expected_ranks))
                and concepts_found
                and bool(adopted)
                and not forbidden_leak
                and citation_integrity
            )
        return _CaseScore(
            passed=passed,
            first_expected_rank=first_rank,
            expected_count=len(expected),
            recalled_at_5=recalled_5,
            recalled_at_10=recalled_10,
            insufficient_correct=insufficient_correct,
            forbidden_leak=forbidden_leak,
            citation_integrity=citation_integrity,
            details={
                "case_key": case.case_key,
                "retrieval_run_id": result.get("retrieval_run_id"),
                "expected_source_keys": list(expected),
                "ranked_source_keys": ranked_keys,
                "adopted_count": len(adopted),
                "concepts_found": concepts_found,
                "forbidden_leak": forbidden_leak,
                "citation_integrity": citation_integrity,
            },
        )

    @staticmethod
    def _metrics(scores: list[_CaseScore]) -> dict[str, Any]:
        positives = [score for score in scores if score.expected_count]
        negative = [score for score in scores if score.insufficient_correct is not None]
        expected_total = sum(score.expected_count for score in positives)
        recall_5 = (
            sum(score.recalled_at_5 for score in positives) / expected_total
            if expected_total
            else 1.0
        )
        recall_10 = (
            sum(score.recalled_at_10 for score in positives) / expected_total
            if expected_total
            else 1.0
        )
        reciprocal_ranks = [
            1 / score.first_expected_rank
            for score in positives
            if score.first_expected_rank is not None
        ]
        mrr = sum(reciprocal_ranks) / len(positives) if positives else 1.0
        insufficient_precision = (
            sum(score.insufficient_correct is True for score in negative) / len(negative)
            if negative
            else 1.0
        )
        leakage_count = sum(score.forbidden_leak for score in scores)
        citation_integrity = sum(score.citation_integrity for score in scores) / len(scores)
        gates_passed = (
            recall_5 >= 0.90
            and recall_10 >= 0.95
            and insufficient_precision >= 0.95
            and leakage_count == 0
            and citation_integrity == 1.0
        )
        return {
            "case_count": len(scores),
            "passed_case_count": sum(score.passed for score in scores),
            "case_pass_rate": sum(score.passed for score in scores) / len(scores),
            "recall_at_5": round(recall_5, 6),
            "recall_at_10": round(recall_10, 6),
            "mrr": round(mrr, 6),
            "insufficient_precision": round(insufficient_precision, 6),
            "unpublished_leakage_count": leakage_count,
            "citation_integrity": round(citation_integrity, 6),
            "gates_passed": gates_passed,
            "thresholds": {
                "recall_at_5": 0.90,
                "recall_at_10": 0.95,
                "insufficient_precision": 0.95,
                "unpublished_leakage_count": 0,
                "citation_integrity": 1.0,
            },
        }


def _object(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise NutritionRagConflict("Stored evaluation object is invalid")
    return decoded


def _source_key(item: dict[str, Any], keys: dict[str, str]) -> str:
    source_id = item.get("source_id")
    return keys.get(source_id, "") if isinstance(source_id, str) else ""


def _strings(value: str) -> tuple[str, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise NutritionRagConflict("Stored evaluation list is invalid")
    return tuple(decoded)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ["NutritionEvaluationService"]
