"""Bull Flag + Momentum Pullback (I1) — configuration."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BullFlagConfig:
    """Parameters for bull-flag (strong) and momentum-pullback (mild) long entries."""

    strategy_id: str = "bull_flag"
    symbol: str = "BTC/USD"
    interval: str = "5m"

    pole_min_pct: float = 5.0
    pole_max_candles: int = 10
    pole_volume_multiplier: float = 3.0
    flag_min_candles: int = 3
    flag_max_candles: int = 8
    flag_max_retracement: float = 0.5
    entry_volume_multiplier: float = 1.0
    rsi_overbought: float = 75.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ema_fast: int = 9
    ema_slow: int = 20
    rsi_period: int = 14
    volume_sma_period: int = 20
    vwap_touch_pct: float = 0.5
    mild_initial_vol_lookback: int = 15
    confidence_base_mild: float = 0.40
    confidence_base_strong: float = 0.60  # 40% mild + 20% structure
    multi_tf_bonus: float = 0.15
    atr_period: int = 14
    notional_risk_pct: float = 2.0
    max_hold_candles: Optional[int] = None
    tp1_R: float = 1.0
    tp2_R: float = 2.0
    tp1_partial_pct: float = 0.6

    # W2 / swing-style gates (defaults preserve I1 intraday behavior)
    allow_mild_pullback: bool = True
    require_daily_ema200: bool = False
    require_btc_d4_gate: bool = False
    daily_ema_period: int = 200
