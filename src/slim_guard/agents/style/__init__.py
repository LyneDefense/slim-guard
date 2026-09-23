"""Public response-style agent API."""

from slim_guard.agents.style.agent import (
    RESPONSE_STYLE_PROMPT,
    RESPONSE_STYLE_PROMPT_VERSION,
    ResponseStyleAgent,
    StyleAgentResult,
)
from slim_guard.agents.style.context import StyleContextCompiler
from slim_guard.agents.style.renderer import NeutralRenderer
from slim_guard.expression_style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleContext,
    StyleExample,
    StyleProfile,
    StyleProfileRepository,
)
from slim_guard.expression_style.review.integrity import (
    StyleIntegrityError,
    StyleIntegrityIssue,
    StyleIntegrityIssueCode,
    StyleResponseValidator,
    StyleValidationReport,
)

# Online style rendering is a capability of the Conversation Agent rather
# than a business-decision agent. Keep the implementation name for persisted
# manifests while exposing the narrower architectural role.
CoachStyleRenderer = ResponseStyleAgent

__all__ = [
    "NeutralRenderer",
    "CoachStyleRenderer",
    "RESPONSE_STYLE_PROMPT",
    "RESPONSE_STYLE_PROMPT_VERSION",
    "ResponseStyleAgent",
    "SLIMGUARD_DEFAULT_V1",
    "StyleAgentResult",
    "StyleContext",
    "StyleContextCompiler",
    "StyleExample",
    "StyleIntegrityError",
    "StyleIntegrityIssue",
    "StyleIntegrityIssueCode",
    "StyleProfile",
    "StyleProfileRepository",
    "StyleResponseValidator",
    "StyleValidationReport",
]
