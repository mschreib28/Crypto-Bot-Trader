"""Historical OHLCV data fetcher from Kraken REST API."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import aiohttp

from backend.redis import get_redis_client

logger = logging.getLogger(__name__)

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Kraken interval codes (minutes)
KRAKEN_INTERVALS = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def _to_kraken_pair(symbol: str) -> str:
    """Convert symbol format to Kraken pair format."""
    # ETH/USD -> ETHUSD, BTC/USD -> XBTUSD
    pair = symbol.replace("/", "")
    if pair.startswith("BTC"):
        pair = pair.replace("BTC", "XBT", 1)
    return pair


async def fetch_kraken_ohlc(
    symbol: str,
    interval: str,
    max_bars: int = 720,
) -> List[Dict[str, Any]]:
    """
    Fetch historical OHLC data from Kraken REST API.

    Args:
        symbol: Trading pair (e.g., "ETH/USD")
        interval: Bar interval (e.g., "5m", "1h", "4h")
        max_bars: Maximum bars to fetch (Kraken limit is 720)

    Returns:
        List of bar dictionaries with OHLCV data
    """
    kraken_pair = _to_kraken_pair(symbol)
    kraken_interval = KRAKEN_INTERVALS.get(interval)

    if kraken_interval is None:
        logger.warning(f"Unsupported interval {interval} for historical fetch")
        return []

    params = {
        "pair": kraken_pair,
        "interval": kraken_interval,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                KRAKEN_OHLC_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Kraken OHLC API error: {resp.status}")
                    return []

                data = await resp.json()

                if data.get("error"):
                    logger.error(f"Kraken API error: {data['error']}")
                    return []

                result = data.get("result", {})
                # Find the pair data (key varies by pair)
                pair_data = None
                for key in result:
                    if key != "last":
                        pair_data = result[key]
                        break

                if not pair_data:
                    logger.warning(f"No OHLC data for {symbol}")
                    return []

                # Convert Kraken format to our bar format
                # Kraken: [time, open, high, low, close, vwap, volume, count]
                bars = []
                for row in pair_data[-max_bars:]:  # Take last max_bars
                    timestamp = datetime.fromtimestamp(row[0], tz=timezone.utc)
                    bar = {
                        "symbol": symbol,
                        "interval": interval,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[6]),
                        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    bars.append(bar)

                logger.info(f"Fetched {len(bars)} historical {interval} bars for {symbol}")
                return bars

    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching historical data for {symbol}")
        return []
    except Exception as e:
        logger.error(f"Error fetching historical data for {symbol}: {e}")
        return []


async def backfill_historical_bars(
    symbol: str,
    interval: str,
    max_bars: int = 720,
) -> int:
    """
    Fetch historical bars and store in Redis stream.

    Args:
        symbol: Trading pair
        interval: Bar interval
        max_bars: Maximum bars to store

    Returns:
        Number of bars stored
    """
    bars = await fetch_kraken_ohlc(symbol, interval, max_bars)

    if not bars:
        return 0

    client = get_redis_client()
    stream_key = f"market:ohlcv:{symbol}:{interval}"

    # Store bars in Redis stream with MAXLEN
    stored = 0
    for bar in bars:
        try:
            client.xadd(
                stream_key,
                bar,
                maxlen=max_bars,
                approximate=True,
            )
            stored += 1
        except Exception as e:
            logger.error(f"Error storing bar in Redis: {e}")

    logger.info(f"Stored {stored} historical bars for {symbol} at {interval}")
    return stored
