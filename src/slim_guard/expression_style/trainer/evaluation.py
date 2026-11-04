"""Evaluate direct conversations through the injected online coach preview port."""

from typing import Any

from slim_guard.expression_style.package import StylePackage, content_hash
from slim_guard.expression_style.review.policy import review_signature

from .client import TrainingClient
from .contracts import TestCase

EXECUTION_FAILURES = frozenset(
    {
        "style_internal_error",
        "model_gateway_error",
        "deadline_exceeded",
        "model_request_failed",
    }
)


async def freeze_execution(client: TrainingClient) -> str:
    if client.preview is None:
        raise ValueError("未装配医生试答端口")
    signature = client.preview.signature
    frozen = await client.port.load("coach_execution_signature")
    if frozen is not None and frozen != signature:
        raise ValueError("医生生成或审查配置已变化，请新建构建；不会在恢复时混用旧结果")
    if frozen is None:
        await client.port.save(
            "coach_execution_signature",
            signature,
            stage="freeze",
            message="已冻结与线上一致的医生生成、上下文、审查和修正策略签名",
        )
    return signature


async def preview_case(
    client: TrainingClient,
    package: StylePackage,
    case: TestCase,
    stage: str,
    *,
    review_feedback: tuple[dict[str, object], ...] = (),
) -> dict[str, Any]:
    preview = client.preview
    if preview is None:
        raise ValueError("未装配医生试答端口，不能退回旧改写流程")
    signature = {
        "model": client.model,
        "review": review_signature(client.model),
        "coach": preview.signature,
        "mode": "direct_generation",
        "temperature": 0,
    }
    key = "direct-preview:" + content_hash(
        {
            "package": package.package_hash,
            "case": case.model_dump(mode="json"),
            **signature,
            "repair_feedback": review_feedback,
        }
    )
    cached = await client.port.load(key)
    if cached is not None and cached.get("failure_code") not in EXECUTION_FAILURES:
        return dict(cached)
    client.gateway.stage = stage
    outcome = {
        **await preview.run(case=case, style=package.runtime_snapshot(), feedback=review_feedback),
        "package_hash": package.package_hash,
        "execution": signature,
    }
    if outcome.get("failure_code") not in EXECUTION_FAILURES:
        await client.port.save(
            key, outcome, stage=stage, message="已保存医生直接试答及共享审查结果"
        )
    return outcome


async def evaluate(
    client: TrainingClient,
    candidate: StylePackage,
    baseline: StylePackage,
    cases: tuple[TestCase, ...],
) -> list[dict[str, Any]]:
    results = []
    for index, case in enumerate(cases):
        before = await preview_case(client, baseline, case, "preview")
        after = await preview_case(client, candidate, case, "preview")
        results.append(
            {
                "case": case.model_dump(mode="json"),
                "baseline": before,
                "candidate": after,
                "reason": "事实与职责检查通过，语气请人工确认"
                if after["passed"]
                else "事实、职责或执行检查未通过",
            }
        )
        await client.port.save(
            "evaluation:preview",
            results,
            stage="preview",
            message=f"直接试答：{index + 1}／{len(cases)}，不进行自动排名或调参",
        )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(results),
        "content_failed": sum(not r["candidate"]["passed"] for r in results),
        "changed": sum(r["candidate"]["text"] != r["baseline"]["text"] for r in results),
        "expression_advisories": sum(
            any(
                d.get("dimension") == "expression"
                for check in r["candidate"]["checks"]
                for d in check.get("details", [])
            )
            for r in results
        ),
    }
