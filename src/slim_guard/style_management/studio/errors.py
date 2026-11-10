"""Conflicts that require reloading a draft, distinct from ordinary validation."""


class DraftConflictError(ValueError):
    """The saved draft changed after the caller read it."""
