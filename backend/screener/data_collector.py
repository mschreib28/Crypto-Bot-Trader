"""Data collection module for A+ scoring system.

Fetches required data for scoring:
- 24h % change (from Kraken ticker)
- Market Cap and Supply Ratio (from CoinGecko backup)
- 1-hour volume (from Redis bars)
- 50-day SMA volume (from daily OHLC, cached)
"""

import json
import logging
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.ingestor.historical import fetch_kraken_ohlc
from backend.ingestor.symbols import (
    get_symbol_change_24h_pct,
    get_symbol_spread,
    get_symbol_volume,
)
from backend.redis import get_redis_client
from backend.redis.keys import MARKET_OHLCV_STREAM
from backend.screener.coingecko import get_market_data

logger = logging.getLogger(__name__)

# Redis cache keys for 50-day SMA
HOURLY_SMA_50D_CACHE_KEY_PREFIX = "screener:hourly_sma_50d:"
HOURLY_SMA_50D_CACHE_TTL = 3600  # 1 hour TTL (update once per hour)

# Redis cache for 50-day daily SMA (from Kraken REST API)
DAILY_SMA_50D_CACHE_KEY_PREFIX = "screener:daily_sma_50d:"
DAILY_SMA_50D_CACHE_TTL = 86400  # 24-hour TTL (daily bars change slowly)

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"


def _symbol_to_kraken_pair(symbol: str) -> str:
    """Convert 'ETH/USD' -> 'ETHUSD', 'BTC/USD' -> 'XBTUSD'."""
    pair = symbol.replace("/", "")
    if pair.startswith("BTC"):
        pair = "XBT" + pair[3:]
    return pair


def fetch_daily_sma_50d(symbol: str) -> Optional[float]:
    """
    Fetch the 50-day average daily volume for a symbol using Kraken's public OHLC API.

    Uses Redis cache with a 24-hour TTL so each symbol is only fetched once per day.
    This works from day one — no need for weeks of local Redis bar data.

    Returns the average daily volume (in base currency) or None on failure.
    """
    cache_key = f"{DAILY_SMA_50D_CACHE_KEY_PREFIX}{symbol}"

    # Check Redis cache first
    try:
        client = get_redis_client()
        cached = client.get(cache_key)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode()
            return float(cached)
    except Exception as e:
        logger.debug(f"[RVOL] Cache read failed for {symbol}: {e}")

    # Fetch from Kraken public OHLC endpoint (no auth required)
    try:
        pair = _symbol_to_kraken_pair(symbol)
        resp = requests.get(
            KRAKEN_OHLC_URL,
            params={"pair": pair, "interval": 1440},  # 1440 min = daily
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            logger.debug(f"[RVOL] Kraken error for {symbol}: {data['error']}")
            return None

        result = data.get("result", {})
        pair_data: Optional[List] = None
        for key in result:
            if key != "last":
                pair_data = result[key]
                break

        if not pair_data or len(pair_data) < 7:
            logger.debug(f"[RVOL] Insufficient daily bars for {symbol}: {len(pair_data) if pair_data else 0}")
            return None

        # Kraken OHLC format: [time, open, high, low, close, vwap, volume, count]
        # Convert base-currency volume to USD using vwap: usd_vol = vwap × volume
        # This matches get_symbol_volume() which also returns USD volume.
        bars = pair_data[-50:]
        usd_volumes = []
        for row in bars:
            vwap = float(row[5])
            vol = float(row[6])
            if vwap > 0 and vol > 0:
                usd_volumes.append(vwap * vol)

        if len(usd_volumes) < 7:
            logger.debug(f"[RVOL] Not enough valid daily volumes for {symbol}: {len(usd_volumes)}")
            return None

        avg_daily_volume = sum(usd_volumes) / len(usd_volumes)

        # Cache result
        try:
            client = get_redis_client()
            client.setex(cache_key, DAILY_SMA_50D_CACHE_TTL, str(avg_daily_volume))
        except Exception as e:
            logger.debug(f"[RVOL] Cache write failed for {symbol}: {e}")

        logger.debug(f"[RVOL] {symbol}: 50-day daily avg volume = {avg_daily_volume:.2f} ({len(volumes)} bars)")
        return avg_daily_volume

    except Exception as e:
        logger.debug(f"[RVOL] Failed to fetch daily SMA for {symbol}: {e}")
        return None

# Number of 1-hour bars needed for 50-day SMA (50 days * 24 hours = 1200 bars)
REQUIRED_HOURLY_BARS = 1200


def fetch_1h_volume(symbol: str) -> Optional[float]:
    """
    Get current 1-hour volume from Redis bars.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        
    Returns:
        Current 1-hour bar volume or None if unavailable
    """
    try:
        client = get_redis_client()
        stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval="1h")
        
        # Get the most recent 1-hour bar
        messages = client.xrevrange(stream_key, count=1)
        
        if not messages:
            return None
        
        # Extract volume from most recent bar
        msg_id, data = messages[0]
        volume = data.get("volume")
        
        if volume is not None:
            return float(volume)
        
        return None
        
    except Exception as e:
        logger.debug(f"Failed to fetch 1h volume for {symbol}: {e}")
        return None


def get_current_hour_progress() -> float:
    """
    Get progress through current hour (0.0 to 1.0).
    
    Returns:
        Progress value (e.g., 0.25 = 15 minutes into the hour)
    """
    now = datetime.now(timezone.utc)
    minutes_into_hour = now.minute
    progress = minutes_into_hour / 60.0
    
    # Ensure progress is between 0.0 and 1.0
    return max(0.0, min(1.0, progress))


def fetch_50d_sma_volume(symbol: str) -> Optional[float]:
    """
    Calculate or retrieve cached 50-day SMA of hourly volumes.
    
    This is optimized for HP EliteDesk by caching the SMA in Redis
    so it only updates once per hour, while current volume updates every 60 seconds.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        
    Returns:
        50-day SMA of hourly volumes or None if insufficient data
    """
    cache_key = f"{HOURLY_SMA_50D_CACHE_KEY_PREFIX}{symbol}"
    
    # Check cache first
    try:
        client = get_redis_client()
        cached_sma = client.get(cache_key)
        
        if cached_sma:
            if isinstance(cached_sma, bytes):
                cached_sma = cached_sma.decode()
            sma_value = float(cached_sma)
            logger.debug(f"Using cached 50-day SMA for {symbol}: {sma_value}")
            return sma_value
    except Exception as e:
        logger.debug(f"Failed to read SMA cache for {symbol}: {e}")
    
    # Calculate SMA from 1-hour bars
    try:
        client = get_redis_client()
        stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval="1h")
        
        # Fetch last 1200 bars (50 days * 24 hours)
        messages = client.xrevrange(stream_key, count=REQUIRED_HOURLY_BARS)
        
        if len(messages) < REQUIRED_HOURLY_BARS:
            logger.debug(
                f"Insufficient hourly bars for {symbol}: "
                f"{len(messages)} < {REQUIRED_HOURLY_BARS}"
            )
            return None
        
        # Extract volumes from bars (oldest first)
        volumes = []
        for msg_id, data in reversed(messages):
            volume = data.get("volume")
            if volume is not None:
                volumes.append(float(volume))
        
        if len(volumes) < REQUIRED_HOURLY_BARS:
            logger.debug(
                f"Insufficient valid volumes for {symbol}: "
                f"{len(volumes)} < {REQUIRED_HOURLY_BARS}"
            )
            return None
        
        # Calculate SMA
        sma = sum(volumes) / len(volumes)
        
        # Cache the result
        try:
            client.setex(cache_key, HOURLY_SMA_50D_CACHE_TTL, str(sma))
            logger.debug(f"Cached 50-day SMA for {symbol}: {sma}")
        except Exception as e:
            logger.debug(f"Failed to cache SMA for {symbol}: {e}")
        
        return sma
        
    except Exception as e:
        logger.debug(f"Failed to calculate 50-day SMA for {symbol}: {e}")
        return None


def fetch_market_data(symbol: str) -> Dict[str, Any]:
    """
    Fetch all market data required for A+ scoring.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        
    Returns:
        Dictionary with:
        - change_24h_pct: 24h price change percentage
        - market_cap: Market cap in USD (from CoinGecko backup)
        - supply_ratio: Circulating supply / Total supply (from CoinGecko backup)
        - spread_bps: Bid-ask spread in basis points
        - current_1h_volume: Current 1-hour bar volume
        - hourly_sma_50d: 50-day SMA of hourly volumes
        - current_hour_progress: Progress through current hour (0.0 to 1.0)
    """
    result = {
        "change_24h_pct": None,
        "market_cap": None,
        "supply_ratio": None,
        "spread_bps": None,
        "current_1h_volume": None,
        "hourly_sma_50d": None,
        "current_hour_progress": None,
    }
    
    # Fetch 24h change % from Kraken ticker (primary source)
    change_24h_pct = get_symbol_change_24h_pct(symbol)
    result["change_24h_pct"] = change_24h_pct
    
    # Fetch spread from Kraken ticker
    spread_bps = get_symbol_spread(symbol)
    result["spread_bps"] = spread_bps
    
    # Fetch Market Cap and Supply Ratio from CoinGecko (backup)
    coingecko_data = get_market_data(symbol)
    result["market_cap"] = coingecko_data.get("market_cap")
    result["supply_ratio"] = coingecko_data.get("supply_ratio")
    
    # Fetch 1-hour volume
    current_1h_volume = fetch_1h_volume(symbol)
    result["current_1h_volume"] = current_1h_volume
    
    # Fetch or calculate 50-day SMA (cached)
    hourly_sma_50d = fetch_50d_sma_volume(symbol)
    result["hourly_sma_50d"] = hourly_sma_50d
    
    # Fallback: ALWAYS use 24h volume / 24 for RVOL calculation
    # This ensures RVOL can be calculated for ALL pairs with 24h volume data
    try:
        volume_24h = get_symbol_volume(symbol)
        if volume_24h is not None and volume_24h > 0:
            # Always set fallback values for RVOL calculation (overwrite None values)
            # This ensures consistent RVOL calculation across all pairs
            if result["current_1h_volume"] is None:
                result["current_1h_volume"] = volume_24h / 24.0
            if result["hourly_sma_50d"] is None:
                result["hourly_sma_50d"] = volume_24h / 24.0
    except Exception as e:
        logger.debug(f"RVOL fallback failed for {symbol}: {e}")
    
    # Always set current_hour_progress
    result["current_hour_progress"] = get_current_hour_progress()
    
    return result
