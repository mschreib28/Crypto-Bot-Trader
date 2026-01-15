"""Configuration management for the data ingestor."""

import os
from typing import List


def get_symbols() -> List[str]:
    """
    Get list of trading pairs to ingest.
    
    Reads from INGESTOR_SYMBOLS environment variable (comma-separated),
    or defaults to BTC/USD and ETH/USD.
    
    Returns:
        List of trading pair symbols
    """
    symbols_str = os.getenv("INGESTOR_SYMBOLS", "BTC/USD,ETH/USD")
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
    return symbols if symbols else ["BTC/USD", "ETH/USD"]


def get_intervals() -> List[str]:
    """
    Get list of time intervals for OHLCV aggregation.
    
    Reads from INGESTOR_INTERVALS environment variable (comma-separated),
    or defaults to 4h and 1d.
    
    Returns:
        List of interval strings (e.g., ["4h", "1d"])
    """
    intervals_str = os.getenv("INGESTOR_INTERVALS", "4h,1d")
    intervals = [i.strip().lower() for i in intervals_str.split(",") if i.strip()]
    # Validate intervals
    valid_intervals = ["4h", "1d"]
    filtered = [i for i in intervals if i in valid_intervals]
    return filtered if filtered else ["4h", "1d"]


def get_health_check_file() -> str:
    """
    Get path to health check signal file.
    
    Returns:
        Path to health check file (default: /tmp/ingestor.health)
    """
    return os.getenv("INGESTOR_HEALTH_FILE", "/tmp/ingestor.health")
