"""Configuration for VWAP Mean Reversion Strategy."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VWAPMeanReversionConfig:
    """Configuration for VWAP Mean Reversion strategy."""
    
    strategy_id: str = "vwap_meanreversion"
    symbol: str = "BTC/USD"
    interval: str = "15m"  # Entry timeframe
    htf_interval: str = "1h"  # Higher timeframe for regime filter
    notional_risk_pct: float = 1.0  # Risk per trade
    
    # Deviation parameters
    dev_threshold_ATR: float = 0.5  # Price must deviate by this * ATR from VWAP
    rsi_oversold: float = 30.0  # RSI threshold for oversold (long entry)
    rsi_overbought: float = 70.0  # RSI threshold for overbought (short entry)
    
    # Stop-loss parameters
    atr_stop_mult: float = 1.5  # Stop distance = ATR * this multiplier
    swing_lookback_bars: int = 5  # Bars to look back for swing low/high
    stop_buffer_ATR: float = 0.15  # Buffer below swing low (in ATR units)
    
    # Take-profit parameters
    tp1_R: float = 1.2  # First target in R-multiples
    tp2_R: float = 2.5  # Second target in R-multiples
    tp1_partial_pct: float = 0.6  # Take 60% at TP1, move stop to breakeven
    
    # Time management
    max_bars_in_trade: int = 12  # Exit if TP1 not reached within this many bars
    
    # Volume filter
    volume_filter_mode: str = "conservative"  # 'conservative' or 'aggressive'
    volume_max_mult: float = 1.5  # Max volume relative to SMA (conservative)
    volume_breakout_mult: float = 2.0  # Allow higher volume if reversal confirmed (aggressive)
    
    # Regime filter (HTF)
    regime_slope_threshold: float = 0.001  # EMA slope threshold (0.1% per bar)
    volatility_max_ATR_mult: float = 2.5  # Block if HTF ATR% exceeds this
    
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
