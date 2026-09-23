"""Core execution results shared by invocation and Turn lifecycle owners."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slim_guard.harness.loop import HarnessLoopResult
    from slim_guard.runtime.contracts import AgentInvocation


@dataclass(frozen=True, slots=True)
class CoreExecutionResult:
    invocation: AgentInvocation
    loop: HarnessLoopResult


class CoreExecutionCancelled(asyncio.CancelledError):
    """Carry completed Core work to the Turn owner while preserving cancellation."""

    def __init__(self, result: HarnessLoopResult):
        super().__init__()
        self.result = result


class CoreExecutionFailed(Exception):
    """Internal envelope; the Turn boundary re-raises the original exception."""

    def __init__(self, result: HarnessLoopResult, error: Exception):
        super().__init__(type(error).__name__)
        self.result = result
        self.error = error
