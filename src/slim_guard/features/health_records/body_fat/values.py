"""Canonical body-fat conversion shared by creates and corrections."""

from decimal import ROUND_HALF_UP, Decimal


def basis_points(value: Decimal) -> int:
    if not value.is_finite():
        raise ValueError("A finite body-fat percentage is required")
    result = int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not 100 <= result <= 7500:
        raise ValueError("Body-fat percentage must be between 1% and 75%")
    return result
