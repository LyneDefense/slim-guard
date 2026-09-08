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
from slim_guard.style_corpus import OfflineStyleCorpus, StyleAssetBundle, StyleEvalCase


class StyleEvaluationInput(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    response_plan: ResponsePlan
    synthetic: bool = True


def synthetic_style_suite() -> tuple[StyleEvaluationInput, ...]:
    """Public invented business scenarios, never copied from the source chat."""
    scenarios: tuple[tuple[CommunicationAct, str, str], ...] = (
        (CommunicationAct.ACKNOWLEDGE, "已记录今天的体重 72.4kg。", "收到这次记录。"),
        (CommunicationAct.CORRECT, "这张图片里的份量还不能确认。", "请补充确认后再继续。"),
        (CommunicationAct.REMIND, "今天的记录尚未提交。", "方便时请补充今天的记录。"),
        (CommunicationAct.ENCOURAGE, "你已经连续记录了 7 天。", "肯定用户的坚持，鼓励继续记录。"),
        (CommunicationAct.EXPLAIN, "现有记录不足以判断长期趋势。", "解释目前不能直接得出结论。"),
        (CommunicationAct.ASK, "目前还不知道这次记录的日期。", "请问这次记录是哪一天的？"),
    )
    cases: list[StyleEvaluationInput] = []
    for act, fact, social in scenarios:
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
                        response_plan=case.response_plan,
                        styled_response=outcome.response,
                        generation_status="degraded" if outcome.used_fallback else "succeeded",
                    )
                )
        generated.append(
            {
                "case_id": case.case_id,
                "synthetic": case.synthetic,
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
