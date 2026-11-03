"""Mechanical reference/quotation integrity; semantic judgments remain with the model."""

from typing import Any

from slim_guard.agents.coach.contracts import CoachEvidence, CoachReply
from slim_guard.expression_style.contracts import StyleProfileSnapshot
from slim_guard.expression_style.review.contracts import ReplyCheck
from slim_guard.group_chat.role_policy import COACH_ROLE_POLICY


def reply_errors(reply: CoachReply, evidence: CoachEvidence) -> list[dict[str, object]]:
    if set(reply.source_refs) <= set(evidence.allowed_source_refs):
        return []
    return [
        {
            "path": ["source_refs"],
            "code": "unavailable_reference",
            "message": "Use only allowed_source_refs from the frozen evidence",
        }
    ]


def review_errors(
    check: ReplyCheck, evidence: CoachEvidence, style: StyleProfileSnapshot, reply: CoachReply
) -> list[dict[str, object]]:
    facts = "\n".join(_texts(evidence.model_dump(mode="json")))
    style_text = "\n".join(_texts(style.model_dump(mode="json")))
    errors: list[dict[str, object]] = []
    for index, issue in enumerate(check.issues):
        source = style_text if issue.dimension == "expression" else facts
        if issue.dimension == "role":
            source += COACH_ROLE_POLICY
        if issue.output_excerpt and issue.output_excerpt not in reply.text:
            errors.append(
                {
                    "path": ["issues", index, "output_excerpt"],
                    "code": "quote_not_in_reply",
                    "message": "Quote the actual reply verbatim; do not invent an output excerpt",
                }
            )
        if issue.source_excerpt and issue.source_excerpt not in source:
            errors.append(
                {
                    "path": ["issues", index, "source_excerpt"],
                    "code": "quote_not_in_source",
                    "message": "Quote evidence verbatim, or leave empty when evidence is missing",
                }
            )
    return errors


def _texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, (tuple, list)):
        return [text for item in value for text in _texts(item)]
    return [str(value)] if value is not None else []
