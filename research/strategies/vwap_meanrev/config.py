"""Configuration for VWAP Mean Reversion Strategy."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class VWAPMeanReversionConfig:
    """Configuration for VWAP Mean Reversion strategy."""
    
    strategy_id: str = "vwap_meanreversion"
    symbol: str = "BTC/USD"
    interval: str = "15m"  # Entry timeframe
    htf_interval: str = "1h"  # Higher timeframe for regime filter
    notional_risk_pct: float = 2.0  # Risk per trade (standardized to 2.0% for consistency with other strategies)
    
    # Deviation parameters (Ross Cameron spec: Price > 2% below VWAP)
    dev_threshold_pct: float = 2.0  # Price must deviate by this % from VWAP (Ross Cameron spec: >2%)
    dev_threshold_ATR: float = 0.5  # Legacy ATR-based threshold (kept for backward compatibility)
    use_percentage_deviation: bool = True  # Use percentage-based deviation instead of ATR-based
    rsi_oversold: float = 30.0  # RSI threshold for oversold (long entry)
    rsi_overbought: float = 70.0  # RSI threshold for overbought (short entry)
    
    # Stop-loss parameters
    atr_stop_mult: float = 1.5  # Stop distance = ATR * this multiplier
    swing_lookback_bars: int = 5  # Bars to look back for swing low/high
    stop_buffer_ATR: float = 0.15  # Buffer below swing low (in ATR units)
    # True = swing stop anchors to the MOST RECENT swing low/high (the structure
    # being traded). False (legacy) = lowest/highest swing in the whole bar
    # window, which can place stops at multi-week extremes and inflate the R
    # denominator. Backtest flag: --swing-stop-recent.
    swing_stop_recent: bool = False
    
    # Take-profit parameters (Ross Cameron spec: 1:2 R/R = 1.5% stop, 3.0% take profit = 2.0 R)
    tp1_R: float = 1.0  # First target in R-multiples (1.5% if stop is 1.5%)
    tp2_R: float = 2.0  # Second target in R-multiples (3.0% if stop is 1.5%)
    tp1_partial_pct: float = 0.6  # Take 60% at TP1, move stop to breakeven
    
    # Time management: Allow up to 6 candles (1.5h at 15m) for TP1/TP2 to be reached
    max_bars_in_trade: int = 6  # Exit after 1.5 hours (6 bars × 15m) — must cover fees before exit
    
    # Volume filter
    volume_filter_mode: str = "conservative"  # 'conservative' or 'aggressive'
    volume_max_mult: float = 1.5  # Max volume relative to SMA (conservative)
    volume_breakout_mult: float = 2.0  # Allow higher volume if reversal confirmed (aggressive)
    # When set (e.g. 2.0), long entries require volume >= this × volume SMA on the signal bar.
    # If set with long_only=True, conservative volume_max_mult is not applied (spike confirmation).
    long_min_volume_ratio: Optional[float] = 1.5
    # When set (e.g. 40.0), long entries require latest HTF (htf_interval) RSI <= this (uses rsi_period).
    htf_rsi_long_max: Optional[float] = None
    
    # Regime filter (HTF)
    regime_slope_threshold: float = 0.001  # EMA slope threshold (0.1% per bar)
    volatility_max_ATR_mult: float = 2.5  # Block if HTF ATR% exceeds this
    # True = the trend half of the regime filter actually blocks longs when HTF
    # price < EMA200 AND the EMA200 slope is strongly bearish. False (legacy) =
    # trend check is advisory only — every path returned allowed=True and only
    # the volatility cap could block (the documented "1h regime filter" was a
    # no-op for trend).
    regime_block_bearish: bool = False
    
    # VWAP calculation
    vwap_session_hours: int = 24  # Session length for VWAP (24h for crypto)
    anchored_vwap_lookback: int = 20  # Bars to look back for anchor point
    
    # Entry refinement
    entry_offset_ATR: float = 0.05  # Entry price offset from VWAP (in ATR units)
    
    # Reversal confirmation
    reversal_body_pct: float = 0.6  # Body must be >= 60% of candle range
    reversal_close_position: float = 0.25  # Close must be in top 25% of range (for long)
    
    # RSI period
    rsi_period: int = 14
    
    # ATR period
    atr_period: int = 14
    
    # Volume SMA period
    volume_sma_period: int = 20
    
    # HTF EMA periods for trend filter
    htf_ema_fast: int = 50
    htf_ema_slow: int = 200
    
    # Momentum exclusion (knife-catch prevention)
    momentum_exclusion_bars: int = 3  # Check last N candles
    momentum_body_pct_threshold: float = 0.6  # Body must be >= 60% of range
    momentum_exclusion_enabled: bool = True
    
    # VWAP slope guard
    vwap_slope_threshold: float = 0.0005  # VWAP slope threshold (0.05% per bar)
    vwap_slope_confirmation_bars: int = 2  # Require N confirmation candles if slope is strong
    vwap_slope_guard_enabled: bool = True

    # Direction constraint
    long_only: bool = True  # Permanently disable short signals; bot is long-only by design

    # Monitor-level config (read by position monitor, not used by strategy logic)
    max_hold_candles: Optional[int] = None  # Overrides monitor default when set in DB config
    invalidation_vwap_atr_mult: Optional[float] = None  # Overrides monitor default when set in DB config
