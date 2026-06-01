"""Centralized interval configuration for the trading bot.

This module defines all update intervals used throughout the system:
- UI_REFRESH_INTERVAL: Frontend polling for positions/balance/PnL
- SCREENER_TICK_INTERVAL: How often the screener scans (status + previews)
- STRATEGY_TIMEFRAME: Per-strategy timeframe configuration

These concepts are separated to avoid confusion between UI updates,
screener ticks, and actual strategy evaluation timeframes.
"""

import os
from typing import Dict

# ============================================================================
# UI Refresh Intervals (Frontend Polling)
# ============================================================================

# Portfolio/positions/PnL updates
# Default: 10 seconds for near-real-time awareness of stops, fills, and risk limits
# Hard max: 30 seconds
# Preferred: 5-10 seconds
UI_REFRESH_INTERVAL_SECONDS: float = float(os.getenv("UI_REFRESH_INTERVAL_SECONDS", "10.0"))
UI_REFRESH_INTERVAL_SECONDS = min(UI_REFRESH_INTERVAL_SECONDS, 30.0)  # Cap at 30s max

# Position sync interval (syncs from Kraken)
POSITION_SYNC_INTERVAL_SECONDS: float = UI_REFRESH_INTERVAL_SECONDS

# Position monitor interval (updates P&L)
POSITION_MONITOR_INTERVAL_SECONDS: float = UI_REFRESH_INTERVAL_SECONDS

# ============================================================================
# Screener Tick Intervals
# ============================================================================

# Screener scan interval
# Default: 30 seconds for near-real-time signal strength updates
# Min: 30s, max: 60s - balance between freshness and API/CPU load
# Note: Real signal generation happens on candle close, but screener
#       runs frequently for accurate, up-to-date signal strength display
SCREENER_TICK_INTERVAL_SECONDS: float = float(os.getenv("SCREENER_INTERVAL_SECONDS", "30.0"))
SCREENER_TICK_INTERVAL_SECONDS = max(30.0, min(SCREENER_TICK_INTERVAL_SECONDS, 60.0))

# ============================================================================
# Strategy Timeframes (Per-Strategy Configuration)
# ============================================================================

# Default timeframes for each strategy
# These are used when a strategy doesn't have an explicit timeframe in its config
STRATEGY_DEFAULT_TIMEFRAMES: Dict[str, str] = {
    "vwap_meanreversion": "15m",
    "volatility_breakout": "15m",
    "htf_trend_pullback": "1h",
}

# HTF (Higher Timeframe) intervals for strategies that use them
STRATEGY_DEFAULT_HTF_TIMEFRAMES: Dict[str, str] = {
    "vwap_meanreversion": "1h",
    "volatility_breakout": "1h",
    "htf_trend_pullback": "4h",
}


def get_strategy_timeframe(strategy_name: str, default: str = "15m") -> str:
    """
    Get the default timeframe for a strategy.
    
    Args:
        strategy_name: Strategy name (e.g., "vwap_meanreversion")
        default: Fallback timeframe if strategy not found
        
    Returns:
        Timeframe string (e.g., "15m", "1h")
    """
    return STRATEGY_DEFAULT_TIMEFRAMES.get(strategy_name.lower(), default)


def get_strategy_htf_timeframe(strategy_name: str, default: str = "1h") -> str:
    """
    Get the default HTF timeframe for a strategy.
    
    Args:
        strategy_name: Strategy name (e.g., "vwap_meanreversion")
        default: Fallback HTF timeframe if strategy not found
        
    Returns:
        HTF timeframe string (e.g., "1h", "4h")
    """
    return STRATEGY_DEFAULT_HTF_TIMEFRAMES.get(strategy_name.lower(), default)
