"""Volatility Contraction → Expansion Strategy.

This strategy trades post-compression breakout with confirmation + retest
to reduce fakeouts. Target: 55-65% win rate with 2-4R payoff.
"""

from research.strategies.volatility_breakout.strategy import VolatilityBreakoutStrategy
from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig

__all__ = ["VolatilityBreakoutStrategy", "VolatilityBreakoutConfig"]
