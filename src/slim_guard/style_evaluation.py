"""Generate comparable style outputs from synthetic or explicitly redacted plans.

Generation calls the real Style Agent; fallback output never counts as a pass.
This module neither creates human ratings nor publishes profiles.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import Field

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.contracts import (
    AgentInvocation,
    AgentRole,
    CommunicationAct,
    ContentBlockKind,
    ContractModel,
    ResponseContentBlock,
    ResponsePlan,
)
from slim_guard.agents.structured_runner import StructuredAgentRunner
from slim_guard.agents.style import ResponseStyleAgent, StyleContext
from slim_guard.agents.style.agent import RESPONSE_STYLE_PROMPT_VERSION
from slim_guard.agents.style.contracts import SLIMGUARD_DEFAULT_V1
from slim_guard.style_corpus import (
    OfflineStyleCorpus,
    StyleAssetBundle,
    StyleEvalCase,
    StyleEvaluationScenario,
)


class StyleEvaluationInput(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    scenario: StyleEvaluationScenario
    response_plan: ResponsePlan
    synthetic: bool = Field(default=False, strict=True)


def synthetic_style_suite() -> tuple[StyleEvaluationInput, ...]:
    """Public invented business scenarios, never copied from the source chat."""
    scenarios: tuple[
        tuple[CommunicationAct, str, str, str, str, str], ...
    ] = (
        (
            CommunicationAct.ACKNOWLEDGE,
            "确认一次体重打卡",
            "用户刚提交了一次日常体重打卡，正在等待回应。",
            "今天的体重 72.4kg 已记录。",
            "行。",
            "用一句短口语确认；含事实版本还必须保留记录成功和体重数值。",
        ),
        (
            CommunicationAct.CORRECT,
            "纠正餐食图片里的选择",
            "用户上传午餐图片并再次把炸鸡列为本餐选择；此前已向用户说明过这餐的选择。",
            "图片里看不到精确的分量。这餐可以吃青菜，不吃炸鸡。",
            "不是跟你讲过了吗？",
            "只依据既定本餐计划直接纠正，不把示例中的食物规则迁移到其他用户或餐次。",
        ),
        (
            CommunicationAct.REMIND,
            "追问当天漏记的午餐",
            "到了当天打卡检查时间，系统确认用户没有提交今天的午餐记录。",
            "你今天午餐没记录。",
            "为什么没记录？",
            "直接点明漏记的午餐并询问原因，不猜测用户为什么漏记。",
        ),
        (
            CommunicationAct.ENCOURAGE,
            "鼓励继续坚持记录",
            "用户已经坚持打卡一段时间，需要一句简短反馈来强化持续记录。",
            "你已经连续记录了 7 天。",
            "不错，要保持。",
            "用一句直接的话肯定并要求保持，不承诺减重结果。",
        ),
        (
            CommunicationAct.EXPLAIN,
            "解释为什么暂时不能判断趋势",
            "用户只记录了 3 天，询问能否据此判断长期变化趋势。",
            "目前只有 3 天记录，不能判断长期趋势。",
            "现在得不出结论。",
            "点明记录天数太少，直接说明判断不了长期趋势、得不出结论。",
        ),
        (
            CommunicationAct.ASK,
            "追问一条记录的日期",
            "用户提交了一条记录，但没有说明这条记录对应哪一天。",
            "这条记录没有日期。",
            "你记录的是哪一天的？",
            "直接问记录的是哪一天，不先礼貌铺垫，不猜测日期或添加其他要求。",
        ),
    )
    cases: list[StyleEvaluationInput] = []
    for act, title, situation, fact, social, goal in scenarios:
        for protected in (False, True):
            blocks = (
                (
                    ResponseContentBlock(
                        block_id="verified-fact",
                        kind=ContentBlockKind.FACT,
                        text=fact,
                        source_refs=(f"synthetic:{act.value}:verified-fact",),
                    ),
                )
                if protected
                else ()
            )
            cases.append(
                StyleEvaluationInput(
                    case_id=f"synthetic-{act.value}-{'protected' if protected else 'social'}",
                    scenario=StyleEvaluationScenario(
                        title=f"{title}（{'含受保护事实' if protected else '只测试表达'}）",
                        user_situation=situation,
                        known_context=(
                            (
                                f"系统已确认：{fact}该内容必须原样保留。"
                                if protected
                                else "该版本不提供可改写的事实块，只测试沟通行为的表达方式。"
                            ),
                            f"沟通行为：{act.value}。",
                        ),
                        response_goal=goal,
                    ),
                    synthetic=True,
                    response_plan=ResponsePlan(
                        communication_act=act,
                        content_blocks=(
                            *blocks,
                            ResponseContentBlock(
                                block_id="communication",
                                kind=ContentBlockKind.SOCIAL_ACT,
                                text=social,
                            ),
                        ),
                    ),
                )
            )
    return tuple(cases)


async def generate_style_comparisons(
    *,
    bundle: StyleAssetBundle,
    inputs: tuple[StyleEvaluationInput, ...],
    gateway: ModelGateway,
    model: str,
    corpus: OfflineStyleCorpus,
    actor: str,
    redacted_inputs_confirmed: bool,
) -> dict[str, Any]:
    if not redacted_inputs_confirmed:
        raise ValueError("Explicit confirmation of synthetic/redacted input plans is required")
    if not inputs or len(inputs) > 60 or len({case.case_id for case in inputs}) != len(inputs):
        raise ValueError("Provide 1 to 60 distinct evaluation inputs")
    if bundle.profile.version == SLIMGUARD_DEFAULT_V1.version:
        raise ValueError("Candidate and baseline profile versions must differ")
    if not actor.strip() or not model.strip():
        raise ValueError("Evaluation requires explicit actor and model identifiers")
    renderer = ResponseStyleAgent(runner=StructuredAgentRunner(model=gateway), model=model)
    bundle_hash = hashlib.sha256(bundle.model_dump_json().encode()).hexdigest()
    generated: list[dict[str, Any]] = []
    eval_cases: list[StyleEvalCase] = []
    for case in inputs:
        outputs: dict[str, Any] = {}
        for label, profile in (("baseline", SLIMGUARD_DEFAULT_V1), ("candidate", bundle.profile)):
            turn_id = "offline-style-" + uuid4().hex
            examples = tuple(
                example
                for example in bundle.examples
                if example.communication_act == case.response_plan.communication_act
                and example.style_profile_version == profile.version
            )[:5]
            invocation = AgentInvocation(
                invocation_id="offline-style-inv-" + uuid4().hex,
                trace_id=turn_id,
                turn_id=turn_id,
                graph_version="offline-style-eval-v1",
                agent_role=AgentRole.RESPONSE_STYLE,
                agent_version=RESPONSE_STYLE_PROMPT_VERSION,
                attempt=1,
                deadline_at=datetime.now(UTC) + timedelta(seconds=60),
                allowed_tools=(),
                privacy_scopes=("response_plan", "style_profile", "style_examples"),
                max_model_calls=2,
                max_tool_calls=0,
                max_total_tokens=8192,
            )
            outcome = await renderer.run(
                invocation=invocation,
                context=StyleContext(
                    turn_id=turn_id,
                    response_plan=case.response_plan,
                    profile=profile,
                    examples=examples,
                ),
            )
            outputs[label] = {
                "response": outcome.response.model_dump(mode="json"),
                "used_example_ids": [example.example_id for example in examples],
                "generation_status": "degraded" if outcome.used_fallback else "succeeded",
                "model_call_count": outcome.model_call_count,
                "total_token_count": outcome.total_token_count,
                "failure_code": outcome.failure_code,
            }
            if label == "candidate":
                eval_cases.append(
                    StyleEvalCase(
                        case_id=case.case_id,
                        scenario=case.scenario,
                        response_plan=case.response_plan,
                        styled_response=outcome.response,
                        generation_status="degraded" if outcome.used_fallback else "succeeded",
                    )
                )
        generated.append(
            {
                "case_id": case.case_id,
                "synthetic": case.synthetic,
                "scenario": case.scenario.model_dump(mode="json"),
                "response_plan": case.response_plan.model_dump(mode="json"),
                **outputs,
            }
        )
    report = await corpus.evaluate(bundle, eval_cases, gateway=gateway, model=model, actor=actor)
    comparable_count = sum(
        item["baseline"]["generation_status"] == "succeeded"
        and item["candidate"]["generation_status"] == "succeeded"
        for item in generated
    )
    return {
        "schema_version": "1",
        "bundle_sha256": bundle_hash,
        "candidate_profile_version": bundle.profile.version,
        "baseline_profile_version": SLIMGUARD_DEFAULT_V1.version,
        "generation_model": model,
        "generated_cases_sha256": hashlib.sha256(
            json.dumps(
                generated,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "comparison_complete": comparable_count == len(generated),
        "comparable_case_count": comparable_count,
        "cases": generated,
        "evaluation": report.model_dump(mode="json"),
        "human_review_status": "pending",
        "published": False,
    }
