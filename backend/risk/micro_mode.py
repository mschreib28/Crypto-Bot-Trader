"""Micro-account mode for small account sizes.

When account equity is below a threshold (default: $250), micro mode:
- Enforces minimum stop distance (avoids tiny stops that cause constant stop-outs)
- Enforces minimum notional logic (skip trade or use fixed minimal size)
- Reduces frequency aggressively (max 1 position open total)
- Makes position sizing more conservative

This prevents issues where:
- Position sizing produces notional below Kraken minimums
- Fees dominate "R" (risk/reward)
- Tiny stops cause constant stop-outs
"""

import logging
import os
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

# Micro mode threshold (equity below this triggers micro mode)
MICRO_MODE_THRESHOLD: float = float(os.getenv("MICRO_MODE_THRESHOLD", "250.0"))

# Minimum stop distance in ATR multiples (micro mode)
MICRO_MODE_MIN_STOP_ATR: float = float(os.getenv("MICRO_MODE_MIN_STOP_ATR", "2.0"))

# Minimum notional size for micro mode (fixed minimal size)
MICRO_MODE_MIN_NOTIONAL: float = float(os.getenv("MICRO_MODE_MIN_NOTIONAL", "5.0"))

# Maximum positions in micro mode (aggressive reduction)
MICRO_MODE_MAX_POSITIONS: int = int(os.getenv("MICRO_MODE_MAX_POSITIONS", "1"))

# Live slots thresholds (for limiting concurrent live positions)
LIVE_SLOTS_THRESHOLD_1: float = float(os.getenv("LIVE_SLOTS_THRESHOLD_1", "50.0"))
LIVE_SLOTS_THRESHOLD_2: float = float(os.getenv("LIVE_SLOTS_THRESHOLD_2", "100.0"))


def is_micro_mode(equity: float) -> bool:
    """
    Check if account is in micro mode.
    
    Args:
        equity: Current account equity in USD
        
    Returns:
        True if equity < threshold, False otherwise
    """
    return equity < MICRO_MODE_THRESHOLD


def check_min_stop_distance(
    entry_price: float,
    stop_loss_price: float,
    atr: Optional[float] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check minimum stop distance for micro mode.
    
    In micro mode, stops must be at least MICRO_MODE_MIN_STOP_ATR * ATR away
    to avoid constant stop-outs from fees and spread.
    
    Args:
        entry_price: Entry price
        stop_loss_price: Stop loss price
        atr: ATR value (optional, if None uses percentage-based check)
        
    Returns:
        Tuple of (is_valid, reason)
    """
    stop_distance = abs(entry_price - stop_loss_price)
    
    if atr and atr > 0:
        stop_distance_atr = stop_distance / atr
        if stop_distance_atr < MICRO_MODE_MIN_STOP_ATR:
            return (
                False,
                f"stop_too_close: {stop_distance_atr:.2f}ATR < {MICRO_MODE_MIN_STOP_ATR}ATR minimum"
            )
    else:
        # Fallback: use percentage-based check (5% minimum)
        stop_distance_pct = (stop_distance / entry_price) * 100.0 if entry_price > 0 else 0
        min_stop_pct = 5.0  # 5% minimum stop distance
        if stop_distance_pct < min_stop_pct:
            return (
                False,
                f"stop_too_close: {stop_distance_pct:.2f}% < {min_stop_pct}% minimum"
            )
    
    return (True, None)


def check_min_notional(
    position_size_usd: float,
    equity: float
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Check minimum notional for micro mode.
    
    In micro mode, if calculated position size is below minimum,
    either skip the trade or use fixed minimal size.
    
    Args:
        position_size_usd: Calculated position size in USD
        equity: Current account equity
        
    Returns:
        Tuple of (should_proceed, adjusted_size, reason)
        - should_proceed: True if trade should proceed, False to skip
        - adjusted_size: Adjusted position size (or None if skipping)
        - reason: Explanation string
    """
    if position_size_usd >= MICRO_MODE_MIN_NOTIONAL:
        return (True, position_size_usd, None)
    
    # Position size too small - check if we can use fixed minimal size
    # Only use fixed size if it's <= 20% of equity (reasonable limit)
    max_fixed_size = equity * 0.20
    
    if MICRO_MODE_MIN_NOTIONAL <= max_fixed_size:
        logger.info(
            f"Micro mode: Using fixed minimal size ${MICRO_MODE_MIN_NOTIONAL:.2f} "
            f"(calculated: ${position_size_usd:.2f})"
        )
        return (True, MICRO_MODE_MIN_NOTIONAL, f"using_fixed_min_size: ${MICRO_MODE_MIN_NOTIONAL:.2f}")
    else:
        logger.warning(
            f"Micro mode: Skipping trade - calculated size ${position_size_usd:.2f} "
            f"below minimum ${MICRO_MODE_MIN_NOTIONAL:.2f}, but fixed size would exceed 20% of equity"
        )
        return (False, None, f"below_min_notional: ${position_size_usd:.2f} < ${MICRO_MODE_MIN_NOTIONAL:.2f}")


def check_max_positions(
    current_position_count: int
) -> Tuple[bool, Optional[str]]:
    """
    Check maximum positions limit for micro mode.
    
    In micro mode, max 1 position open total (aggressive frequency reduction).
    
    Args:
        current_position_count: Current number of open positions
        
    Returns:
        Tuple of (can_open_new, reason)
    """
    if current_position_count >= MICRO_MODE_MAX_POSITIONS:
        return (
            False,
            f"max_positions_reached: {current_position_count} >= {MICRO_MODE_MAX_POSITIONS}"
        )
    
    return (True, None)


def get_micro_mode_status(equity: float) -> dict:
    """
    Get micro mode status information.
    
    Args:
        equity: Current account equity
        
    Returns:
        Dictionary with micro mode status and configuration
    """
    active = is_micro_mode(equity)
    
    return {
        "active": active,
        "equity": equity,
        "threshold": MICRO_MODE_THRESHOLD,
        "min_stop_atr": MICRO_MODE_MIN_STOP_ATR,
        "min_notional": MICRO_MODE_MIN_NOTIONAL,
        "max_positions": MICRO_MODE_MAX_POSITIONS,
        "message": (
            f"Equity ${equity:.2f} < ${MICRO_MODE_THRESHOLD:.2f} threshold. "
            f"Max {MICRO_MODE_MAX_POSITIONS} position, min stop {MICRO_MODE_MIN_STOP_ATR}ATR, "
            f"min notional ${MICRO_MODE_MIN_NOTIONAL:.2f}"
            if active
            else None
        ),
    }


def get_live_slots_max(equity: float) -> int:
    """
    Get maximum live slots based on account equity.
    
    Live slots limit concurrent live positions (excludes shadow positions).
    
    Args:
        equity: Current account equity in USD
        
    Returns:
        Maximum number of live slots:
        - Balance < $50: 1 slot
        - Balance >= $50: 2 slots (M2 milestone)
        - Balance >= $100: 3 slots (future)
    """
    if equity < LIVE_SLOTS_THRESHOLD_1:
        return 1
    elif equity < LIVE_SLOTS_THRESHOLD_2:
        return 2
    else:
        return 3


def get_live_slots_status(equity: float) -> dict:
    """
    Get live slots status information.
    
    Args:
        equity: Current account equity in USD
        
    Returns:
        Dictionary with live slots status:
        {
            "max_slots": int,
            "current_slots": int,
            "available": bool
        }
    """
    max_slots = get_live_slots_max(equity)
    
    # Get current live position count (excludes shadow positions)
    try:
        from backend.positions.tracker import get_position_tracker
        tracker = get_position_tracker()
        current_slots = tracker.get_live_position_count()
    except Exception as e:
        logger.warning(f"Failed to get live position count: {e}")
        current_slots = 0
    
    available = current_slots < max_slots
    
    return {
        "max_slots": max_slots,
        "current_slots": current_slots,
        "available": available,
    }
