"""Data ingestor module for Kraken WebSocket market data."""

from backend.ingestor.bar_builder import BarBuilder
from backend.ingestor.historical import backfill_historical_bars, fetch_kraken_ohlc
from backend.ingestor.kraken_ws import KrakenWebSocketClient, MultiConnectionManager
from backend.ingestor.normalizer import Normalizer
from backend.ingestor.symbols import fetch_usd_pairs

__all__ = [
    "KrakenWebSocketClient",
    "MultiConnectionManager",
    "BarBuilder",
    "Normalizer",
    "fetch_usd_pairs",
    "backfill_historical_bars",
    "fetch_kraken_ohlc",
]
