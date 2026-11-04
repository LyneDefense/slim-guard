"""Opt-in real-model conversation smoke test; synthetic data, no DB or publication.

PYTHONPATH=src .venv/bin/python tests/manual/doctor_dialogue_check.py --case exercise
Uses configured model credentials and consumes tokens. Printed replies are for
human assessment, not an automatic claim that a personality has been learned.
"""

import argparse
import asyncio
import json

from slim_guard.agent.assembly.style_preview import build_coach_preview
from slim_guard.agent_models.zhipu import ZhipuModelGateway
from slim_guard.config import Settings
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.package import (
    Evidence,
    FixedExample,
    Guide,
    GuideRule,
    make_package,
)
from slim_guard.expression_style.trainer.contracts import TestCase

CASES = {
    "greeting": TestCase(id="greeting", family="social", user_input="你好"),
    "exercise": TestCase(
        id="exercise",
        family="exercise",
        user_input="我今天不想运动",
        follow_up_inputs=("我就是不爱运动", "散步还行"),
    ),
    "correction": TestCase(
        id="correction",
        family="meal",
        user_input="买的是酸菜鱼",
        history=(
            DialogueMessage(role="user", content="这份鱼是我今天的午餐"),
            DialogueMessage(role="assistant", content="是什么做法？"),
        ),
    ),
    "detail": TestCase(
        id="detail",
        family="detail",
        user_input="详细解释一下怎么看体重变化",
        context=("没有用户个人的体重、诊断或目标数据。",),
    ),
}


async def main(selected: str) -> None:
    settings = Settings()
    if not settings.zhipu_is_configured:
        raise SystemExit("需要本地配置模型凭据；不会读取生产用户数据")
    guide = Guide(
        summary="话少，直接，情绪平稳。不刻意热情，不训人。",
        rules=(
            GuideRule(
                rule_id="reserved",
                text="回应当前这件事，省去多余复述和说教，能结束就结束。",
                boundary="用户要求解释或有重要风险时保留必要说明，不按固定字数或句数裁剪。",
                evidence=(Evidence(example_id="synthetic", revision=1, quote="那就休息一天。"),),
            ),
        ),
    )
    package = make_package(
        "doctor",
        "local-smoke",
        "本地合成风格样例",
        guide,
        (
            FixedExample(
                id="synthetic",
                revision=1,
                user_input="我今天不想运动",
                original_response="今天可以休息。",
                desired_response="那就休息一天。",
                rule_ids=("reserved",),
                dialogue=(
                    DialogueMessage(role="user", content="我今天不想运动"),
                    DialogueMessage(role="assistant", content="那就休息一天。"),
                ),
            ),
        ),
    )
    gateway = ZhipuModelGateway(
        api_key=settings.zhipu_api_key,
        base_url=settings.zhipu_base_url,
        timeout_seconds=60,
        thinking_enabled=False,
    )
    results = []
    try:
        preview = build_coach_preview(gateway, settings.zhipu_text_model, 120)
        for name, case in CASES.items():
            if selected not in {"all", name}:
                continue
            result = await preview.run(case=case, style=package.runtime_snapshot())
            results.append(result["passed"])
            print(
                json.dumps(
                    {
                        "case": name,
                        "model": settings.zhipu_text_model,
                        "passed": result["passed"],
                        "failure_code": result["failure_code"],
                        "style_advisory": result["style_advisory"],
                        "dialogue": result["dialogue"],
                        "model_calls": result["model_calls"],
                        "checks": result["checks"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await gateway.close()
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *CASES], default="greeting")
    asyncio.run(main(parser.parse_args().case))
