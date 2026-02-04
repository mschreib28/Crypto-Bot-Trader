"""MACD Crossover strategy module for cryptocurrency trading.

This module implements a MACD (Moving Average Convergence Divergence) crossover
strategy that generates signals based on MACD/Signal line crossovers.

Strategy ID: macd_crossover
Symbol: BTC/USD (default, configurable)
Intervals: 4H, 1D

Crypto-specific considerations:
- 24/7 market operation (no session gaps)
- Higher volatility environments
- Volume patterns differ from traditional markets
"""

from research.strategies.macd.config import MACDConfig, get_config_schema
from research.strategies.macd.strategy import MACDStrategy

__all__ = ["MACDStrategy", "MACDConfig", "get_config_schema"]
