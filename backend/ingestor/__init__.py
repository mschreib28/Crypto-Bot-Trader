"""Data ingestor module for Kraken market data (CLI-backed)."""

from backend.ingestor.cli_ws import MultiIntervalCLIIngestor
from backend.ingestor.historical import backfill_historical_bars, fetch_kraken_ohlc
from backend.ingestor.symbols import fetch_usd_pairs

__all__ = [
    "MultiIntervalCLIIngestor",
    "fetch_usd_pairs",
    "backfill_historical_bars",
    "fetch_kraken_ohlc",
]
