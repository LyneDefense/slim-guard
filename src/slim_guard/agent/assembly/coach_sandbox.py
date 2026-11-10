"""Read-only coach dialogue using the same generation/review policy as live replies."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.coach import (
    COACH_PROMPT_VERSION,
    COACH_REVIEW_VERSION,
    CoachEvidence,
    CoachReply,
    CoachResponder,
    respond,
)
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.dialogue import DialogueMessage, validate_dialogue
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.runtime.contracts import AgentInvocation, AgentRole
from slim_guard.runtime.invocation import InvocationRunner, InvocationRunResult


class PreviewTurn:
    def __init__(
        self,
        coach: CoachResponder,
        evidence: CoachEvidence,
        style: StyleProfileSnapshot,
        seconds: int,
    ) -> None:
        self.coach, self.evidence, self.style = coach, evidence, style
        self.deadline = datetime.now(UTC) + timedelta(seconds=seconds)
        self.turn_id, self.trace_id = str(uuid4()), str(uuid4())

    def invocation(self, role: AgentRole, version: str, attempt: int) -> AgentInvocation:
        return AgentInvocation(
            invocation_id=str(uuid4()),
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            agent_role=role,
            agent_version=version,
            graph_version="coach-preview-v1",
            deadline_at=self.deadline,
            attempt=attempt,
            caller="style_preview",
            privacy_scopes=("current_user_input", "working_memory", "style_profile"),
        )

    async def generate(
        self, *, attempt: int, feedback: tuple[dict[str, object], ...], previous_reply: str | None
    ) -> InvocationRunResult[CoachReply]:
        return await self.coach.generate(
            invocation=self.invocation(AgentRole.COACH_RESPONSE, COACH_PROMPT_VERSION, attempt),
            evidence=self.evidence,
            style=self.style,
            feedback=feedback,
            previous_reply=previous_reply,
        )

    async def review(self, *, attempt: int, reply: CoachReply) -> InvocationRunResult[ReplyCheck]:
        return await self.coach.review(
            invocation=self.invocation(AgentRole.COACH_REVIEW, COACH_REVIEW_VERSION, attempt),
            evidence=self.evidence,
            style=self.style,
            reply=reply,
        )


class CoachSandbox:
    """No real user identity, tools, memory lookup, conversation store, or write port."""

    def __init__(self, gateway: ModelGateway, model: str, seconds: int) -> None:
        self.coach = CoachResponder(runner=InvocationRunner(model=gateway), model=model)
        self.seconds = seconds

    async def reply(
        self,
        *,
        style: StyleProfileSnapshot,
        user_input: str,
        history: tuple[DialogueMessage, ...],
    ) -> dict[str, Any]:
        validate_dialogue(history)
        messages = [m.model_dump(mode="json") for m in history]
        refs = []
        for index, message in enumerate(messages):
            if message["role"] == "user":
                message["evidence_ref"] = f"preview-history-{index}"
                refs.append(message["evidence_ref"])
        evidence = CoachEvidence(
            context={
                "current_user_input": {"text": user_input, "attachments": []},
                "recent_dialogue": [{"messages": messages}],
                "authoritative_facts": {},
                "turn_trigger": "user_message",
                "synthetic_preview": True,
            },
            allowed_source_refs=("preview-current-user", *refs),
        )
        result = await respond(PreviewTurn(self.coach, evidence, style, self.seconds))
        return {
            "text": result.reply.text if result.reply else "",
            "passed": result.reply is not None,
            "failure_code": result.failure_code,
            "checks": [
                {**check, "adopted": check["attempt"] == result.adopted_attempt}
                for check in result.checks
            ],
            "style_advisory": result.style_advisory,
            "model_calls": result.model_call_count,
            "tokens": result.total_token_count,
            "repair_attempted": result.repair_attempted,
        }
