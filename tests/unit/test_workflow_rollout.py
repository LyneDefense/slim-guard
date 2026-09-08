"""Synthetic test reports only; these fixtures are not real release evidence."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from slim_guard.rollout import REQUIRED_REGRESSIONS, RolloutEvidence, check_rollout
from slim_guard.tools import check_workflow_rollout

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
STAGES = [("test_canary", 20, 20), ("small_canary", 100, 50), ("on", 500, 100)]


def synthetic_report(**changes: Any) -> dict[str, Any]:
    """Return explicitly labelled TEST data, never an operator approval report."""
    report: dict[str, Any] = {
        "target_stage": "test_canary",
        "actor": "TEST-ONLY-synthetic-reviewer",
        "graph_version": "TEST-ONLY-graph-v1",
        "agent_version": "TEST-ONLY-agent-v1",
        "profile_version": "TEST-ONLY-profile-v1",
        "evaluated_at": NOW.isoformat(),
        "metrics_ref": "tests/synthetic-runtime-metrics",
        "sampled_workflows": 20,
        "rejected_workflows": 0,
        "repaired_workflows": 0,
        "degraded_workflows": 0,
        "latency_p95_ms": 60_000,
        "tokens_p95": 64_000,
        "maximum_node_failure_rate": 0.02,
        "rag_enabled": True,
        "rag_claims": 10,
        "covered_rag_claims": 10,
        "invalid_citations": 0,
        "critical_safety_failures": 0,
        "duplicate_business_writes": 0,
        "quality_pair_count": 20,
        "legacy_quality_mean": 4,
        "candidate_quality_mean": 4,
        "quality_review_ref": "tests/synthetic-human-quality-pairs",
        "real_failure_sample_count": 2,
        "real_failure_retests_passed": 2,
        "regressions": [
            {"case_id": case, "passed": True, "evidence_ref": f"tests/synthetic/{case}"}
            for case in sorted(REQUIRED_REGRESSIONS)
        ],
    }
    report.update(changes)
    return report


def decision(**changes: Any):
    return check_rollout(RolloutEvidence.model_validate(synthetic_report(**changes)), now=NOW)


@pytest.mark.parametrize(("stage", "samples", "pairs"), STAGES)
def test_each_stage_accepts_exact_minimum_samples_and_quality_pairs(
    stage: str,
    samples: int,
    pairs: int,
) -> None:
    result = decision(target_stage=stage, sampled_workflows=samples, quality_pair_count=pairs)
    assert result.passed
    assert result.reasons == ()
    assert (result.minimum_samples, result.minimum_quality_pairs) == (samples, pairs)
    assert result.target_stage == stage
    assert result.deployment_performed is False


@pytest.mark.parametrize(("stage", "samples", "pairs"), STAGES)
def test_each_stage_rejects_insufficient_runtime_samples(
    stage: str,
    samples: int,
    pairs: int,
) -> None:
    result = decision(
        target_stage=stage,
        sampled_workflows=samples - 1,
        quality_pair_count=min(pairs, samples - 1),
    )
    assert not result.passed
    assert "insufficient_runtime_samples" in result.reasons


@pytest.mark.parametrize(("stage", "samples", "pairs"), STAGES)
def test_each_stage_rejects_insufficient_human_quality_pairs(
    stage: str,
    samples: int,
    pairs: int,
) -> None:
    result = decision(target_stage=stage, sampled_workflows=samples, quality_pair_count=pairs - 1)
    assert result.reasons == ("insufficient_human_quality_pairs",)


@pytest.mark.parametrize("candidate", [1.0, 3.99])
def test_candidate_quality_must_not_regress_against_legacy(candidate: float) -> None:
    assert decision(candidate_quality_mean=candidate).reasons == ("candidate_quality_regression",)


def test_missing_and_failed_required_regressions_are_reported() -> None:
    regressions = synthetic_report()["regressions"]
    missing = regressions.pop(0)["case_id"]
    regressions[0]["passed"] = False
    failed = regressions[0]["case_id"]
    result = decision(regressions=regressions)
    assert not result.passed
    assert set(result.reasons) == {
        f"regression_missing_or_failed:{missing}",
        f"regression_missing_or_failed:{failed}",
        "regression_failure_present",
    }


def test_failed_additional_regression_cannot_be_ignored() -> None:
    regressions = synthetic_report()["regressions"]
    regressions.append({"case_id": "TEST-extra", "passed": False, "evidence_ref": "tests/extra"})
    assert decision(regressions=regressions).reasons == ("regression_failure_present",)


@pytest.mark.parametrize("age", [timedelta(days=7, microseconds=1), timedelta(microseconds=-1)])
def test_stale_or_future_evidence_cannot_pass(age: timedelta) -> None:
    assert decision(evaluated_at=NOW - age).reasons == ("evidence_stale_or_future",)


def test_seven_day_evidence_boundary_is_inclusive() -> None:
    assert decision(evaluated_at=NOW - timedelta(days=7)).passed


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("critical_safety_failures", 1, "critical_safety_failures_present"),
        ("duplicate_business_writes", 1, "duplicate_business_writes_present"),
        ("invalid_citations", 1, "invalid_citations_present"),
        ("covered_rag_claims", 9, "rag_coverage_incomplete"),
        ("real_failure_retests_passed", 1, "real_failure_retests_incomplete"),
        ("latency_p95_ms", 60_001, "latency_p95_exceeded"),
        ("tokens_p95", 64_001, "tokens_p95_exceeded"),
        ("maximum_node_failure_rate", 0.02001, "node_failure_rate_exceeded"),
    ],
)
def test_safety_integrity_and_runtime_failures_block_release(
    field: str,
    value: int | float,
    reason: str,
) -> None:
    result = decision(**{field: value})
    assert not result.passed
    assert result.reasons == (reason,)


@pytest.mark.parametrize(
    ("field", "allowed", "reason"),
    [
        ("rejected_workflows", 2, "rejection_rate_exceeded"),
        ("repaired_workflows", 15, "repair_rate_exceeded"),
        ("degraded_workflows", 5, "degradation_rate_exceeded"),
    ],
)
def test_outcome_rates_accept_ceiling_and_reject_above_it(
    field: str,
    allowed: int,
    reason: str,
) -> None:
    assert decision(sampled_workflows=100, **{field: allowed}).passed
    result = decision(sampled_workflows=100, **{field: allowed + 1})
    assert result.reasons == (reason,)


@pytest.mark.parametrize(
    "changes",
    [
        {"rejected_workflows": 21},
        {"repaired_workflows": 21},
        {"degraded_workflows": 21},
        {"quality_pair_count": 21},
        {"covered_rag_claims": 11},
        {"real_failure_retests_passed": 3},
        {"sampled_workflows": True},
        {"sampled_workflows": "20"},
        {"sampled_workflows": 20.5},
        {"invalid_citations": -1},
        {"candidate_quality_mean": 5.1},
        {"legacy_quality_mean": 0.9},
        {"latency_p95_ms": float("nan")},
        {"tokens_p95": float("inf")},
        {"evaluated_at": NOW.replace(tzinfo=None)},
        {"metrics_ref": " "},
        {"quality_review_ref": " "},
    ],
)
def test_inconsistent_or_invalid_report_values_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RolloutEvidence.model_validate(synthetic_report(**changes))


def test_duplicate_regression_ids_cannot_inflate_evidence() -> None:
    regressions = synthetic_report()["regressions"]
    regressions.append(dict(regressions[0]))
    with pytest.raises(ValidationError, match="Regression case IDs must be unique"):
        RolloutEvidence.model_validate(synthetic_report(regressions=regressions))


def test_zero_workflow_denominator_fails_without_dividing_by_zero() -> None:
    result = decision(sampled_workflows=0, quality_pair_count=0)
    assert not result.passed
    assert result.reasons == ("insufficient_runtime_samples", "insufficient_human_quality_pairs")


def test_enabled_rag_needs_claim_samples_while_disabled_rag_can_have_none() -> None:
    assert decision(rag_claims=0, covered_rag_claims=0).reasons == ("rag_claim_samples_missing",)
    assert decision(rag_enabled=False, rag_claims=0, covered_rag_claims=0).passed


def test_gate_rejects_naive_clock() -> None:
    evidence = RolloutEvidence.model_validate(synthetic_report())
    with pytest.raises(ValueError, match="Gate clock must be timezone-aware"):
        check_rollout(evidence, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_cli_exit_codes_are_read_only_and_do_not_expose_invalid_reports(
    exit_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "TEST-ONLY-rollout-report.json"
    payload = synthetic_report(evaluated_at=datetime.now(UTC).isoformat())
    if exit_code == 1:
        payload["critical_safety_failures"] = 1
    text = json.dumps(payload) if exit_code != 2 else "INVALID PRIVATE TEST SENTINEL"
    report.write_text(text, encoding="utf-8")
    configuration = tmp_path / ".env"
    configuration.write_text("MULTI_AGENT_MODE=off\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MULTI_AGENT_MODE", "off")
    monkeypatch.setattr("sys.argv", ["check_workflow_rollout", str(report)])
    before_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    before_env = dict(os.environ)

    with pytest.raises(SystemExit) as raised:
        check_workflow_rollout.main()

    assert raised.value.code == exit_code
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["passed"] is (exit_code == 0)
    assert "PRIVATE TEST SENTINEL" not in output.out + output.err
    if exit_code == 2:
        assert result["reasons"] == ["invalid_report"]
    else:
        assert result["deployment_performed"] is False
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before_files
    assert dict(os.environ) == before_env


def test_cli_missing_report_returns_invalid_report_without_leaking_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "PRIVATE-MISSING-TEST-REPORT.json"
    monkeypatch.setattr("sys.argv", ["check_workflow_rollout", str(path)])
    with pytest.raises(SystemExit) as raised:
        check_workflow_rollout.main()
    assert raised.value.code == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {"passed": False, "reasons": ["invalid_report"]}
    assert "PRIVATE-MISSING" not in output.out + output.err


def test_cli_schema_is_available_without_a_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["check_workflow_rollout", "--schema"])
    check_workflow_rollout.main()
    schema = json.loads(capsys.readouterr().out)
    assert schema["properties"]["target_stage"]["enum"] == [stage for stage, _, _ in STAGES]
    assert "metrics_ref" in schema["required"]
    assert "quality_review_ref" in schema["required"]
