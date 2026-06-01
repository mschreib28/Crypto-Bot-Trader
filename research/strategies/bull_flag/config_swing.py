"""W2 — Swing Bull Flag preset (4h, shared BullFlagStrategy / BullFlagConfig)."""

from __future__ import annotations

from typing import Any, Dict

# Overrides merged on top of intraday bull-flag defaults for backtests / DB parameters.
SWING_BULL_FLAG_BACKTEST_OVERRIDES: Dict[str, Any] = {
    "interval": "4h",
    "flag_min_candles": 2,
    "flag_max_candles": 5,
    "max_bars_in_trade": 30,
    "allow_mild_pullback": False,
    "require_daily_ema200": True,
    "require_btc_d4_gate": True,
    "daily_ema_period": 200,
}


def swing_bull_flag_parameters_json() -> Dict[str, Any]:
    """Nested `parameters` object for strategies.sql (excludes top-level DB keys)."""
    return {k: v for k, v in SWING_BULL_FLAG_BACKTEST_OVERRIDES.items() if k != "max_bars_in_trade"}
