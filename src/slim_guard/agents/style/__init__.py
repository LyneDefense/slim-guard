"""Public response-style agent API."""

from slim_guard.agents.style.agent import (
    RESPONSE_STYLE_PROMPT,
    RESPONSE_STYLE_PROMPT_VERSION,
    ResponseStyleAgent,
    StyleAgentResult,
)
from slim_guard.agents.style.context import StyleContextCompiler
from slim_guard.agents.style.contracts import (
    SLIMGUARD_DEFAULT_V1,
    StyleContext,
    StyleExample,
    StyleProfile,
    StyleProfileRepository,
)
from slim_guard.agents.style.renderer import NeutralRenderer
from slim_guard.agents.style.validation import (
    StyleIntegrityError,
    StyleIntegrityIssue,
    StyleIntegrityIssueCode,
    StyleResponseValidator,
    StyleValidationReport,
)

__all__ = [
    "NeutralRenderer",
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
