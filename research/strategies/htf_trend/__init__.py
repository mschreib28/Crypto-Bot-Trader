"""HTF Trend Pullback Continuation Strategy.

This strategy trades WITH a higher timeframe trend using pullbacks into
dynamic support/resistance. Target: 50-65% win rate with strong expectancy.
"""

from research.strategies.htf_trend.strategy import HTFTrendStrategy
from research.strategies.htf_trend.config import HTFTrendConfig

__all__ = ["HTFTrendStrategy", "HTFTrendConfig"]
