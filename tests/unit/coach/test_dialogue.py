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
async def test_style_only_disagreement_can_repair_but_cannot_hide_reviewed_doctor(severity):
    turn = Turn(
        [result(CoachReply(text="有依据但风格不够好"))] * 2,
        [result(check("expression", severity))] * 2,
    )
    outcome = await respond(turn)
    assert outcome.reply.text == "有依据但风格不够好"
    assert outcome.failure_code is None and outcome.style_advisory
    assert outcome.model_call_count == 4


async def test_style_repair_can_be_adopted_without_rewriting_a_neutral_draft():
    turn = Turn(
        [result(CoachReply(text="铺垫")), result(CoachReply(text="直接回答"))],
        [result(check("expression")), result(check())],
    )
    outcome = await respond(turn)
    assert outcome.reply.text == "直接回答" and not outcome.style_advisory


@pytest.mark.parametrize("second", ["generation_failure", "review_failure", "unsafe"])
async def test_only_previously_reviewed_safe_reply_can_be_preserved(second):
    safe = result(CoachReply(text="已通过事实和职责审查"))
    generations = [
        safe,
        result(None, "model_gateway_error")
        if second == "generation_failure"
        else result(CoachReply(text="未通过的新回答")),
    ]
    reviews = [result(check("expression"))]
    if second != "generation_failure":
        reviews.append(
            result(None, "structured_output_invalid")
            if second == "review_failure"
            else result(check("fidelity", "blocking"))
        )
    outcome = await respond(Turn(generations, reviews))
    assert outcome.reply.text == safe.output.text and outcome.style_advisory


async def test_broken_reviewer_cannot_justify_delivering_unreviewed_text():
    outcome = await respond(
        Turn([result(CoachReply(text="未审查"))], [result(None, "structured_output_invalid")])
    )
    assert outcome.reply is None and outcome.failure_code == "structured_output_invalid"


async def test_blocking_unsafe_answer_is_not_retried_or_delivered():
    turn = Turn([result(CoachReply(text="危险建议"))], [result(check("fidelity", "blocking"))])
    outcome = await respond(turn)
    assert outcome.reply is None and not outcome.repair_attempted
    assert len(turn.calls) == 1
