"""Strategy module for quantitative research.

This module provides the BaseStrategy abstraction for implementing trading strategies.
Strategies consume market data and emit trade intents, following the constraints
defined in docs/MSSD.md § 4.2.
"""

from research.strategies.base import BaseStrategy
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent

# New production strategies
from research.strategies.vwap_meanrev import VWAPMeanReversionStrategy, VWAPMeanReversionConfig
from research.strategies.volatility_breakout import VolatilityBreakoutStrategy, VolatilityBreakoutConfig
from research.strategies.htf_trend import HTFTrendStrategy, HTFTrendConfig

# Legacy strategies (deprecated but kept for reference)
from research.strategies.meanrev import MeanReversionStrategy
from research.strategies.macd import MACDStrategy
from research.strategies.momentum import MomentumStrategy

__all__ = [
    "BaseStrategy",
    "MarketDataEvent",
    "SignalResult",
    "TradeIntent",
    # New strategies
    "VWAPMeanReversionStrategy",
    "VWAPMeanReversionConfig",
    "VolatilityBreakoutStrategy",
    "VolatilityBreakoutConfig",
    "HTFTrendStrategy",
    "HTFTrendConfig",
    # Legacy strategies (deprecated)
    "MeanReversionStrategy",
    "MACDStrategy",
    "MomentumStrategy",
]
