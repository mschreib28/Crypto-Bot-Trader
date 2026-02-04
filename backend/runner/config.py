"""Configuration for the Strategy Runner service."""

import os


# Strategy configuration
RUNNER_STRATEGY_ID: str = os.getenv("RUNNER_STRATEGY_ID", "mean_reversion")
RUNNER_SYMBOL: str = os.getenv("RUNNER_SYMBOL", "ETH/USD")
RUNNER_INTERVAL: str = os.getenv("RUNNER_INTERVAL", "4h")

# Redis stream configuration
RUNNER_CONSUMER_GROUP: str = os.getenv("RUNNER_CONSUMER_GROUP", "strategy_runner")
RUNNER_CONSUMER_NAME: str = os.getenv("RUNNER_CONSUMER_NAME", "runner_1")

# Polling configuration
RUNNER_BLOCK_MS: int = int(os.getenv("RUNNER_BLOCK_MS", "5000"))  # Block for 5s waiting for new messages

# Health check file
RUNNER_HEALTH_FILE: str = os.getenv("RUNNER_HEALTH_FILE", "/tmp/runner.health")

# Screener configuration
# Default: 60 seconds (1 minute), max: 60 seconds (hard limit)
# Screener ticks every 60s for status + previews, but real signal generation
# happens on candle close boundaries
from backend.intervals.config import SCREENER_TICK_INTERVAL_SECONDS
SCREENER_INTERVAL_SECONDS: float = SCREENER_TICK_INTERVAL_SECONDS


def get_stream_key(symbol: str, interval: str) -> str:
    """
    Build Redis stream key for market data.
    
    Args:
        symbol: Trading pair (e.g., "ETH/USD")
        interval: Time interval (e.g., "4h")
        
    Returns:
        Stream key in format: market:ohlcv:{symbol}:{interval}
    """
    return f"market:ohlcv:{symbol}:{interval}"
