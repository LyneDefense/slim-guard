"""Single shared review entry point; no training, tools, or persistence access."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from slim_guard.agent_models.gateway import ModelMessage, ModelPurpose, ModelRequest
from slim_guard.agents.contracts import AgentInvocation, StyledResponse
from slim_guard.expression_style.review.contracts import ReplyCheck, RewriteReviewResult
from slim_guard.expression_style.review.integrity import StyleResponseValidator
from slim_guard.expression_style.review.policy import REVIEW_PROMPT, review_signature
from slim_guard.runtime.invocation import InvocationGrant, InvocationRunner

if TYPE_CHECKING:
    from slim_guard.expression_style.contracts import StyleContext


class StyleReviewService:
    def __init__(
        self,
        runner: InvocationRunner,
        model: str,
        validator: StyleResponseValidator | None = None,
    ) -> None:
        self.runner = runner
        self.model = model
        self.validator = validator or StyleResponseValidator()
        self.signature = review_signature(model)

    async def review(
        self,
        *,
        invocation: AgentInvocation,
        context: StyleContext,
        response: StyledResponse,
        source_text: str,
        remaining_tokens: int,
        grant: InvocationGrant | None = None,
    ) -> RewriteReviewResult:
        integrity = self.validator.validate(context, response)
        if not integrity.is_valid:
            return RewriteReviewResult(
                False,
                integrity.issue_codes,
                "style_integrity_invalid",
                integrity=integrity,
                verdict="needs_repair",
                signature=self.signature,
                checks=(
                    {"name": "integrity", "status": "failed"},
                    {"name": "fidelity", "status": "not_run"},
                    {"name": "expression", "status": "not_run"},
                ),
            )
        if remaining_tokens <= 0:
            return RewriteReviewResult(
                False,
                ("review_budget_exhausted",),
                "review_unavailable",
                signature=self.signature,
                checks=(
                    {"name": "integrity", "status": "passed"},
                    {"name": "fidelity", "status": "not_run"},
                    {"name": "expression", "status": "not_run"},
                ),
            )
        request = ModelRequest(
            purpose=ModelPurpose.RESPONSE_STYLE,
            model=self.model,
            messages=(
                ModelMessage(
                    role="system",
                    content=REVIEW_PROMPT,
                ),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "original": source_text,
                            "rewritten": response.text,
                            "user_input": context.user_input,
                            "minimal_context": context.minimal_context,
                            "content_contract": context.response_plan.model_dump(mode="json"),
                            "style": json.loads(context.compiled_prompt)
                            if context.compiled_prompt
                            else context.profile.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ),
            tool_choice="none",
            response_format="json_object",
            output_schema_name="ReplyCheck",
            max_output_tokens=min(2048, remaining_tokens),
            temperature=0,
        )
        check = await self.runner.run(
            invocation=invocation.model_copy(
                update={"max_model_calls": 1, "max_total_tokens": remaining_tokens}
            ),
            request=request,
            output_type=ReplyCheck,
            grant=grant,
        )
        decision = check.output
        if decision is not None and any(
            i.source_excerpt not in source_text or i.output_excerpt not in response.text
            for i in decision.issues
        ):
            decision = None
        passed = bool(decision and decision.fidelity_passed and decision.expression_passed)
        verdict = "passed" if passed else "needs_repair"
        if decision is None:
            verdict = "inconclusive"
        elif any(i.severity == "blocking" for i in decision.issues):
            verdict = "blocked"
        return RewriteReviewResult(
            passed=passed,
            issues=tuple(
                f"{i.explanation}；修复：{i.repair_requirement}"
                for i in decision.issues
                if i.severity != "warning"
            )
            if decision
            else (check.failure_code or "review_result_invalid",),
            failure_code=None if passed else "style_review_failed",
            responses=check.responses,
            total_token_count=check.total_token_count,
            integrity=integrity,
            verdict=verdict,
            details=tuple(i.model_dump() for i in decision.issues) if decision else (),
            signature=self.signature,
            checks=(
                {"name": "integrity", "status": "passed"},
                {
                    "name": "fidelity",
                    "status": ("passed" if decision.fidelity_passed else "failed")
                    if decision
                    else "error",
                },
                {
                    "name": "expression",
                    "status": ("passed" if decision.expression_passed else "failed")
                    if decision
                    else "error",
                },
            ),
        )
