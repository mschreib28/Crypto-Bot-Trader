"""Mean-reversion strategy module for ETH/USD trading.

This module implements a mean-reversion trading strategy that generates signals
based on Bollinger Band deviation and RSI extremes.

Strategy ID: meanrev_eth
Symbol: ETH/USD
Intervals: 4H, 1D
"""

from research.strategies.meanrev.config import MeanReversionConfig
from research.strategies.meanrev.strategy import MeanReversionStrategy

__all__ = ["MeanReversionStrategy", "MeanReversionConfig"]
