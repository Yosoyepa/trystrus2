"""Money is a fixed 2-decimal string everywhere it is signed or stored (M7)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def fmt(value) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def dec(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
