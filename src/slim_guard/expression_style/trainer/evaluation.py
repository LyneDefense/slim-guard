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
from .contracts import TestCase


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
            + timedelta(seconds=client.gateway.config.request_seconds),
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
) -> list[dict[str, Any]]:
    results = []
    for index, case in enumerate(cases):
        before = await rewrite_case(client, baseline, case, "preview")
        after = await rewrite_case(client, candidate, case, "preview")
        item = {
            "case": case.model_dump(mode="json"),
            "baseline": before,
            "candidate": after,
            "reason": "语义检查通过，语气请人工确认" if after["passed"] else "语义或执行检查未通过",
        }
        results.append(item)
        await client.port.save(
            "evaluation:preview",
            results,
            stage="preview",
            message=f"试答：{index + 1}／{len(cases)}，不进行自动排名或调参",
        )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(results),
        "content_failed": sum(not r["candidate"]["passed"] for r in results),
        "changed": sum(r["candidate"]["text"] != r["case"]["source_text"] for r in results),
        "expression_advisories": sum(
            any(
                d.get("dimension") == "expression"
                for check in r["candidate"]["checks"]
                for d in check.get("details", [])
            )
            for r in results
        ),
    }
