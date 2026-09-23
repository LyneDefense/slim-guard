"""Canonical Turn Harness API.

The implementation remains in ``harness.runner`` during the staged migration.
Only this module should be used by production composition code.
"""

from slim_guard.harness.preparation import TurnGrants
from slim_guard.harness.runner import TurnHarness, TurnRunResult

__all__ = ["TurnGrants", "TurnHarness", "TurnRunResult"]
