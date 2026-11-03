"""Core Agent boundary for the user-facing model and tool loop."""

from slim_guard.agents.core.agent import CoreAgent
from slim_guard.agents.core.contracts import CoreCompletion, OperationQuestion

__all__ = [
    "CoreAgent",
    "CoreCompletion",
    "OperationQuestion",
]
