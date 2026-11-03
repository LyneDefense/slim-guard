"""Direct coach expression; business orchestration belongs to Core."""

from slim_guard.agents.coach.agent import CoachResponder
from slim_guard.agents.coach.contracts import CoachEvidence, CoachReply
from slim_guard.agents.coach.prompt import COACH_PROMPT_VERSION, COACH_REVIEW_VERSION

__all__ = [
    "CoachResponder",
    "CoachEvidence",
    "CoachReply",
    "COACH_PROMPT_VERSION",
    "COACH_REVIEW_VERSION",
]
