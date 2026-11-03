"""Zero-tool direct expression and grounding review capabilities."""

import json
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from slim_guard.agent_models.gateway import (
    ModelMessage,
    ModelPurpose,
    ModelRequest,
    ResponseFormat,
    ToolChoice,
)
from slim_guard.agents.coach.contracts import CoachEvidence, CoachReply
from slim_guard.agents.coach.prompt import (
    COACH_PROMPT,
    COACH_PROMPT_VERSION,
    COACH_REVIEW_PROMPT,
    COACH_REVIEW_VERSION,
)
from slim_guard.agents.coach.validation import reply_errors, review_errors
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.runtime.contracts import AgentInvocation, AgentRole
from slim_guard.runtime.invocation import InvocationGrant, InvocationRunner, InvocationRunResult
from slim_guard.usage.context import usage_scope

Output = TypeVar("Output", bound=BaseModel)


class CoachResponder:
    """Generate in the chosen style on the first call; never rewrite a Core draft."""

    def __init__(self, *, runner: InvocationRunner, model: str) -> None:
        self._runner = runner
        self._model = model

    async def generate(
        self,
        *,
        invocation: AgentInvocation,
        evidence: CoachEvidence,
        style: StyleProfileSnapshot,
        feedback: tuple[dict[str, object], ...] = (),
        previous_reply: str | None = None,
    ) -> InvocationRunResult[CoachReply]:
        return await self._run(
            invocation,
            role=AgentRole.COACH_RESPONSE,
            purpose=ModelPurpose.COACH_RESPONSE,
            prompt=COACH_PROMPT,
            version=COACH_PROMPT_VERSION,
            schema=CoachReply,
            payload={
                "evidence": evidence.model_dump(mode="json"),
                "style": style.model_dump(mode="json"),
                "feedback": feedback,
                "previous_reply": previous_reply,
            },
            validate_output=lambda output: reply_errors(output, evidence),
        )

    async def review(
        self,
        *,
        invocation: AgentInvocation,
        evidence: CoachEvidence,
        style: StyleProfileSnapshot,
        reply: CoachReply,
    ) -> InvocationRunResult[ReplyCheck]:
        return await self._run(
            invocation,
            role=AgentRole.COACH_REVIEW,
            purpose=ModelPurpose.COACH_REVIEW,
            prompt=COACH_REVIEW_PROMPT,
            version=COACH_REVIEW_VERSION,
            schema=ReplyCheck,
            payload={
                "evidence": evidence.model_dump(mode="json"),
                "style": style.model_dump(mode="json"),
                "reply": reply.model_dump(mode="json"),
            },
            validate_output=lambda output: review_errors(output, evidence, style, reply),
        )

    async def _run(
        self,
        invocation: AgentInvocation,
        *,
        role: AgentRole,
        purpose: ModelPurpose,
        prompt: str,
        version: str,
        schema: type[Output],
        payload: dict[str, object],
        validate_output: Callable[[Output], list[dict[str, object]]],
    ) -> InvocationRunResult[Output]:
        if invocation.agent_role is not role or invocation.allowed_tools:
            raise ValueError("Coach capabilities have fixed roles and no business tools")
        request = ModelRequest(
            purpose=purpose,
            model=self._model,
            messages=(
                ModelMessage(role="system", content=prompt),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {**payload, "output_schema": schema.model_json_schema()}, ensure_ascii=False
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            response_format=ResponseFormat.JSON_OBJECT,
            output_schema_name=schema.__name__,
            max_output_tokens=1800,
            temperature=0,
            metadata={"invocation_id": invocation.invocation_id, "prompt_version": version},
        )
        with usage_scope(
            invocation_id=invocation.invocation_id,
            agent_role=role.value,
            parent_invocation_id=invocation.parent_invocation_id,
        ):
            return await self._runner.run(
                invocation=invocation,
                request=request,
                output_type=schema,
                validate_output=validate_output,
                grant=InvocationGrant(
                    agent_role=role, privacy_scopes=frozenset(invocation.privacy_scopes)
                ),
            )
