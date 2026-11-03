"""Opt-in synthetic calibration: python tests/manual/style_quantity_review_check.py.

Uses the configured model credentials and consumes tokens. No production data,
database, style packages or services are read/written. Not a default pytest test.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.agents.contracts import (
    AgentInvocation,
    ResponseContentBlock,
    ResponsePlan,
    StyledResponse,
)
from slim_guard.config import Settings
from slim_guard.expression_style.contracts import SLIMGUARD_DEFAULT_V1, StyleContext
from slim_guard.expression_style.review.policy import POLICY_VERSION
from slim_guard.expression_style.review.service import StyleReviewService
from slim_guard.runtime.invocation import InvocationRunner

# Synthetic pairs are reviewer calibration data, never runtime routing rules.
CASES = (
    (
        "repeated-quantity",
        True,
        "今天喝水1500毫升。",
        "今天喝水1500毫升，这1500毫升是今天的饮水量。",
    ),
    ("consolidated-quantity", True, "今天喝水1500毫升，饮水量为1500毫升。", "今天喝水1500毫升。"),
    ("chinese-numeral", True, "这周运动了3次。", "这周运动了三次。"),
    ("unit-conversion", True, "今天喝水1.5升。", "今天喝水1500毫升。"),
    ("decimal-format", True, "今天体重79.0kg。", "今天体重79kg。"),
    (
        "removed-list-markers",
        True,
        "1. 今天散步30分钟。\n2. 今天喝水1500毫升。",
        "今天散步30分钟，喝水1500毫升。",
    ),
    ("date-binding-swapped", False, "昨天体重79kg，今天78kg。", "昨天体重78kg，今天79kg。"),
    ("unit-changed", False, "今天喝水1.5升。", "今天喝水1500升。"),
    ("scope-changed", False, "每天喝水1500毫升。", "每次喝水1500毫升。"),
    ("independent-fact-omitted", False, "今天散步30分钟，游泳20分钟。", "今天散步30分钟。"),
    (
        "qualifiers-lost",
        False,
        "如果份量相同，这餐可能不到500千卡。",
        "这餐是500千卡。",
    ),
    ("unsupported-target", False, "今天散步30分钟。", "今天散步30分钟，明天增加到60分钟。"),
)


async def main(case_id: str | None = None) -> None:
    settings = Settings()
    if not settings.zhipu_is_configured:
        raise SystemExit("This opt-in calibration requires configured model credentials")
    gateway = ZhipuModelGateway(
        api_key=settings.zhipu_api_key,
        base_url=settings.zhipu_base_url,
        timeout_seconds=40,
    )
    reviewer = StyleReviewService(InvocationRunner(model=gateway), settings.zhipu_text_model)
    semaphore = asyncio.Semaphore(3)

    async def check(identifier: str, expected: bool, source: str, candidate: str) -> bool:
        async with semaphore:
            result = await reviewer.review(
                invocation=AgentInvocation(
                    invocation_id=identifier,
                    trace_id=identifier,
                    turn_id=identifier,
                    graph_version="synthetic-style-calibration",
                    agent_role="response_style",
                    agent_version=POLICY_VERSION,
                    deadline_at=datetime.now(UTC) + timedelta(seconds=45),
                ),
                context=StyleContext(
                    turn_id=identifier,
                    user_input="请按原意换个说法。",
                    profile=SLIMGUARD_DEFAULT_V1,
                    response_plan=ResponsePlan(
                        content_blocks=(
                            ResponseContentBlock(
                                block_id="neutral", kind="social_act", text=source
                            ),
                        )
                    ),
                ),
                response=StyledResponse(
                    text=candidate,
                    used_block_ids=("neutral",),
                    style_profile_version=SLIMGUARD_DEFAULT_V1.version,
                ),
                source_text=source,
            )
            matched = (
                result.passed == expected
                and result.verdict != "inconclusive"
                and all(c["status"] not in {"not_run", "error"} for c in result.checks)
            )
            print(
                json.dumps(
                    {
                        "case": identifier,
                        "expected_pass": expected,
                        "actual_pass": result.passed,
                        "verdict": result.verdict,
                        "matched": matched,
                        "issue_codes": [d["code"] for d in result.details],
                        "checks": result.checks,
                        "failure_code": result.failure_code,
                        "failure_details": result.issues if not matched else (),
                        "synthetic_review_output": [r.message.content for r in result.responses]
                        if not matched
                        else [],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return matched

    try:
        selected = [case for case in CASES if case_id is None or case[0] == case_id]
        results = await asyncio.gather(*(check(*case) for case in selected))
    finally:
        await gateway.close()
    print(
        json.dumps(
            {
                "model": settings.zhipu_text_model,
                "policy": POLICY_VERSION,
                "total": len(results),
                "matched": sum(results),
            }
        ),
        flush=True,
    )
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[case[0] for case in CASES])
    asyncio.run(main(parser.parse_args().case))
