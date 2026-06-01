"""Micro-account mode for small account sizes.

When account equity is below a threshold (default: $250), micro mode:
- Enforces minimum stop distance (avoids tiny stops that cause constant stop-outs)
- Enforces minimum notional logic (skip trade or use fixed minimal size)
- Makes position sizing more conservative

Entry position *slots* (per-symbol and total concurrent) are enforced by
``check_entry_position_limits`` using bot mode + effective strategy mode
(SHADOW/SIM: unlimited symbols, one slot per symbol for LIVE/PENDING/EXITING; LIVE+LIVE: capped by LIVE_FULL_MAX_CONCURRENT_POSITIONS, min 1).
``MICRO_MODE_MAX_POSITIONS`` / ``check_max_positions`` are legacy-only (no longer used
by the risk evaluator for BUY gating).

This prevents issues where:
- Position sizing produces notional below Kraken minimums
- Fees dominate "R" (risk/reward)
- Tiny stops cause constant stop-outs
"""

import logging
import os
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Micro mode threshold (equity below this triggers micro mode)
MICRO_MODE_THRESHOLD: float = float(os.getenv("MICRO_MODE_THRESHOLD", "250.0"))

# Minimum stop distance in ATR multiples (micro mode)
MICRO_MODE_MIN_STOP_ATR: float = float(os.getenv("MICRO_MODE_MIN_STOP_ATR", "2.0"))

# Minimum notional size for micro mode (fixed minimal size)
MICRO_MODE_MIN_NOTIONAL: float = float(os.getenv("MICRO_MODE_MIN_NOTIONAL", "5.0"))

# Maximum positions in micro mode (legacy; see check_entry_position_limits for BUY gating)
MICRO_MODE_MAX_POSITIONS: int = int(os.getenv("MICRO_MODE_MAX_POSITIONS", "1"))

# Max concurrent open positions when bot is LIVE and effective strategy mode is LIVE
_LIVE_FULL_MAX_RAW = int(os.getenv("LIVE_FULL_MAX_CONCURRENT_POSITIONS", "2"))
if _LIVE_FULL_MAX_RAW < 1:
    logger.warning(
        "LIVE_FULL_MAX_CONCURRENT_POSITIONS=%s is invalid (<1); clamping to 1",
        _LIVE_FULL_MAX_RAW,
    )
LIVE_FULL_MAX_CONCURRENT_POSITIONS: int = max(1, _LIVE_FULL_MAX_RAW)

# Legacy env vars (no longer used for slot max; see _entry_cap_display_for_api / check_entry_position_limits)
LIVE_SLOTS_THRESHOLD_1: float = float(os.getenv("LIVE_SLOTS_THRESHOLD_1", "50.0"))
LIVE_SLOTS_THRESHOLD_2: float = float(os.getenv("LIVE_SLOTS_THRESHOLD_2", "100.0"))

# API display: SHADOW has no total concurrent cap (aligned with check_entry_position_limits)
SHADOW_CONCURRENT_CAP_DISPLAY: int = 999


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


def check_entry_position_limits(
    symbol: str,
    strategy_canonical: str,
    tracker: Any,
) -> Tuple[bool, Optional[str]]:
    """
    Gate new BUY entries by per-symbol slot and optional total cap.

    - Rejects if tracker status for symbol is LIVE, PENDING, or EXITING (one slot per symbol).
    - SHADOW bot or effective SIM: no total cap.
    - LIVE bot + effective LIVE: at most LIVE_FULL_MAX_CONCURRENT_POSITIONS open positions.
    """
    try:
        status = tracker.get_position_status(symbol)
    except Exception as exc:
        logger.warning(f"check_entry_position_limits: get_position_status failed: {exc}")
        return (False, f"position_status_error: {exc}")

    if status in ("LIVE", "PENDING", "EXITING"):
        return (False, f"symbol_slot_occupied: {status}")

    from backend.api.routes.trading import get_bot_mode
    from backend.supervisor.store import get_effective_mode

    bot_mode = get_bot_mode()
    eff_mode, _ = get_effective_mode(strategy_canonical)

    if bot_mode == "SHADOW" or eff_mode == "SIM":
        return (True, None)

    if bot_mode == "LIVE" and eff_mode == "LIVE":
        try:
            current_positions = tracker.get_all_positions()
            position_count = len(current_positions)
        except Exception as exc:
            logger.warning(f"check_entry_position_limits: get_all_positions failed: {exc}")
            return (False, f"position_count_error: {exc}")

        if position_count >= LIVE_FULL_MAX_CONCURRENT_POSITIONS:
            return (
                False,
                f"max_positions_reached: {position_count} >= "
                f"{LIVE_FULL_MAX_CONCURRENT_POSITIONS}",
            )

    return (True, None)


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


def _entry_cap_display_for_api(bot_mode: Optional[str] = None) -> int:
    """
    Concurrent position cap for API display (micro_mode.max_positions, live_slots_max).

    Matches check_entry_position_limits: SHADOW → effectively unlimited (sentinel 999);
    LIVE → LIVE_FULL_MAX_CONCURRENT_POSITIONS.
    """
    if bot_mode is None:
        from backend.api.routes.trading import get_bot_mode

        bot_mode = get_bot_mode()
    if bot_mode == "SHADOW":
        return SHADOW_CONCURRENT_CAP_DISPLAY
    return LIVE_FULL_MAX_CONCURRENT_POSITIONS


def get_micro_mode_status(equity: float, bot_mode: Optional[str] = None) -> dict:
    """
    Get micro mode status information.

    Args:
        equity: Current account equity
        bot_mode: Optional override; if None, uses get_bot_mode() for max_positions display.

    Returns:
        Dictionary with micro mode status and configuration
    """
    active = is_micro_mode(equity)
    cap = _entry_cap_display_for_api(bot_mode)

    return {
        "active": active,
        "equity": equity,
        "threshold": MICRO_MODE_THRESHOLD,
        "min_stop_atr": MICRO_MODE_MIN_STOP_ATR,
        "min_notional": MICRO_MODE_MIN_NOTIONAL,
        "max_positions": cap,
        "message": (
            f"Equity ${equity:.2f} < ${MICRO_MODE_THRESHOLD:.2f} threshold. "
            f"Min stop {MICRO_MODE_MIN_STOP_ATR}ATR, min notional ${MICRO_MODE_MIN_NOTIONAL:.2f}"
            if active
            else None
        ),
    }


def get_live_slots_max(equity: float) -> int:
    """
    Maximum concurrent open positions for API display (matches micro_mode.max_positions).

    Equity argument retained for backward compatibility; cap follows bot mode and
    check_entry_position_limits (SHADOW sentinel 999, LIVE = LIVE_FULL_MAX_CONCURRENT_POSITIONS).
    """
    _ = equity  # unused; legacy callers may still pass equity
    return _entry_cap_display_for_api()


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

    # Align with check_entry_position_limits (len get_all_positions), not get_live_position_count
    try:
        from backend.positions.tracker import get_position_tracker

        tracker = get_position_tracker()
        current_slots = len(tracker.get_all_positions())
    except Exception as e:
        logger.warning(f"Failed to get open position count: {e}")
        current_slots = 0

    available = current_slots < max_slots
    
    return {
        "max_slots": max_slots,
        "current_slots": current_slots,
        "available": available,
    }
