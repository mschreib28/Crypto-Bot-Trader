"""Configuration for MomentumStrategy.

Default parameters for the momentum strategy as specified in Ticket 17.

A+ Setup Filters:
- EMA stack alignment (20 > 50 for bullish trend structure)
- ADX > 25 (strong trend confirmation)
- RSI in optimal range (avoid late entries)
- Volume confirmation
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class MomentumConfig:
    """Configuration parameters for MomentumStrategy."""
    
    # Lookback period for momentum calculation (number of bars)
    lookback_period: int = 20
    
    # Rate of Change (ROC) threshold for generating signals
    # Increased to 4% for A+ setups (was 2% - too low for crypto)
    roc_threshold: float = 4.0
    
    # === A+ SETUP FILTERS ===
    
    # EMA periods for trend structure alignment
    # Reduced ema_slow from 200 to 50 for intraday compatibility
    # (200 bars at 1h = 8+ days of data, exceeds available historical)
    ema_fast: int = 20
    ema_medium: int = 50
    ema_slow: int = 50
    
    # ADX threshold for strong trend confirmation
    # ADX > 25 indicates a strong trend worth following
    adx_threshold: float = 25.0
    
    # RSI optimal range for long positions (avoid late entries)
    # RSI 50-75: bullish but not overbought
    rsi_min_long: float = 50.0
    rsi_max_long: float = 75.0
    
    # RSI optimal range for short positions
    # RSI 25-50: bearish but not oversold
    rsi_min_short: float = 25.0
    rsi_max_short: float = 50.0
    
    # Volume ratio threshold for confirmation
    volume_threshold: float = 1.5
    
    # Notional risk percentage per trade (default: 2.0% as per MSSD)
    notional_risk_pct: float = 2.0
    
    # Symbol to trade (default BTC/USD, configurable)
    symbol: str = "BTC/USD"
    
    # Bar interval for analysis (recommended: 4h or 1d for A+ setups)
    interval: str = "4h"
    
    # Stop-loss parameters
    atr_stop_mult: float = 2.0  # Stop distance = ATR * this multiplier (wider stops for trend following)
    atr_period: int = 14  # ATR calculation period
    
    # Direction constraint
    long_only: bool = True  # Permanently disable short signals; bot is long-only by design

    # Strategy identifier
    strategy_id: str = "trend_following"


def get_config_schema() -> Dict[str, Any]:
    """Return the configuration schema for momentum/trend-following strategy.
    
    Returns a dictionary with parameters, filters, and description
    suitable for API responses.
    """
    defaults = MomentumConfig()
    
    return {
        "strategy_type": "momentum",
        "parameters": {
            "lookback_period": defaults.lookback_period,
            "roc_threshold": defaults.roc_threshold,
            "ema_fast": defaults.ema_fast,
            "ema_medium": defaults.ema_medium,
            "ema_slow": defaults.ema_slow,
            "adx_threshold": defaults.adx_threshold,
            "rsi_min_long": defaults.rsi_min_long,
            "rsi_max_long": defaults.rsi_max_long,
            "volume_threshold": defaults.volume_threshold,
            "notional_risk_pct": defaults.notional_risk_pct,
            "interval": defaults.interval,
            "atr_stop_mult": defaults.atr_stop_mult,
            "atr_period": defaults.atr_period,
        },
        "filters": {
            "min_volume_24h": 1000000,
            "min_circulating_supply": 0,
            "max_circulating_supply": None,
        },
        "description": (
            "Momentum/trend-following strategy with A+ setup filtering. "
            "Confidence scoring based on: ROC magnitude (25%), EMA stack alignment (25%), "
            "ADX trend strength (25%), RSI optimal range (15%), volume confirmation (10%)."
        ),
    }
