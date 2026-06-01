"""Pullback to VWAP Strategy.

Strategy 6: After an initial 8%+ momentum move with RVOL spike, wait for
price to pull back to within 0.5% of VWAP on low absorption volume, then
enter long with stop below the pullback bar low and a 2R target.
"""

from research.strategies.pullback_vwap.strategy import PullbackVWAPStrategy
from research.strategies.pullback_vwap.config import PullbackVWAPConfig

__all__ = ["PullbackVWAPStrategy", "PullbackVWAPConfig"]
