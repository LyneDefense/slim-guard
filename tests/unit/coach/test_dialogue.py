"""The online and preview paths share this decision policy, not a word counter."""

import pytest

from slim_guard.agents.coach.contracts import CoachReply
from slim_guard.agents.coach.dialogue import respond
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.runtime.contracts import InvocationStatus
from slim_guard.runtime.invocation import InvocationRunResult


def result(value=None, failure=None):
    return InvocationRunResult(
        status=InvocationStatus.SUCCEEDED if value else InvocationStatus.FAILED,
        output=value,
        responses=(),
        model_call_count=1,
        total_token_count=10,
        failure_code=failure,
    )


def check(dimension=None, severity="repairable"):
    return ReplyCheck.model_validate(
        {
            "fidelity_passed": dimension != "fidelity",
            "role_passed": dimension != "role",
            "expression_passed": dimension != "expression",
            "issues": [
                {
                    "dimension": dimension,
                    "severity": severity,
                    "code": "synthetic_issue",
                    "explanation": "具体语义差异",
                    "source_excerpt": "",
                    "output_excerpt": "",
                    "repair_requirement": "根据原始上下文去掉无依据内容",
                }
            ]
            if dimension
            else [],
        }
    )


class Turn:
    def __init__(self, generations, reviews):
        self.generations, self.reviews = list(generations), list(reviews)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.generations.pop(0)

    async def review(self, **kwargs):
        return self.reviews.pop(0)


@pytest.mark.parametrize("text", ["你好。", "解释" * 800])
async def test_semantically_valid_answers_are_not_truncated_or_forced_to_two_sentences(text):
    outcome = await respond(Turn([result(CoachReply(text=text))], [result(check())]))
    assert outcome.reply.text == text and not outcome.style_advisory
    assert outcome.model_call_count == 2


@pytest.mark.parametrize("dimension", ["role", "fidelity"])
async def test_fact_and_role_failures_never_become_expression_advisories(dimension):
    turn = Turn([result(CoachReply(text="坏回答"))] * 2, [result(check(dimension))] * 2)
    outcome = await respond(turn)
    assert outcome.reply is None and outcome.failure_code
    assert not outcome.style_advisory and outcome.repair_attempted
    assert turn.calls[1]["previous_reply"] == "坏回答"
    assert turn.calls[1]["feedback"][0]["repair_requirement"]


@pytest.mark.parametrize("severity", ["repairable", "blocking"])
async def test_style_only_disagreement_adopts_reviewed_reply_without_more_calls(severity):
    turn = Turn(
        [result(CoachReply(text="有依据但风格不够好"))],
        [result(check("expression", severity))],
    )
    outcome = await respond(turn)
    assert outcome.reply.text == "有依据但风格不够好"
    assert outcome.failure_code is None and outcome.style_advisory
    assert outcome.model_call_count == 2 and outcome.total_token_count == 20
    assert not outcome.repair_attempted and outcome.adopted_attempt == 1
    assert len(turn.calls) == 1 and len(outcome.checks) == 1


@pytest.mark.parametrize("dimension", ["fidelity", "role"])
@pytest.mark.parametrize("expression_passes", [True, False])
async def test_safe_repaired_reply_is_adopted_even_with_style_advisory(
    dimension, expression_passes
):
    turn = Turn(
        [result(CoachReply(text="无依据或越界的回答")), result(CoachReply(text="有依据的回答"))],
        [result(check(dimension)), result(check(None if expression_passes else "expression"))],
    )
    outcome = await respond(turn)
    assert outcome.reply.text == "有依据的回答"
    assert outcome.style_advisory is not expression_passes
    assert outcome.repair_attempted and outcome.adopted_attempt == 2
    assert outcome.model_call_count == 4 and len(turn.calls) == 2
    assert turn.calls[1]["previous_reply"] == "无依据或越界的回答"
    assert turn.calls[1]["feedback"][0]["dimension"] == dimension


@pytest.mark.parametrize("second", ["generation_failure", "review_failure", "unsafe"])
async def test_failed_safety_repair_never_delivers_an_unreviewed_or_rejected_reply(second):
    generations = [
        result(CoachReply(text="事实审查未通过")),
        result(None, "model_gateway_error")
        if second == "generation_failure"
        else result(CoachReply(text="未通过的新回答")),
    ]
    reviews = [result(check("fidelity"))]
    if second != "generation_failure":
        reviews.append(
            result(None, "structured_output_invalid")
            if second == "review_failure"
            else result(check("fidelity", "blocking"))
        )
    outcome = await respond(Turn(generations, reviews))
    assert outcome.reply is None and outcome.failure_code
    assert not outcome.style_advisory and outcome.adopted_attempt is None
    assert outcome.repair_attempted
    assert outcome.model_call_count == (3 if second == "generation_failure" else 4)


@pytest.mark.parametrize("dimension", ["fidelity", "role"])
async def test_safety_failure_with_style_failure_still_requires_repair(dimension):
    verdict = check(dimension).model_dump()
    verdict["expression_passed"] = False
    verdict["issues"] += [issue.model_dump() for issue in check("expression").issues]
    turn = Turn(
        [result(CoachReply(text="仍有安全问题"))] * 2,
        [result(ReplyCheck.model_validate(verdict))] * 2,
    )
    outcome = await respond(turn)
    assert outcome.reply is None and not outcome.style_advisory
    assert outcome.repair_attempted and outcome.model_call_count == 4


async def test_broken_reviewer_cannot_justify_delivering_unreviewed_text():
    outcome = await respond(
        Turn([result(CoachReply(text="未审查"))], [result(None, "structured_output_invalid")])
    )
    assert outcome.reply is None and outcome.failure_code == "structured_output_invalid"


@pytest.mark.parametrize("dimension", ["fidelity", "role"])
async def test_blocking_unsafe_answer_is_not_retried_or_delivered(dimension):
    turn = Turn([result(CoachReply(text="危险建议"))], [result(check(dimension, "blocking"))])
    outcome = await respond(turn)
    assert outcome.reply is None and not outcome.repair_attempted
    assert len(turn.calls) == 1
