class WeightDomainError(RuntimeError):
    """Base class for authoritative weight-domain failures."""


class WeightRecordCollision(WeightDomainError):
    pass


class WeightSourceMismatch(WeightDomainError):
    pass
