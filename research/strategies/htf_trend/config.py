"""Configuration for HTF Trend Pullback Continuation Strategy."""

import os
from dataclasses import dataclass


def _parse_env_bool(name: str, unset_default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return unset_default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class HTFTrendConfig:
    """Configuration for HTF Trend Pullback strategy."""
    
    strategy_id: str = "htf_trend_pullback"
    symbol: str = "BTC/USD"
    interval: str = "5m"  # Entry timeframe (Ross Cameron spec: 5-minute pullback)
    htf_interval: str = "1h"  # Higher timeframe for trend (Ross Cameron spec: 1-hour trend filter)
    notional_risk_pct: float = 2.0  # Risk per trade (standardized to 2.0% for consistency with other strategies)
    
    # Trend qualification (HTF - 4h)
    htf_ema_slow: int = 200  # EMA200 for trend direction
    htf_ema_fast: int = 50  # Optional: EMA50 for slope
    htf_slope_threshold: float = 0.001  # EMA slope threshold (0.1% per bar)
    htf_adx_threshold: float = 18.0  # Minimum ADX for trend strength (optional)
    use_adx_filter: bool = False  # Whether to use ADX filter
    
    # Pullback detection (ETF - 1h)
    etf_ema_fast: int = 20  # EMA20 for pullback zone
    etf_ema_slow: int = 50  # EMA50 for pullback zone
    pullback_max_ATR: float = 1.5  # Max distance to EMA20 (in ATR units)
    break_bps: float = 50.0  # Max close below EMA50 before invalidating (in bps)
    
    # Entry confirmation
    reversal_body_pct: float = 0.5  # Body must be >= this % of candle range
    reversal_close_position_long: float = 0.7  # Close in top X% for long
    reversal_close_position_short: float = 0.3  # Close in bottom X% for short

    # Direction constraint
    long_only: bool = True  # Permanently disable short signals; bot is long-only by design
    
    # Stop-loss
    atr_stop_mult: float = 1.5  # Stop distance = ATR * this (minimum)
    swing_buffer_ATR: float = 0.15  # Buffer below swing low (in ATR units)
    # True = swing stop anchors to the MOST RECENT swing low (the pullback being
    # traded). False (legacy) = lowest swing in the whole bar window, which can
    # place stops at multi-week extremes and inflate the R denominator.
    swing_stop_recent: bool = False
    
    # Take-profit (Ross Cameron spec: 1:2 R/R = 1.5% stop, 3.0% take profit = 2.0 R)
    tp1_R: float = 1.0  # First target in R-multiples (1.5% if stop is 1.5%)
    tp2_R: float = 2.0  # Second target in R-multiples (3.0% if stop is 1.5%)
    tp1_partial_pct: float = 0.7  # Take 70% at TP1, move stop to breakeven
    
    # Trailing stop
    trailing_stop_mode: str = "structure"  # 'atr' or 'structure'
    atr_trail_mult: float = 2.0  # Trail stop by ATR * this
    
    # Trend invalidation
    trend_invalidation_enabled: bool = True  # Exit if HTF closes below EMA200 (for longs)

    # RSI invalidation (parity with backtest.py HTF_TREND_DEFAULT_CONFIG / check_exits)
    # invalidation_rsi_candles == min_hold_bars_before_rsi_exit in CLAUDE Pending Work naming
    invalidation_rsi_candles: int = 6  # Min bars held before RSI invalidation can fire
    invalidation_rsi_long_floor: int = 35  # Longs: exit when RSI below this (was 40 in older backtests)

    # Macro: BTC daily close must be above BTC EMA(btc_ema_period) before long entries.
    # Env HTF_REQUIRE_BTC_BULL unset → use field default; if set, parsed as bool (1/true/on).
    require_btc_bull_market: bool = True
    btc_ema_period: int = 200

    # Filters
    extension_ATR_mult: float = 3.0  # Skip if HTF price too extended from EMA20
    choppy_regime_adx_threshold: float = 15.0  # Skip if ADX too low (choppy)
    
    # Late entry filter (at entry timeframe - 1h)
    late_entry_ema20_distance_atr: float = 2.0  # Skip if distance from 1h EMA20 exceeds X * ATR
    late_entry_filter_enabled: bool = True
    
    # Time management
    max_hours_in_trade: int = 24  # Exit if TP1 not reached within this many hours
    
    # ATR period
    atr_period: int = 14

    # Swing detection
    swing_lookback_bars: int = 3

    # Hold time limit (number of 1h candles before forced exit; matches DB config field)
    max_hold_candles: int = 3

    def __post_init__(self) -> None:
        if os.environ.get("HTF_REQUIRE_BTC_BULL") is not None:
            self.require_btc_bull_market = _parse_env_bool(
                "HTF_REQUIRE_BTC_BULL", True
            )
