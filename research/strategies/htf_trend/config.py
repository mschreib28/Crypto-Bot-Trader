"""Configuration for HTF Trend Pullback Continuation Strategy."""

from dataclasses import dataclass


@dataclass
class HTFTrendConfig:
    """Configuration for HTF Trend Pullback strategy."""
    
    strategy_id: str = "htf_trend_pullback"
    symbol: str = "BTC/USD"
    interval: str = "1h"  # Entry timeframe
    htf_interval: str = "4h"  # Higher timeframe for trend
    notional_risk_pct: float = 1.0  # Risk per trade
    
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
    
    # Stop-loss
    atr_stop_mult: float = 1.5  # Stop distance = ATR * this (minimum)
    swing_buffer_ATR: float = 0.15  # Buffer below swing low (in ATR units)
    
    # Take-profit
    tp1_R: float = 1.5  # First target in R-multiples
    tp2_R: float = 3.0  # Second target in R-multiples
    tp1_partial_pct: float = 0.7  # Take 70% at TP1, move stop to breakeven
    
    # Trailing stop
    trailing_stop_mode: str = "structure"  # 'atr' or 'structure'
    atr_trail_mult: float = 2.0  # Trail stop by ATR * this
    
    # Trend invalidation
    trend_invalidation_enabled: bool = True  # Exit if HTF closes below EMA200 (for longs)
    
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
