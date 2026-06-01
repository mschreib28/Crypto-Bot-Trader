"""Configuration for Volatility Contraction → Expansion Strategy."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class VolatilityBreakoutConfig:
    """Configuration for Volatility Breakout strategy."""
    
    strategy_id: str = "volatility_breakout"
    symbol: str = "BTC/USD"
    interval: str = "15m"  # Entry timeframe
    htf_interval: str = "1h"  # Higher timeframe for filter
    notional_risk_pct: float = 2.0  # Risk per trade (standardized to 2.0% for consistency with other strategies)
    
    # Compression detection
    squeeze_percentile: float = 10.0  # Bottom X percentile for BB Width
    squeeze_lookback_N: int = 100  # Bars to look back for percentile (100 allows ~116-bar streams)
    vol_compress_mult: float = 0.9  # Volume must be <= this * vol_sma
    atr_compress_threshold: float = 0.7  # ATR ratio threshold for compression
    
    # Breakout detection
    vol_breakout_mult: float = 1.5  # Volume must be >= this * vol_sma
    breakout_body_pct: float = 0.55  # Body must be >= this % of candle range
    breakout_close_position: float = 0.7  # Close must be in top X% of range (for long)
    
    # Retest logic
    retest_window_bars: int = 6  # Retest must occur within this many bars
    retest_fail_bps: float = 50.0  # Retest fails if closes back into range by this many bps
    
    # Stop-loss
    atr_stop_mult: float = 1.8  # Stop distance = ATR * this multiplier
    retest_buffer_ATR: float = 0.15  # Buffer below retest low (in ATR units)
    
    # Take-profit (Ross Cameron spec: 1:2 R/R = 1.5% stop, 3.0% take profit)
    # Note: ATR multipliers will be calculated based on stop distance to achieve 2.0 R target
    atr_target1_mult: float = 1.5  # TP1 = entry + ATR * this (approximately 1.0 R)
    atr_target2_mult: float = 3.0  # TP2 = entry + ATR * this (approximately 2.0 R = 3.0%)
    use_measured_move: bool = False  # Use range height projection instead of ATR
    
    # Trailing stop
    trailing_stop_mode: str = "atr"  # 'atr' or 'structure'
    atr_trail_mult: float = 2.0  # Trail stop by ATR * this
    
    # Filters
    atr_max_ATR_mult: float = 2.5  # Skip if ATR% exceeds this
    htf_resistance_distance_ATR: float = 1.0  # Skip if too close to HTF resistance
    # BTC macro: skip long entries when BTC daily close is below EMA (env: VB_REQUIRE_BTC_BULL, default on)
    require_btc_bull_market: bool = True
    btc_ema_period: int = 200
    
    # Bollinger Bands
    bb_period: int = 20
    bb_std_dev: float = 2.0
    
    # Donchian Channel (optional)
    donchian_period: int = 20
    
    # Volume SMA (Ross Cameron spec: 5-period average for breakout confirmation)
    volume_sma_period: int = 5  # Changed from 20 to 5 for Ross Cameron spec
    
    # ATR period
    atr_period: int = 14

    # Monitor-level config (read by position monitor, not used by strategy logic)
    max_hold_candles: Optional[int] = None  # Overrides monitor default when set in DB config

    def __post_init__(self) -> None:
        raw = os.environ.get("VB_REQUIRE_BTC_BULL")
        if raw is not None:
            self.require_btc_bull_market = raw.strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
