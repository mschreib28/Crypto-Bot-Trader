"""Configuration for MACD Crossover Strategy.

Default parameters for the MACD crossover strategy optimized for cryptocurrency markets.
Standard MACD parameters (12/26/9) work well across crypto timeframes due to 24/7 trading.

A+ Setup Filters:
- EMA trend alignment (price above/below EMA for direction confirmation)
- ADX trend strength (trending market confirmation)
- Volume confirmation (validates conviction behind the move)
"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class MACDConfig:
    """Configuration parameters for MACDStrategy.
    
    Attributes:
        fast_period: Fast EMA period (default: 12)
        slow_period: Slow EMA period (default: 26)
        signal_period: Signal line EMA period (default: 9)
        ema_trend_period: EMA period for trend filter (default: 50)
        adx_threshold: Minimum ADX for trending market (default: 20)
        volume_threshold: Minimum volume ratio for confirmation (default: 1.5)
        notional_risk_pct: Notional risk percentage per trade (default: 2.0%)
        symbol: Symbol to trade (default: BTC/USD)
        strategy_id: Unique strategy identifier
    """
    
    # Fast EMA period for MACD line calculation
    fast_period: int = 12
    
    # Slow EMA period for MACD line calculation
    slow_period: int = 26
    
    # Signal line EMA period (EMA of MACD line)
    signal_period: int = 9
    
    # === A+ SETUP FILTERS ===
    
    # EMA period for trend alignment filter
    # Price should be above EMA for longs, below for shorts
    ema_trend_period: int = 50
    
    # ADX threshold for trending market confirmation
    # ADX > 25 indicates a strong trending market (good for MACD)
    # ADX < 25 indicates ranging/choppy market (avoid signals)
    adx_threshold: float = 25.0
    
    # Volume ratio threshold for confirmation
    # Current volume should be > threshold × average volume
    volume_threshold: float = 1.5
    
    # Notional risk percentage per trade (default: 2.0% as per MSSD)
    notional_risk_pct: float = 2.0
    
    # Symbol to trade (default BTC/USD, configurable for any crypto pair)
    symbol: str = "BTC/USD"
    
    # Bar interval for analysis (recommended: 1h or 4h for A+ setups)
    interval: str = "1h"
    
    # Stop-loss parameters
    atr_stop_mult: float = 1.8  # Stop distance = ATR * this multiplier (appropriate for trend following)
    atr_period: int = 14  # ATR calculation period
    
    # Strategy identifier
    strategy_id: str = "macd_crossover"


def get_config_schema() -> Dict[str, Any]:
    """Return the configuration schema for MACD crossover strategy.
    
    Returns a dictionary with parameters, filters, and description
    suitable for API responses.
    """
    defaults = MACDConfig()
    return {
        "strategy_type": "macd",
        "parameters": {
            "fast_period": defaults.fast_period,
            "slow_period": defaults.slow_period,
            "signal_period": defaults.signal_period,
            "ema_trend_period": defaults.ema_trend_period,
            "adx_threshold": defaults.adx_threshold,
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
            "Generates signals on MACD/Signal line crossovers with A+ setup filtering. "
            "Confidence scoring based on: crossover detection (25%), histogram expansion (15%), "
            "EMA trend alignment (25%), ADX trend strength (20%), volume confirmation (15%)."
        ),
    }
