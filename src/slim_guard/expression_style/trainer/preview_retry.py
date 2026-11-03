"""Re-run frozen failed previews, never rebuild a style package or alter review policy."""

from typing import Any

from slim_guard.agents.contracts import ReviewerIssue, ReviewerVerdict
from slim_guard.expression_style.package import StylePackage

from .client import TrainingClient
from .contracts import TestCase
from .evaluation import EXECUTION_FAILURES, rewrite_case


def failure_feedback(outcome: dict[str, Any]) -> ReviewerVerdict:
    checks = outcome.get("checks", [])
    last = checks[-1] if checks else {}
    issues = last.get("issues", []) or [outcome.get("failure_code") or "previous_preview_failed"]
    return ReviewerVerdict(
        verdict="repair",
        repair_target="response_style",
        issue_type="changed_meaning",
        reason_summary="上次试答未通过。根据真实失败原因重新改写，保留原稿全部事实、数字和限定；"
        "不能修改风格包、原稿或检查要求来通过。上次输出仅供定位问题，不是新事实。",
        issues=(
            ReviewerIssue(
                type="changed_meaning",
                excerpt=(last.get("output") or "")[:1000] or None,
                explanation="；".join(str(issue) for issue in issues)[:2000],
            ),
        ),
    )


async def retry_previews(client: TrainingClient, snapshot: dict[str, Any]) -> dict[str, Any]:
    package = StylePackage.model_validate(snapshot["retry_package"])
    if package.version_id != snapshot["retry_version_id"]:
        raise ValueError("重试风格包版本不匹配")
    inputs = snapshot["retry_cases"]
    await client.port.save(
        "retry_inputs",
        inputs,
        stage="preview",
        message=f"只重试 {len(inputs)} 条失败用例，风格包不变",
    )
    results = []
    for item in inputs:
        outcome = await rewrite_case(
            client,
            package,
            TestCase.model_validate(item["test_case"]),
            "preview",
            review_feedback=failure_feedback(item["automated"]),
        )
        if outcome.get("failure_code") in EXECUTION_FAILURES:
            raise RuntimeError("失败用例执行未完成；保留原回答，恢复时复用已成功用例")
        results.append({"case_id": item["case_id"], "candidate": outcome})
        await client.port.save(
            "retry_results",
            list(results),
            stage="preview",
            message=f"失败用例重试 {len(results)}／{len(inputs)}，仍须通过共享检查和人工确认",
        )
    return {
        "job_kind": "preview_retry",
        "package": package.model_dump(mode="json"),
        "cases": results,
    }
