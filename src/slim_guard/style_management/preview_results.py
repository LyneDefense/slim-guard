"""Atomically adopt targeted retry results, fencing stale workers and stale approvals."""

from typing import Any

from sqlalchemy import select

from slim_guard.db.models import utc_now
from slim_guard.db.session import Database
from slim_guard.expression_style.package import StylePackage, content_hash
from slim_guard.expression_style.trainer.evaluation import summarize
from slim_guard.expression_style.trainer.ports import BuildInterrupted

from .models import BuildRun, ReviewCase, Style, Version
from .preview_state import case_fingerprint


async def finish_retry(db: Database, run_id: str, token: str, result: dict[str, Any]) -> None:
    package = StylePackage.model_validate(result["package"])
    async with db.session() as session, session.begin():
        # Same lock order as publication and retry creation; heartbeat only locks its own job.
        await session.scalar(select(Style).where(Style.id == package.style_id).with_for_update())
        version = await session.scalar(
            select(Version)
            .where(
                Version.id == package.version_id,
            )
            .with_for_update()
        )
        row = await session.scalar(
            select(BuildRun)
            .where(
                BuildRun.id == run_id,
                BuildRun.worker_token == token,
                BuildRun.status == "running",
            )
            .with_for_update()
        )
        if row is None:
            raise BuildInterrupted("重试任务已取消或被接管")
        snapshot = row.snapshot
        if (
            snapshot.get("job_kind") != "preview_retry"
            or version is None
            or version.style_id != row.style_id
            or package.style_id != row.style_id
            or snapshot.get("retry_version_id") != version.id
        ):
            raise ValueError("重试结果归属错误")
        if version.status != "ready_for_review":
            raise ValueError("目标版本已发布，不能覆盖评审结果")
        current = StylePackage.model_validate(version.package)
        frozen = StylePackage.model_validate(snapshot["retry_package"])
        if (
            package.package_hash != current.package_hash
            or current.package_hash != frozen.package_hash
        ):
            raise ValueError("重试期间风格包已变化，结果不会采用")
        if content_hash(version.report) != content_hash(snapshot["retry_version_report"]):
            raise ValueError("重试期间版本报告已变化，结果不会覆盖新状态")
        inputs = {item["case_id"]: item for item in snapshot["retry_cases"]}
        results = result["cases"]
        ids = [item["case_id"] for item in results]
        if len(ids) != len(set(ids)) or set(ids) != inputs.keys():
            raise ValueError("重试结果必须与冻结的失败用例逐条对应")
        cases = list(
            await session.scalars(
                select(ReviewCase)
                .where(
                    ReviewCase.version_id == version.id,
                )
                .with_for_update()
            )
        )
        indexed = {case.id: case for case in cases}
        if (
            set(inputs) - indexed.keys()
            or len(cases) != snapshot["retry_version_report"]["preview_metrics"]["total"]
        ):
            raise ValueError("重试期间试答清单已变化")
        for item in results:
            case = indexed[item["case_id"]]
            before = inputs[case.id]
            if case_fingerprint(case) != before["fingerprint"]:
                raise ValueError("重试期间回答或评审已变化，结果不会覆盖新数据")
            outcome = item["candidate"]
            if (
                outcome.get("package_hash") != current.package_hash
                or outcome.get("execution", {}).get("review") != snapshot["review_signature"]
                or outcome.get("execution", {}).get("model") != snapshot["model"]
            ):
                raise ValueError("重试结果的风格包或检查签名不匹配")
            case.doctor_response = outcome["text"]
            case.automated = {
                **outcome,
                "output_revision": before["output_revision"] + 1,
                "retry_run_id": row.id,
                "last_review_revision": max(
                    (before["review"] or {}).get("revision", 0),
                    before["automated"].get("last_review_revision", 0),
                ),
            }
            # Old answers/approvals are frozen in retry_inputs, not silently reused for new output.
            case.review = None
        metrics = summarize(
            [{"case": case.test_case, "candidate": case.automated} for case in cases]
        )
        eligible = not metrics["content_failed"] and not any(
            c.automated.get("fallback") for c in cases
        )
        conclusion = "试答完成，请人工确认重试后的回答" if eligible else "仍有失败用例，暂不可发布"
        report = {
            **version.report,
            "preview_metrics": metrics,
            "release_eligible": eligible,
            "conclusion": conclusion,
            "last_preview_retry_id": row.id,
        }
        version.report = report
        row.report = report
        row.checkpoints = {**row.checkpoints, "final_report": report}
        now = utc_now()
        event = {
            "sequence": len(row.events) + 1,
            "stage": "complete",
            "state": "completed",
            "time": now.isoformat(),
            "message": conclusion,
        }
        row.events = [*row.events, event]
        row.stage, row.activity, row.progress_at = "complete", event, now
        row.status, row.finished_at = "completed", now
        row.worker_token, row.lease_until = None, None
