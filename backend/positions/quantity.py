"""Position quantity validation and 8dp floor (Kraken paper CLI precision)."""

import math
from typing import Union

_MIN_VIABLE_QTY = 1e-8


def floor_qty_8dp(quantity: float) -> float:
    """Floor quantity to 8 decimal places (matches Kraken CLI paper account)."""
    if not math.isfinite(quantity):
        return 0.0
    return math.floor(quantity * 1e8) / 1e8


def is_valid_position_quantity(quantity: Union[float, int, None]) -> bool:
    """True if quantity is finite and sellable after 8dp floor."""
    if quantity is None:
        return False
    try:
        q = float(quantity)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(q):
        return False
    return floor_qty_8dp(q) > _MIN_VIABLE_QTY
