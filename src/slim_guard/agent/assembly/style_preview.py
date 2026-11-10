"""Wire offline previews to the same doctor capability, without business tools or writes."""

from typing import Any

from slim_guard.agent.assembly.coach_sandbox import PreviewTurn
from slim_guard.agent_models.gateway import ModelGateway
from slim_guard.agents.coach import (
    CoachEvidence,
    CoachResponder,
    respond,
)
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.dialogue import DialogueMessage
from slim_guard.expression_style.trainer.contracts import TestCase
from slim_guard.expression_style.trainer.ports import CoachPreviewPort
from slim_guard.runtime.invocation import InvocationRunner


class DirectCoachPreview:
    def __init__(self, gateway: ModelGateway, model: str, request_seconds: int) -> None:
        self.coach = CoachResponder(runner=InvocationRunner(model=gateway), model=model)
        self.seconds = request_seconds
        self.signature = self.coach.signature

    async def run(
        self,
        *,
        case: TestCase,
        style: StyleProfileSnapshot,
        feedback: tuple[dict[str, object], ...] = (),
    ) -> dict[str, Any]:
        history = list(case.history)
        transcript: list[dict[str, Any]] = []
        checks: list[dict[str, object]] = []
        calls = tokens = repairs = 0
        failure = None
        advisory = False
        for step, user_input in enumerate((case.user_input, *case.follow_up_inputs), 1):
            evidence = self._evidence(case, history, user_input)
            result = await respond(
                PreviewTurn(self.coach, evidence, style, self.seconds),
                feedback=feedback if step == 1 else (),
            )
            calls += result.model_call_count
            tokens += result.total_token_count
            repairs += int(result.repair_attempted)
            checks.extend(
                {**check, "step": step, "adopted": check["attempt"] == result.adopted_attempt}
                for check in result.checks
            )
            advisory |= result.style_advisory
            transcript.append({"role": "user", "content": user_input})
            if result.reply is None:
                failure = result.failure_code
                break
            transcript.append({"role": "assistant", "content": result.reply.text})
            # Roll out actual generated answers, not ideal answers from the corpus.
            history.extend(
                (
                    DialogueMessage(role="user", content=user_input),
                    DialogueMessage(role="assistant", content=result.reply.text),
                )
            )
        return {
            "text": transcript[-1]["content"] if transcript[-1]["role"] == "assistant" else "",
            "passed": failure is None,
            "fallback": failure is not None,
            "failure_code": failure,
            "checks": checks,
            "tokens": tokens,
            "model_calls": calls,
            "dialogue": transcript,
            "history": [
                m.model_dump(mode="json") for m in (history[:-2] if failure is None else history)
            ],
            "repairs": repairs,
            "style_advisory": advisory,
            "mode": "direct_generation",
        }

    @staticmethod
    def _evidence(case: TestCase, history: list[DialogueMessage], user_input: str) -> CoachEvidence:
        messages = [m.model_dump(mode="json") for m in history]
        refs = []
        for index, message in enumerate(messages):
            if message["role"] == "user":
                message["evidence_ref"] = f"preview-history-{index}"
                refs.append(message["evidence_ref"])
        return CoachEvidence(
            context={
                "current_user_input": {"text": user_input, "attachments": []},
                "recent_dialogue": [{"messages": messages}],
                "authoritative_facts": {"preview_facts": case.context},
                "turn_trigger": "user_message",
                "synthetic_preview": True,
            },
            allowed_source_refs=("preview-current-user", "preview-facts", *refs),
        )


def build_coach_preview(
    gateway: ModelGateway, model: str, request_seconds: int
) -> CoachPreviewPort:
    return DirectCoachPreview(gateway, model, request_seconds)
