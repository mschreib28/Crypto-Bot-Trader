"""VWAP Mean Reversion Strategy.

This strategy captures reversion back to fair value (VWAP / anchored VWAP)
after controlled deviations. Target: 60-75% win rate with 1.2-2.5R payoff.
"""

from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy
from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig

__all__ = ["VWAPMeanReversionStrategy", "VWAPMeanReversionConfig"]
