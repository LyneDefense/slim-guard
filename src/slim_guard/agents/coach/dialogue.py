"""One generate/review/repair policy shared by live turns and offline previews."""

from dataclasses import dataclass
from typing import Protocol

from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.runtime.invocation import InvocationRunResult

from .contracts import CoachReply

DIALOGUE_POLICY_VERSION = "coach-dialogue-v1"


class CoachTurnPort(Protocol):
    async def generate(
        self, *, attempt: int, feedback: tuple[dict[str, object], ...], previous_reply: str | None
    ) -> InvocationRunResult[CoachReply]: ...

    async def review(
        self, *, attempt: int, reply: CoachReply
    ) -> InvocationRunResult[ReplyCheck]: ...


@dataclass(frozen=True)
class CoachDialogueResult:
    reply: CoachReply | None
    checks: tuple[dict[str, object], ...]
    model_call_count: int
    total_token_count: int
    failure_code: str | None
    repair_attempted: bool
    style_advisory: bool = False
    adopted_attempt: int | None = None


async def respond(
    turn: CoachTurnPort, *, feedback: tuple[dict[str, object], ...] = ()
) -> CoachDialogueResult:
    previous = None
    safe_reply = None
    safe_attempt = None
    checks: list[dict[str, object]] = []
    calls = tokens = 0
    failure = "coach_generation_failed"
    for attempt in (1, 2):
        generated = await turn.generate(attempt=attempt, feedback=feedback, previous_reply=previous)
        calls += generated.model_call_count
        tokens += generated.total_token_count
        if generated.output is None:
            failure = generated.failure_code or "coach_generation_failed"
            break
        previous = generated.output.text
        reviewed = await turn.review(attempt=attempt, reply=generated.output)
        calls += reviewed.model_call_count
        tokens += reviewed.total_token_count
        check = reviewed.output
        grounded = bool(check and check.fidelity_passed and check.role_passed)
        checks.append(
            {
                "attempt": attempt,
                "passed": grounded,
                "output": previous,
                "issues": [i.code for i in check.issues]
                if check
                else [reviewed.failure_code or "coach_review_failed"],
                "details": [i.model_dump(mode="json") for i in check.issues] if check else [],
                "expression_passed": check.expression_passed if check else False,
                "review": check.model_dump(mode="json") if check else None,
            }
        )
        if grounded:
            safe_reply = generated.output
            safe_attempt = attempt
            if check is not None and check.expression_passed:
                return CoachDialogueResult(
                    safe_reply,
                    tuple(checks),
                    calls,
                    tokens,
                    None,
                    attempt == 2,
                    adopted_attempt=attempt,
                )
        failure = reviewed.failure_code or "coach_grounding_rejected"
        if check is None:
            break
        feedback = tuple(i.model_dump(mode="json") for i in check.issues)
        if any(i.severity == "blocking" and i.dimension != "expression" for i in check.issues):
            break
    # A style-only disagreement cannot erase a previously safety/role-reviewed
    # answer. Never use an unreviewed or factually rejected candidate as fallback.
    return CoachDialogueResult(
        safe_reply,
        tuple(checks),
        calls,
        tokens,
        None if safe_reply else failure,
        attempt == 2,
        style_advisory=safe_reply is not None,
        adopted_attempt=safe_attempt,
    )
