class MemoryError(Exception):
    """Base class for controlled user-memory failures."""


class MemorySourceMismatch(MemoryError):
    pass


class MemoryEvidenceMismatch(MemoryError):
    pass


class MemoryCollision(MemoryError):
    pass


class MemoryNotFound(MemoryError):
    pass
