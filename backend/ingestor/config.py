"""Configuration management for the data ingestor."""

import os
from typing import List, Optional

# Kraken WebSocket subscription limit per connection
MAX_SYMBOLS_PER_WS = 45

# Default number of symbols to stream (top by volume)
# Reduced for WebSocket stability - can increase once connection is stable
SYMBOL_LIMIT = 20

# Maximum total symbols after merging with held (WebSocket stability)
# With 2 intervals per symbol, this = 50 subscriptions max
MAX_TOTAL_SYMBOLS = 25

# Symbol refresh interval (seconds) - 1 hour for RVOL re-ranking
# This is the expensive operation (fetches OHLC data for RVOL calculation)
SYMBOL_REFRESH_INTERVAL = 3600

# Universe refresh interval (seconds) - 15 minutes for ticker data (24h change, volume)
# This is fast (single REST API call) and updates the symbol universe
UNIVERSE_REFRESH_INTERVAL = 15 * 60  # 15 minutes

# Hysteresis settings to prevent universe thrashing
# Only add symbols that rank in top N for M consecutive refreshes
UNIVERSE_ADD_THRESHOLD_RANK = 10  # Must rank in top 10 to be considered for addition
UNIVERSE_ADD_CONFIRMATIONS = 2    # Must appear in top 10 for 2 consecutive refreshes

# Only drop symbols that fall below rank N for M consecutive refreshes
UNIVERSE_DROP_THRESHOLD_RANK = 30  # Must fall below rank 30 to be considered for removal
UNIVERSE_DROP_CONFIRMATIONS = 2    # Must be below rank 30 for 2 consecutive refreshes

# Immediate drop conditions (hard failures - no confirmation needed)
# Default: $10M minimum (configurable via MIN_24H_VOLUME_USD env var)
MIN_24H_VOLUME_USD = 10000000.0  # Minimum 24h volume in USD (below this = immediate drop)
VOLUME_COLLAPSE_THRESHOLD = 0.1  # If volume drops below 10% of previous, immediate drop

# Spread filtering threshold
MAX_SPREAD_BPS = 15.0  # Maximum allowed bid-ask spread in basis points (default: 15 bps = 0.15%)

# Minimum RVOL threshold (%) - symbols below this excluded unless owned
MIN_RVOL_THRESHOLD = 100

# Number of candidate symbols to evaluate for RVOL (limits API calls)
RVOL_CANDIDATE_LIMIT = 50

# Default intervals for screening
# 5m for MACD/Mean Reversion, 15m for VWAP/Volatility strategies, 1h for HTF Trend
DEFAULT_INTERVALS = ["5m", "15m", "1h"]

# Symbols are fetched dynamically at startup (None means fetch from Kraken)
SYMBOLS: Optional[List[str]] = None


def get_max_symbols_per_ws() -> int:
    """
    Get maximum number of symbols per WebSocket connection.
    
    Kraken limits subscriptions per connection. With 2 intervals per symbol,
    this effectively means MAX_SYMBOLS_PER_WS * 2 subscriptions per connection.
    
    Returns:
        Maximum symbols per WebSocket connection
    """
    return int(os.getenv("INGESTOR_MAX_SYMBOLS_PER_WS", str(MAX_SYMBOLS_PER_WS)))


def get_symbol_limit() -> int:
    """
    Get the number of top symbols to stream by volume.
    
    Returns:
        Number of top USD pairs to fetch (default: 25)
    """
    return int(os.getenv("INGESTOR_SYMBOL_LIMIT", str(SYMBOL_LIMIT)))


def get_max_total_symbols() -> int:
    """
    Get maximum total symbols after merging with held symbols.
    
    Caps the total for WebSocket stability.
    
    Returns:
        Maximum total symbols (default: 30)
    """
    return int(os.getenv("INGESTOR_MAX_TOTAL_SYMBOLS", str(MAX_TOTAL_SYMBOLS)))


def get_symbols() -> Optional[List[str]]:
    """
    Get list of trading pairs to ingest.
    
    Reads from INGESTOR_SYMBOLS environment variable (comma-separated).
    Returns None if not set, indicating symbols should be fetched dynamically.
    
    Returns:
        List of trading pair symbols, or None for dynamic fetching
    """
    symbols_str = os.getenv("INGESTOR_SYMBOLS", "")
    if not symbols_str:
        return None  # Signal to fetch dynamically from Kraken
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
    return symbols if symbols else None


def get_pinned_symbols() -> List[str]:
    """
    Get symbols that must always be ingested regardless of RVOL ranking.

    Reads from INGESTOR_PINNED_SYMBOLS (comma-separated). Used to guarantee
    data for runner strategies that require specific symbols (e.g. BTC/USD for HTF Trend).
    """
    raw = os.getenv("INGESTOR_PINNED_SYMBOLS", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_intervals() -> List[str]:
    """
    Get list of time intervals for OHLCV aggregation.
    
    Reads from INGESTOR_INTERVALS environment variable (comma-separated),
    or defaults to 1m and 5m for screening.
    
    Returns:
        List of interval strings (e.g., ["1m", "5m"])
    """
    intervals_str = os.getenv("INGESTOR_INTERVALS", ",".join(DEFAULT_INTERVALS))
    intervals = [i.strip().lower() for i in intervals_str.split(",") if i.strip()]
    # Validate intervals
    valid_intervals = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    filtered = [i for i in intervals if i in valid_intervals]
    return filtered if filtered else DEFAULT_INTERVALS


def get_health_check_file() -> str:
    """
    Get path to health check signal file.
    
    Returns:
        Path to health check file (default: /tmp/ingestor.health)
    """
    return os.getenv("INGESTOR_HEALTH_FILE", "/tmp/ingestor.health")


def get_symbol_refresh_interval() -> int:
    """
    Get symbol refresh interval in seconds.
    
    Returns:
        Refresh interval (default: 3600 seconds / 1 hour)
    """
    return int(os.getenv("INGESTOR_SYMBOL_REFRESH_INTERVAL", str(SYMBOL_REFRESH_INTERVAL)))


def get_min_rvol_threshold() -> float:
    """
    Get minimum RVOL threshold percentage.
    
    Symbols below this threshold are excluded unless owned.
    
    Returns:
        Minimum RVOL threshold (default: 100%)
    """
    return float(os.getenv("INGESTOR_MIN_RVOL_THRESHOLD", str(MIN_RVOL_THRESHOLD)))


def get_rvol_candidate_limit() -> int:
    """
    Get number of candidate symbols to evaluate for RVOL.
    
    Limits the number of OHLC API calls needed.
    
    Returns:
        Number of candidates to evaluate (default: 50)
    """
    return int(os.getenv("INGESTOR_RVOL_CANDIDATE_LIMIT", str(RVOL_CANDIDATE_LIMIT)))


def get_universe_refresh_interval() -> int:
    """
    Get universe refresh interval in seconds.
    
    This is the fast refresh that updates ticker data (24h change, volume)
    without recalculating RVOL. Default: 15 minutes.
    
    Returns:
        Universe refresh interval (default: 900 seconds / 15 minutes)
    """
    return int(os.getenv("INGESTOR_UNIVERSE_REFRESH_INTERVAL", str(UNIVERSE_REFRESH_INTERVAL)))


def get_universe_add_threshold_rank() -> int:
    """
    Get rank threshold for adding symbols to universe.
    
    Symbols must rank in top N to be considered for addition.
    
    Returns:
        Rank threshold (default: 10)
    """
    return int(os.getenv("INGESTOR_UNIVERSE_ADD_THRESHOLD_RANK", str(UNIVERSE_ADD_THRESHOLD_RANK)))


def get_universe_add_confirmations() -> int:
    """
    Get number of confirmations required before adding a symbol.
    
    Symbol must appear in top rank threshold for this many consecutive refreshes.
    
    Returns:
        Number of confirmations (default: 2)
    """
    return int(os.getenv("INGESTOR_UNIVERSE_ADD_CONFIRMATIONS", str(UNIVERSE_ADD_CONFIRMATIONS)))


def get_universe_drop_threshold_rank() -> int:
    """
    Get rank threshold for dropping symbols from universe.
    
    Symbols must fall below this rank to be considered for removal.
    
    Returns:
        Rank threshold (default: 30)
    """
    return int(os.getenv("INGESTOR_UNIVERSE_DROP_THRESHOLD_RANK", str(UNIVERSE_DROP_THRESHOLD_RANK)))


def get_universe_drop_confirmations() -> int:
    """
    Get number of confirmations required before dropping a symbol.
    
    Symbol must be below drop threshold for this many consecutive refreshes.
    
    Returns:
        Number of confirmations (default: 2)
    """
    return int(os.getenv("INGESTOR_UNIVERSE_DROP_CONFIRMATIONS", str(UNIVERSE_DROP_CONFIRMATIONS)))


def get_min_24h_volume_usd() -> float:
    """
    Get minimum 24h volume threshold for immediate drop.
    
    Symbols below this volume are immediately dropped (hard failure).
    
    Returns:
        Minimum volume in USD (default: 10000000.0 = $10M)
    """
    return float(os.getenv("MIN_24H_VOLUME_USD", str(MIN_24H_VOLUME_USD)))


def get_max_spread_bps() -> float:
    """
    Get maximum allowed bid-ask spread in basis points.
    
    Symbols with spread above this threshold are excluded from strategy evaluation.
    
    Returns:
        Maximum spread in basis points (default: 15.0 = 0.15%)
    """
    return float(os.getenv("MAX_SPREAD_BPS", str(MAX_SPREAD_BPS)))




def get_volume_collapse_threshold() -> float:
    """
    Get volume collapse threshold for immediate drop.
    
    If volume drops below this fraction of previous volume, immediate drop.
    
    Returns:
        Volume collapse threshold (default: 0.1 = 10%)
    """
    return float(os.getenv("INGESTOR_VOLUME_COLLAPSE_THRESHOLD", str(VOLUME_COLLAPSE_THRESHOLD)))


def get_enforce_whitelist_in_shadow() -> bool:
    """
    Get whether to enforce whitelist filtering in shadow mode.
    
    When True, only symbols in the live universe are allowed in shadow mode.
    When False, shadow mode allows any symbol that passes liquidity/spread filters.
    
    Returns:
        True if whitelist should be enforced in shadow mode (default: True)
    """
    return os.getenv("ENFORCE_WHITELIST_IN_SHADOW", "true").lower() in ("true", "1", "yes")
