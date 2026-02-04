"""Momentum strategy module for BTC/USD trading.

This module implements a momentum-based trading strategy that generates signals
based on price momentum indicators (e.g., N-bar breakout or ROC threshold).

Strategy ID: momentum_btc
Symbol: BTC/USD
Intervals: 4H, 1D
"""

from research.strategies.momentum.config import MomentumConfig
from research.strategies.momentum.strategy import MomentumStrategy

__all__ = ["MomentumStrategy", "MomentumConfig"]
