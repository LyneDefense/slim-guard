"""Offline evaluations execute the same Agent and reviewer as online responses."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from slim_guard.agents.contracts import AgentInvocation, ResponseContentBlock, ResponsePlan
from slim_guard.agents.style.agent import RESPONSE_STYLE_PROMPT_VERSION, ResponseStyleAgent
from slim_guard.expression_style.contracts import StyleContext
from slim_guard.expression_style.package import StylePackage, content_hash
from slim_guard.expression_style.review.policy import review_signature
from slim_guard.runtime.invocation import InvocationRunner

from .client import TrainingClient
from .contracts import Comparison, TestCase


async def rewrite_case(
    client: TrainingClient,
    package: StylePackage,
    case: TestCase,
    stage: str,
) -> dict[str, Any]:
    signature = {
        "model": client.model,
        "review": review_signature(client.model),
        "rewrite": RESPONSE_STYLE_PROMPT_VERSION,
        "temperature": 0,
    }
    key = "rewrite:" + content_hash(
        {"package": package.package_hash, "case": case.model_dump(mode="json"), **signature}
    )
    cached = await client.port.load(key)
    if cached is not None:
        return dict(cached)
    client.gateway.stage = stage
    snapshot = package.runtime_snapshot()
    turn = str(uuid4())
    result = await ResponseStyleAgent(
        runner=InvocationRunner(model=client.gateway),
        model=client.model,
    ).run(
        invocation=AgentInvocation(
            invocation_id=str(uuid4()),
            trace_id=str(uuid4()),
            turn_id=turn,
            graph_version="style-trainer-v1",
            agent_role="response_style",
            agent_version=RESPONSE_STYLE_PROMPT_VERSION,
            deadline_at=datetime.now(UTC)
            + timedelta(seconds=client.gateway.budget.request_seconds),
            max_model_calls=4,
            max_tool_calls=0,
            max_total_tokens=32_000,
        ),
        context=StyleContext(
            turn_id=turn,
            profile=snapshot.profile,
            examples=snapshot.examples,
            compiled_prompt=snapshot.compiled_prompt,
            package_hash=snapshot.package_hash,
            user_input=case.user_input,
            minimal_context=case.context,
            protected_literals=case.protected_literals,
            response_plan=ResponsePlan(
                content_blocks=(
                    ResponseContentBlock(
                        block_id="neutral",
                        kind="social_act",
                        text=case.source_text,
                    ),
                )
            ),
        ),
    )
    outcome = {
        "package_hash": package.package_hash,
        "text": result.response.text,
        "passed": not result.used_fallback,
        "fallback": result.used_fallback,
        "failure_code": result.failure_code,
        "checks": list(result.checks),
        "tokens": result.total_token_count,
        "model_calls": result.model_call_count,
        "execution": signature,
    }
    await client.port.save(key, outcome, stage=stage, message="已保存改写及共享审查结果")
    return outcome


async def evaluate(
    client: TrainingClient,
    candidate: StylePackage,
    baseline: StylePackage,
    cases: tuple[TestCase, ...],
    references: list[dict[str, Any]],
    *,
    stage: str,
    label: str,
) -> list[dict[str, Any]]:
    results = []
    for index, case in enumerate(cases):
        before = await rewrite_case(client, baseline, case, stage)
        after = await rewrite_case(client, candidate, case, stage)
        # Swap ordering deterministically; the judge sees no version or candidate labels.
        swapped = int(content_hash(case.id)[0], 16) % 2 == 0
        left, right = (after, before) if swapped else (before, after)
        judge = await client.ask(
            Comparison,
            "匿名比较两个表达。依据共同的人工表达参考而非候选自己的 Guide。"
            "优先忠实原文，再比较风格匹配、自然程度和交流分寸。"
            "四个维度各给1-5分，1表示明显不满足、3表示部分满足、5表示充分满足。"
            "不能因更短就认为更好，也不能用高风格分抵消语义错误；证据不足则 uncertain。"
            "给出具体差异，不声称完整克隆真人；不要猜测哪个是新版本。",
            {
                "user_input": case.user_input,
                "source": case.source_text,
                "context": case.context,
                "left": left["text"],
                "right": right["text"],
                "human_references": references,
            },
            stage=stage,
        )
        winner = judge.winner
        preference = (
            ("candidate" if (winner == "left") == swapped else "baseline")
            if (winner in {"left", "right"})
            else winner
        )
        if not after["passed"]:
            preference = "baseline" if before["passed"] else "uncertain"
        item = {
            "case": case.model_dump(mode="json"),
            "baseline": before,
            "candidate": after,
            "preference": preference,
            "reason": judge.reason,
            "scores": {
                "candidate": (judge.left_scores if swapped else judge.right_scores).model_dump(),
                "baseline": (judge.right_scores if swapped else judge.left_scores).model_dump(),
            },
        }
        results.append(item)
        await client.port.save(
            f"evaluation:{label}",
            results,
            stage=stage,
            message=f"{label}：已评测 {index + 1}／{len(cases)}",
        )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(results),
        "candidate_better": sum(r["preference"] == "candidate" for r in results),
        "baseline_better": sum(r["preference"] == "baseline" for r in results),
        "tie": sum(r["preference"] == "tie" for r in results),
        "uncertain": sum(r["preference"] == "uncertain" for r in results),
        "candidate_failed": sum(not r["candidate"]["passed"] for r in results),
        "baseline_failed": sum(not r["baseline"]["passed"] for r in results),
        "regressions": sum(
            r["baseline"]["passed"] and not r["candidate"]["passed"] for r in results
        ),
        "repairs": sum(len(r["candidate"].get("checks", [])) > 1 for r in results),
        "fallbacks": sum(r["candidate"].get("fallback", False) for r in results),
        "dimensions": {
            side: {
                dimension: {
                    str(score): sum(r["scores"][side][dimension] == score for r in results)
                    for score in range(1, 6)
                }
                for dimension in ("fidelity", "style_match", "naturalness", "appropriateness")
            }
            for side in ("baseline", "candidate")
        },
    }
