from __future__ import annotations


class HarnessStateError(RuntimeError):
    """Base class for persistent harness state errors."""


class InvalidTurnTransition(HarnessStateError):
    pass


class TurnStateConflict(HarnessStateError):
    pass


class TurnNotWritable(HarnessStateError):
    pass
