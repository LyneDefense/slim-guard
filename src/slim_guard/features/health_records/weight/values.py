"""Canonical weight conversion shared by creates and corrections."""

from decimal import ROUND_HALF_UP, Decimal

_GRAMS_PER_UNIT = {"kg": Decimal("1000"), "jin": Decimal("500"), "lb": Decimal("453.59237")}


def weight_grams(value: Decimal, unit: str) -> int:
    if not value.is_finite() or unit not in _GRAMS_PER_UNIT:
        raise ValueError("A finite weight and a supported unit are required")
    grams = int((value * _GRAMS_PER_UNIT[unit]).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not 10_000 <= grams <= 500_000:
        raise ValueError("Weight measurement must be between 10kg and 500kg")
    return grams
