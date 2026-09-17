"""Compatibility imports for the pre-refactor structured runner names.

Production code should import the canonical API from ``runtime.invocation``.
This module is removed in migration Phase 8.
"""

from slim_guard.runtime.invocation.runner import (
    InvocationCallBudget,
    InvocationRunner,
    InvocationRunResult,
    invocation_call_budget,
)

StructuredAgentRunner = InvocationRunner
StructuredRunResult = InvocationRunResult
WorkflowCallBudget = InvocationCallBudget
workflow_call_budget = invocation_call_budget

__all__ = [
    "StructuredAgentRunner",
    "StructuredRunResult",
    "WorkflowCallBudget",
    "workflow_call_budget",
]
