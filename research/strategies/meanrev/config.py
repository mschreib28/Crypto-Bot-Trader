"""Configuration for MeanReversionStrategy.

Default parameters for the mean-reversion strategy as specified in Ticket 18.

A+ Setup Filters:
- ADX < 20 (ranging market - mean reversion fails in trends)
- ATR > average (market is active, not dead)
- RSI extremes (25/75 for A+ setups)
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class MeanReversionConfig:
    """Configuration parameters for MeanReversionStrategy."""
    
    # Lookback period for moving average and standard deviation (number of bars)
    lookback_period: int = 20
    
    # Standard deviation multiplier for Bollinger Bands
    # Upper band = MA + (multiplier * std_dev)
    # Lower band = MA - (multiplier * std_dev)
    std_dev_multiplier: float = 2.0
    
    # RSI lookback period (number of bars)
    rsi_period: int = 14
    
    # RSI oversold threshold (buy signal when RSI < oversold_threshold)
    # Tightened to 25 for A+ setups (was 30)
    rsi_oversold_threshold: float = 25.0
    
    # RSI overbought threshold (sell signal when RSI > overbought_threshold)
    # Tightened to 75 for A+ setups (was 70)
    rsi_overbought_threshold: float = 75.0
    
    # === A+ SETUP FILTERS ===
    
    # ADX maximum threshold for ranging market detection
    # Mean reversion ONLY works when ADX < this value (ranging market)
    # ADX > 20 indicates trending market where mean reversion fails
    adx_max_threshold: float = 20.0
    
    # Minimum ATR ratio (current ATR / average ATR)
    # Ensures market is active, not dead/consolidating
    # Ratio >= 1.0 means current volatility is at or above average
    atr_min_ratio: float = 1.0
    
    # Notional risk percentage per trade (default: 2.0% as per MSSD)
    notional_risk_pct: float = 2.0
    
    # Symbol to trade (default ETH/USD, configurable)
    symbol: str = "ETH/USD"
    
    # Bar interval for analysis (recommended: 4h for A+ setups)
    interval: str = "5m"
    
    # Strategy identifier
    strategy_id: str = "mean_reversion"


def get_config_schema() -> Dict[str, Any]:
    """Return the configuration schema for mean reversion strategy.
    
    Returns a dictionary with parameters, filters, and description
    suitable for API responses.
    """
    defaults = MeanReversionConfig()
    
    return {
        "strategy_type": "mean_reversion",
        "parameters": {
            "rsi_period": defaults.rsi_period,
            "rsi_overbought": defaults.rsi_overbought_threshold,
            "rsi_oversold": defaults.rsi_oversold_threshold,
            "lookback_period": defaults.lookback_period,
            "bollinger_std": defaults.std_dev_multiplier,
            "adx_max_threshold": defaults.adx_max_threshold,
            "atr_min_ratio": defaults.atr_min_ratio,
            "notional_risk_pct": defaults.notional_risk_pct,
            "interval": defaults.interval,
        },
        "filters": {
            "min_volume_24h": 1000000,
            "min_circulating_supply": 0,
            "max_circulating_supply": None,
        },
        "description": (
            "Mean reversion strategy with A+ setup filtering. "
            "Confidence scoring based on: RSI extreme (30%), BB position (25%), "
            "ADX range filter (25%), ATR activity (20%). "
            "CRITICAL: Only signals in ranging markets (ADX < 20)."
        ),
    }
