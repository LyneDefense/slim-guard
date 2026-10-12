class ExerciseDomainError(RuntimeError):
    """Base class for authoritative exercise-domain failures."""


class ExerciseRecordCollision(ExerciseDomainError):
    pass


class ExerciseSourceMismatch(ExerciseDomainError):
    pass
