class MealDomainError(RuntimeError):
    """Base class for authoritative meal-domain failures."""


class MealRecordCollision(MealDomainError):
    pass


class MealSourceMismatch(MealDomainError):
    pass
