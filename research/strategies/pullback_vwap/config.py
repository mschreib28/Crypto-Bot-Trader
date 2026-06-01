"""Configuration for Pullback to VWAP Strategy."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PullbackVWAPConfig:
    """Configuration for Pullback to VWAP strategy (Strategy 6)."""

    strategy_id: str = "pullback_vwap"
    symbol: str = "BTC/USD"
    interval: str = "15m"
    notional_risk_pct: float = 2.0

    # Direction constraint
    long_only: bool = True  # This strategy is long-only by design

    # Initial move detection
    initial_move_min_pct: float = 8.0        # Price must be up ≥8% vs close N bars ago
    initial_move_lookback_bars: int = 96     # 96 × 15m = 24h lookback window
    initial_move_rvol_min: float = 2.0       # Initial move bar volume ≥ 2× 20-bar avg

    # Pullback trigger
    pullback_threshold_pct: float = 0.5      # Price within 0.5% of VWAP to qualify

    # Volume absorption confirmation
    volume_absorption_check: bool = True     # Require pullback bar volume < initial move volume
    absorption_vs_sma_max: float = 1.5       # Pullback bar volume < SMA × this (secondary gate)

    # Exit levels
    tp1_R: float = 1.0                       # First target in R-multiples
    tp2_R: float = 2.0                       # Second target in R-multiples (primary)
    tp1_partial_pct: float = 0.6             # Take 60% at TP1, move stop to breakeven

    # Time management
    max_bars_in_trade: int = 8               # 8 × 15m = 2h maximum hold

    # Stop-loss
    atr_stop_mult: float = 1.0               # Stop = pullback bar low − ATR × this

    # Indicators
    rsi_period: int = 14
    atr_period: int = 14
    volume_sma_period: int = 20

    # VWAP calculation
    anchored_vwap_lookback: int = 20         # Bars to look back for VWAP anchor point

    # Monitor-level config (read by position monitor, not used by strategy logic)
    max_hold_candles: Optional[int] = None
