"""Dynamic symbol fetching from Kraken exchange."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from backend.execution.auth import get_auth_headers, generate_nonce, KRAKEN_API_URL
from backend.ingestor.config import (
    get_symbol_limit,
    get_max_total_symbols,
    get_min_rvol_threshold,
    get_rvol_candidate_limit,
    get_universe_add_threshold_rank,
    get_universe_add_confirmations,
    get_universe_drop_threshold_rank,
    get_universe_drop_confirmations,
    get_min_24h_volume_usd,
    get_volume_collapse_threshold,
)
from backend.redis import get_redis_client
from backend.redis.keys import (
    SYMBOL_VOLUME_KEY,
    MARKET_OHLCV_STREAM,
    FAILED_SYMBOLS_KEY,
    FAILED_SYMBOLS_TTL,
    LIVE_UNIVERSE_KEY,
)

logger = logging.getLogger(__name__)

# Kraken internal symbol name mappings
# Kraken uses X prefix for crypto and Z prefix for fiat, plus various suffixes
KRAKEN_TO_STANDARD = {
    # Major cryptos with X prefix and Z suffix
    "XETHZ/USD": "ETH/USD",
    "XXBTZ/USD": "BTC/USD",  # Kraken's internal BTC symbol
    "XXRPZ/USD": "XRP/USD",
    "XXMRZ/USD": "XMR/USD",
    "XLTCZ/USD": "LTC/USD",
    "XZECZ/USD": "ZEC/USD",
    "XXLMZ/USD": "XLM/USD",
    "XETCZ/USD": "ETC/USD",
    "XREPZ/USD": "REP/USD",
    # Additional common pairs
    "XETH/USD": "ETH/USD",
    "XXBT/USD": "BTC/USD",
    "XXRP/USD": "XRP/USD",
    "XXMR/USD": "XMR/USD",
    "XLTC/USD": "LTC/USD",
    "XZEC/USD": "ZEC/USD",
    "XXLM/USD": "XLM/USD",
    "XETC/USD": "ETC/USD",
    "XREP/USD": "REP/USD",
}


def normalize_symbol(symbol: str) -> str:
    """
    Convert Kraken internal symbol to standard format.
    
    Handles various Kraken naming conventions:
    - X prefix for crypto assets (XETH -> ETH)
    - Z suffix for some pairs (XETHZ -> ETH)
    - XBT -> BTC conversion
    
    Args:
        symbol: Symbol in any format (e.g., "XETHZ/USD", "XBT/USD", "ETH/USD")
        
    Returns:
        Normalized symbol (e.g., "ETH/USD", "BTC/USD")
    """
    # Check explicit mapping first
    if symbol in KRAKEN_TO_STANDARD:
        return KRAKEN_TO_STANDARD[symbol]
    
    # Handle XBT -> BTC conversion
    if "XBT" in symbol:
        symbol = symbol.replace("XBT", "BTC")
    
    # If already in standard format, return as-is
    if "/" in symbol:
        parts = symbol.split("/")
        if len(parts) == 2:
            base, quote = parts
            # Strip X prefix and Z suffix from base
            if base.startswith("X") and len(base) > 3:
                base = base[1:]
            if base.endswith("Z") and len(base) > 3:
                base = base[:-1]
            return f"{base}/{quote}"
    
    return symbol


# Kraken REST API endpoints
KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

# Timeout for API requests (seconds)
REQUEST_TIMEOUT = 30

# OHLC interval for daily candles (in minutes)
OHLC_DAILY_INTERVAL = 1440

# Number of days to use for average volume calculation
RVOL_LOOKBACK_DAYS = 20

# Rate limit delay between OHLC API calls (seconds)
OHLC_RATE_LIMIT_DELAY = 0.2

# Assets to exclude from owned symbols (stablecoins, fiat that don't form /USD pairs)
EXCLUDED_ASSETS = {"USD", "ZUSD", "USDT", "USDC", "DAI", "EUR", "ZEUR", "GBP", "ZGBP", "CAD", "ZCAD", "CHF"}

# Stablecoin pairs to exclude from trading (these are pegged to ~$1 and shouldn't be traded)
# Common stablecoins: USDC, USDT, DAI, TUSD, BUSD, PAX, GUSD, HUSD, SUSD, etc.
STABLECOIN_BASE_ASSETS = {
    "USDC", "USDT", "DAI", "TUSD", "BUSD", "PAX", "GUSD", "HUSD", "SUSD",
    "USD1", "USDP", "FRAX", "LUSD", "MIM", "FEI", "TRIBE", "UST", "LUNA"
}

def is_stablecoin_pair(symbol: str) -> bool:
    """
    Check if a symbol is a stablecoin pair (e.g., USDC/USD, USDT/USD).
    
    Args:
        symbol: Trading pair symbol (e.g., "USDC/USD", "BTC/USD")
        
    Returns:
        True if the symbol is a stablecoin pair, False otherwise
    """
    if "/" not in symbol:
        return False
    
    base_asset = symbol.split("/")[0].upper()
    return base_asset in STABLECOIN_BASE_ASSETS

# Stablecoin pairs to exclude from trading (these are pegged to USD and shouldn't be traded)
EXCLUDED_STABLECOIN_PAIRS = {"USDC/USD", "USDT/USD", "DAI/USD", "TUSD/USD", "BUSD/USD", "PAXG/USD"}  # PAXG is gold-backed, not a trading pair


async def fetch_usd_pairs() -> List[str]:
    """
    Fetch all USD trading pairs from Kraken.
    
    Calls the Kraken AssetPairs REST endpoint and filters for pairs
    with USD as the quote currency that are currently tradeable.
    
    Returns:
        List of trading pair symbols in format ["ETH/USD", "BTC/USD", "SOL/USD", ...]
        
    Raises:
        requests.RequestException: If API request fails
        ValueError: If API response is malformed
    """
    logger.info("Fetching USD trading pairs from Kraken...")
    
    try:
        response = requests.get(KRAKEN_ASSET_PAIRS_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        # Check for API errors
        if data.get("error") and len(data["error"]) > 0:
            error_msg = ", ".join(data["error"])
            raise ValueError(f"Kraken API error: {error_msg}")
        
        result = data.get("result", {})
        if not result:
            raise ValueError("Empty result from Kraken AssetPairs API")
        
        usd_pairs = []
        
        for pair_name, pair_info in result.items():
            # Skip darkpool pairs (they contain ".d" suffix)
            if ".d" in pair_name:
                continue
            
            # Get the quote currency
            quote = pair_info.get("quote", "")
            
            # Check if USD quote (Kraken uses "ZUSD" or "USD" for USD)
            if quote not in ("ZUSD", "USD"):
                continue
            
            # Check if pair is tradeable
            status = pair_info.get("status", "")
            if status and status != "online":
                continue
            
            # Get the websocket name (preferred) or construct from wsname/altname
            wsname = pair_info.get("wsname", "")
            if wsname:
                # wsname is already in format like "XBT/USD"
                # Convert XBT to BTC for consistency
                normalized = _normalize_pair_name(wsname)
                # Skip stablecoin pairs
                if not is_stablecoin_pair(normalized):
                    usd_pairs.append(normalized)
            else:
                # Fallback: use altname if wsname not available
                altname = pair_info.get("altname", pair_name)
                # Try to parse altname (e.g., "ETHUSD" -> "ETH/USD")
                if altname.endswith("USD"):
                    base = altname[:-3]
                    normalized = _normalize_pair_name(f"{base}/USD")
                    # Skip stablecoin pairs
                    if not is_stablecoin_pair(normalized):
                        usd_pairs.append(normalized)
        
        # Sort for consistent ordering
        usd_pairs.sort()
        
        logger.info(f"Found {len(usd_pairs)} USD trading pairs from Kraken")
        logger.debug(f"USD pairs: {usd_pairs[:10]}... (showing first 10)")
        
        return usd_pairs
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch asset pairs from Kraken: {e}")
        raise
    except (KeyError, TypeError) as e:
        logger.error(f"Failed to parse Kraken AssetPairs response: {e}")
        raise ValueError(f"Malformed API response: {e}")


def fetch_top_usd_pairs_by_volume(limit: Optional[int] = None) -> List[str]:
    """
    Fetch top USD trading pairs from Kraken sorted by 24h volume.
    
    Also stores volume data in Redis for use by screener.
    
    Args:
        limit: Maximum number of pairs to return (default: SYMBOL_LIMIT from config)
        
    Returns:
        List of trading pair symbols in format ["BTC/USD", "ETH/USD", ...]
    """
    if limit is None:
        limit = get_symbol_limit()
    
    logger.info(f"Fetching top {limit} USD pairs by volume")
    
    # Get all ticker data
    response = requests.get(KRAKEN_TICKER_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    
    if data.get("error"):
        raise ValueError(f"Kraken API error: {data['error']}")
    
    result = data.get("result", {})
    
    # Filter for USD pairs and extract volume
    usd_pairs: List[Tuple[str, float]] = []
    volume_data: Dict[str, Dict[str, float]] = {}
    
    for pair_name, ticker_data in result.items():
        # Skip non-USD pairs
        if not pair_name.endswith("USD"):
            continue
        
        # Get 24h volume (field 'v' is [today, last24h]) - this is in base currency units
        volume_24h_base = float(ticker_data.get("v", [0, 0])[1])
        
        # Get last trade price to convert volume to USD
        last_price = float(ticker_data.get("c", [0, 0])[0])  # 'c' is [price, lot volume]
        volume_24h_usd = volume_24h_base * last_price
        
        # Get 24h change percentage
        # Kraken REST API ticker fields:
        # 'p' = volume weighted average price [today, last24h] - p[1] is 24h VWAP
        # 'l' = low array [today, last24h] - l[1] is 24h low
        # 'h' = high array [today, last24h] - h[1] is 24h high
        # 'o' = today's opening price (UTC midnight)
        # 'c' = last trade closed [price, lot volume] - c[0] is current price
        # 
        # Calculate 24h change using best available method:
        # Method 1 (preferred): Use 24h VWAP as proxy for "price 24h ago" (most accurate)
        # Method 2 (fallback): Use midpoint of 24h high/low range
        # Method 3 (last resort): Use today's open vs current price
        change_24h_pct = None
        try:
            # Method 1: Use 24h VWAP (volume-weighted average, best proxy for 24h price)
            vwap_24h = ticker_data.get("p")
            if vwap_24h and isinstance(vwap_24h, list) and len(vwap_24h) >= 2:
                vwap_24h_price = float(vwap_24h[1])  # 24h VWAP (second element)
                if vwap_24h_price > 0:
                    change_24h_pct = ((last_price - vwap_24h_price) / vwap_24h_price) * 100
                    logger.debug(f"{pair_name}: 24h change from VWAP: {change_24h_pct:.2f}% (price={last_price}, vwap_24h={vwap_24h_price})")
            # Method 2: Use midpoint of 24h high/low range (more accurate than open price)
            elif 'h' in ticker_data and 'l' in ticker_data:
                high_24h = ticker_data.get("h")
                low_24h = ticker_data.get("l")
                if isinstance(high_24h, list) and len(high_24h) >= 2 and isinstance(low_24h, list) and len(low_24h) >= 2:
                    high_24h_price = float(high_24h[1])  # 24h high
                    low_24h_price = float(low_24h[1])    # 24h low
                    if low_24h_price > 0:
                        # Use midpoint as reference
                        midpoint_24h = (high_24h_price + low_24h_price) / 2
                        change_24h_pct = ((last_price - midpoint_24h) / midpoint_24h) * 100
                        logger.debug(f"{pair_name}: 24h change from midpoint: {change_24h_pct:.2f}% (price={last_price}, midpoint={midpoint_24h})")
            # Method 3: Fallback to today's open vs current price (daily change approximation)
            elif 'o' in ticker_data:
                open_price = ticker_data.get("o")
                if isinstance(open_price, list) and len(open_price) > 0:
                    open_price = float(open_price[0])
                else:
                    open_price = float(open_price) if open_price else None
                if open_price and open_price > 0:
                    change_24h_pct = ((last_price - open_price) / open_price) * 100
                    logger.debug(f"{pair_name}: Daily change from open: {change_24h_pct:.2f}% (price={last_price}, open={open_price})")
        except (ValueError, TypeError, IndexError) as e:
            logger.debug(f"Could not calculate 24h change for {pair_name}: {e}")
            change_24h_pct = None
        
        # Add slash if needed: ETHUSD -> ETH/USD, XETHZUSD -> XETHZ/USD
        if "/" not in pair_name and pair_name.endswith("USD"):
            pair_name = pair_name[:-3] + "/USD"
        
        # Normalize to standard format (XETHZ/USD -> ETH/USD, XBT/USD -> BTC/USD)
        normalized = normalize_symbol(pair_name)
        
        # Skip stablecoin pairs (USDC/USD, USDT/USD, etc.) - these are pegged to ~$1 and shouldn't be traded
        if is_stablecoin_pair(normalized):
            logger.debug(f"Skipping stablecoin pair: {normalized}")
            continue
        
        usd_pairs.append((normalized, volume_24h_usd))
        volume_data[normalized] = {
            "volume_24h": volume_24h_usd,
            "change_24h_pct": change_24h_pct,
        }
    
    # Sort by volume descending and take top N
    usd_pairs.sort(key=lambda x: x[1], reverse=True)
    top_pairs = [pair for pair, _ in usd_pairs[:limit]]
    
    # Store volume data in Redis for all pairs (not just top N)
    _store_volume_data(volume_data)
    
    logger.info(f"Found {len(top_pairs)} USD pairs. Top 5: {top_pairs[:5]}")
    return top_pairs


def _store_volume_data(volume_data: Dict[str, Dict[str, float]]) -> None:
    """
    Store symbol volume and 24h change data in Redis.
    
    Args:
        volume_data: Dict mapping symbol to dict with "volume_24h" and optionally "change_24h_pct"
    """
    try:
        client = get_redis_client()
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Store as hash: symbol -> JSON with volume, change_24h_pct, and timestamp
        pipeline = client.pipeline()
        for symbol, data_dict in volume_data.items():
            # Handle both old format (just float) and new format (dict)
            if isinstance(data_dict, dict):
                volume_24h = data_dict.get("volume_24h", 0.0)
                change_24h_pct = data_dict.get("change_24h_pct")
            else:
                # Legacy format: just volume as float
                volume_24h = float(data_dict) if data_dict else 0.0
                change_24h_pct = None
            
            store_data = {
                "volume_24h": volume_24h,
                "change_24h_pct": change_24h_pct,
                "updated_at": timestamp,
            }
            data_json = json.dumps(store_data)
            pipeline.hset(SYMBOL_VOLUME_KEY, symbol, data_json)
        pipeline.execute()
        
        logger.info(f"Stored volume and change data for {len(volume_data)} symbols in Redis")
    except Exception as e:
        logger.warning(f"Failed to store volume data in Redis: {e}", exc_info=True)


def get_symbol_volume(symbol: str) -> Optional[float]:
    """
    Get 24h USD volume for a symbol from Redis.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        
    Returns:
        24h USD volume or None if not available
    """
    try:
        client = get_redis_client()
        data = client.hget(SYMBOL_VOLUME_KEY, symbol)
        if data:
            parsed = json.loads(data)
            return parsed.get("volume_24h")
    except Exception as e:
        logger.debug(f"Failed to get volume for {symbol}: {e}")
    return None


def get_last_universe_refresh_time() -> Optional[datetime]:
    """
    Get timestamp of last universe refresh from Redis.
    
    Returns:
        Datetime of last refresh, or None if never refreshed
    """
    try:
        client = get_redis_client()
        from backend.redis.keys import UNIVERSE_LAST_REFRESH_KEY
        timestamp_str = client.get(UNIVERSE_LAST_REFRESH_KEY)
        if timestamp_str:
            if isinstance(timestamp_str, bytes):
                timestamp_str = timestamp_str.decode()
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception as e:
        logger.debug(f"Failed to get last universe refresh time: {e}")
    return None


def get_last_rvol_refresh_time() -> Optional[datetime]:
    """
    Get timestamp of last RVOL refresh from Redis.
    
    Returns:
        Datetime of last refresh, or None if never refreshed
    """
    try:
        client = get_redis_client()
        from backend.redis.keys import RVOL_LAST_REFRESH_KEY
        timestamp_str = client.get(RVOL_LAST_REFRESH_KEY)
        if timestamp_str:
            if isinstance(timestamp_str, bytes):
                timestamp_str = timestamp_str.decode()
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception as e:
        logger.debug(f"Failed to get last RVOL refresh time: {e}")
    return None


def mark_universe_refresh_time() -> None:
    """Mark current time as universe refresh timestamp in Redis."""
    try:
        client = get_redis_client()
        from backend.redis.keys import UNIVERSE_LAST_REFRESH_KEY
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        client.set(UNIVERSE_LAST_REFRESH_KEY, timestamp)
    except Exception as e:
        logger.debug(f"Failed to mark universe refresh time: {e}")


def mark_rvol_refresh_time() -> None:
    """Mark current time as RVOL refresh timestamp in Redis."""
    try:
        client = get_redis_client()
        from backend.redis.keys import RVOL_LAST_REFRESH_KEY
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        client.set(RVOL_LAST_REFRESH_KEY, timestamp)
    except Exception as e:
        logger.debug(f"Failed to mark RVOL refresh time: {e}")


def refresh_ticker_data() -> None:
    """
    Fast refresh of ticker data (24h change, volume) without RVOL calculation.
    
    This is a lightweight operation that fetches ticker data for all USD pairs
    and updates Redis with volume and 24h change percentages. Called every 15 minutes
    to keep universe selection data fresh without the overhead of RVOL calculation.
    
    Does NOT change the active symbol universe - that's handled separately with hysteresis.
    """
    logger.info("Refreshing ticker data (24h change, volume)...")
    
    try:
        # Get all ticker data in single REST API call
        response = requests.get(KRAKEN_TICKER_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        if data.get("error"):
            logger.warning(f"Kraken API error during ticker refresh: {data['error']}")
            return
        
        result = data.get("result", {})
        volume_data: Dict[str, Dict[str, float]] = {}
        
        for pair_name, ticker_data in result.items():
            # Skip non-USD pairs
            if not pair_name.endswith("USD"):
                continue
            
            # Get 24h volume (field 'v' is [today, last24h]) - this is in base currency units
            volume_24h_base = float(ticker_data.get("v", [0, 0])[1])
            
            # Get last trade price to convert volume to USD
            last_price = float(ticker_data.get("c", [0, 0])[0])  # 'c' is [price, lot volume]
            volume_24h_usd = volume_24h_base * last_price
            
            # Calculate 24h change percentage (same logic as fetch_top_usd_pairs_by_volume)
            change_24h_pct = None
            try:
                # Method 1: Use 24h VWAP (volume-weighted average, best proxy for 24h price)
                vwap_24h = ticker_data.get("p")
                if vwap_24h and isinstance(vwap_24h, list) and len(vwap_24h) >= 2:
                    vwap_24h_price = float(vwap_24h[1])  # 24h VWAP (second element)
                    if vwap_24h_price > 0:
                        change_24h_pct = ((last_price - vwap_24h_price) / vwap_24h_price) * 100
                # Method 2: Use midpoint of 24h high/low range
                elif 'h' in ticker_data and 'l' in ticker_data:
                    high_24h = ticker_data.get("h")
                    low_24h = ticker_data.get("l")
                    if isinstance(high_24h, list) and len(high_24h) >= 2 and isinstance(low_24h, list) and len(low_24h) >= 2:
                        high_24h_price = float(high_24h[1])  # 24h high
                        low_24h_price = float(low_24h[1])    # 24h low
                        if low_24h_price > 0:
                            midpoint_24h = (high_24h_price + low_24h_price) / 2
                            change_24h_pct = ((last_price - midpoint_24h) / midpoint_24h) * 100
                # Method 3: Fallback to today's open vs current price
                elif 'o' in ticker_data:
                    open_price = ticker_data.get("o")
                    if isinstance(open_price, list) and len(open_price) > 0:
                        open_price = float(open_price[0])
                    else:
                        open_price = float(open_price) if open_price else None
                    if open_price and open_price > 0:
                        change_24h_pct = ((last_price - open_price) / open_price) * 100
            except (ValueError, TypeError, IndexError):
                change_24h_pct = None
            
            # Normalize symbol
            if "/" not in pair_name and pair_name.endswith("USD"):
                pair_name = pair_name[:-3] + "/USD"
            
            normalized = normalize_symbol(pair_name)
            
            # Skip stablecoin pairs
            if is_stablecoin_pair(normalized):
                continue
            
            volume_data[normalized] = {
                "volume_24h": volume_24h_usd,
                "change_24h_pct": change_24h_pct,
            }
        
        # Store all ticker data in Redis
        if volume_data:
            _store_volume_data(volume_data)
            logger.info(f"Refreshed ticker data for {len(volume_data)} symbols")
        
    except Exception as e:
        logger.error(f"Error refreshing ticker data: {e}", exc_info=True)


def get_symbol_change_24h_pct(symbol: str) -> Optional[float]:
    """
    Get 24h change percentage for a symbol from Redis.
    
    Args:
        symbol: Trading pair (e.g., "BTC/USD")
        
    Returns:
        24h change percentage or None if not available
    """
    try:
        client = get_redis_client()
        data = client.hget(SYMBOL_VOLUME_KEY, symbol)
        if data:
            parsed = json.loads(data)
            # Handle nested structure (legacy bug) and flat structure (correct)
            change_pct = parsed.get("change_24h_pct")
            if change_pct is None:
                # Check if volume_24h is a dict (nested structure bug)
                vol_data = parsed.get("volume_24h")
                if isinstance(vol_data, dict):
                    change_pct = vol_data.get("change_24h_pct")
            return float(change_pct) if change_pct is not None else None
    except Exception as e:
        logger.debug(f"Failed to get 24h change % for {symbol}: {e}")
    return None


def get_all_symbol_volumes() -> Dict[str, float]:
    """
    Get 24h USD volumes for all symbols from Redis.
    
    Returns:
        Dict mapping symbol to 24h USD volume
    """
    try:
        client = get_redis_client()
        all_data = client.hgetall(SYMBOL_VOLUME_KEY)
        volumes = {}
        for symbol, data in all_data.items():
            try:
                parsed = json.loads(data)
                volumes[symbol] = parsed.get("volume_24h", 0.0)
            except (json.JSONDecodeError, TypeError):
                continue
        return volumes
    except Exception as e:
        logger.warning(f"Failed to get all volumes from Redis: {e}")
        return {}


def _normalize_pair_name(pair: str) -> str:
    """
    Normalize pair name to standard format.
    
    Converts Kraken-specific symbols to standard format:
    - XBT -> BTC
    - X prefix removal (XETH -> ETH)
    - Z suffix removal (XETHZ -> ETH)
    
    Args:
        pair: Pair name in Kraken format (e.g., "XBT/USD", "XETHZ/USD")
        
    Returns:
        Normalized pair name (e.g., "BTC/USD", "ETH/USD")
    """
    return normalize_symbol(pair)


def fetch_usd_pairs_sync() -> List[str]:
    """
    Synchronous version of fetch_usd_pairs for use in non-async contexts.
    
    Returns:
        List of trading pair symbols in format ["ETH/USD", "BTC/USD", "SOL/USD", ...]
    """
    import asyncio
    return asyncio.get_event_loop().run_until_complete(fetch_usd_pairs())


def get_owned_symbols() -> List[str]:
    """
    Fetch symbols for assets owned in Kraken account balance.
    
    Calls the Kraken Balance API to get non-zero holdings and converts
    them to USD trading pairs.
    
    Returns:
        List of trading pair symbols for owned assets (e.g., ["ETH/USD", "SOL/USD"])
        Returns empty list if API call fails or no credentials configured.
    """
    logger.info("Fetching owned symbols from Kraken balance...")
    
    try:
        # Prepare request
        uri_path = "/0/private/Balance"
        post_data = {"nonce": generate_nonce()}
        
        try:
            headers = get_auth_headers(uri_path, post_data)
        except ValueError as e:
            logger.warning(f"Kraken API credentials not configured: {e}")
            return []
        
        url = f"{KRAKEN_API_URL}{uri_path}"
        
        response = requests.post(url, headers=headers, data=post_data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        if data.get("error"):
            errors = data["error"]
            if errors:
                logger.warning(f"Kraken Balance API error: {errors}")
                return []
        
        result = data.get("result", {})
        if not result:
            logger.info("No balance data returned from Kraken")
            return []
        
        owned_symbols: List[str] = []
        
        for asset, balance_str in result.items():
            try:
                balance = float(balance_str)
            except (ValueError, TypeError):
                continue
            
            # Skip zero or near-zero balances
            if balance <= 0.0:
                continue
            
            # Normalize asset name (remove leading X/Z for crypto/fiat)
            symbol = asset
            if len(asset) == 4 and asset[0] in ("X", "Z"):
                symbol = asset[1:]
            
            # Convert XBT to BTC
            if symbol == "XBT":
                symbol = "BTC"
            
            # Skip excluded assets (stablecoins, fiat)
            if asset in EXCLUDED_ASSETS or symbol in EXCLUDED_ASSETS:
                continue
            
            # Form USD pair
            pair = f"{symbol}/USD"
            
            # Double-check: skip stablecoin pairs (even if not in EXCLUDED_ASSETS)
            if is_stablecoin_pair(pair):
                logger.debug(f"Skipping stablecoin pair from owned symbols: {pair}")
                continue
            
            owned_symbols.append(pair)
        
        logger.info(f"Found {len(owned_symbols)} owned symbols: {owned_symbols}")
        return owned_symbols
        
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch balance from Kraken: {e}")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error fetching owned symbols: {e}")
        return []


def get_dynamic_symbols(limit: Optional[int] = None) -> List[str]:
    """
    Get dynamic symbol list: top N USD pairs by volume + owned symbols.
    
    Fetches top USD pairs by 24h volume and merges with symbols from
    account holdings. Deduplicates the result and caps at MAX_TOTAL_SYMBOLS.
    
    Args:
        limit: Number of top pairs to fetch by volume (default: SYMBOL_LIMIT from config)
        
    Returns:
        Merged and deduplicated list of symbols (capped at MAX_TOTAL_SYMBOLS)
    """
    if limit is None:
        limit = get_symbol_limit()
    
    max_total = get_max_total_symbols()
    
    # Fetch top pairs by volume
    try:
        top_pairs = fetch_top_usd_pairs_by_volume(limit=limit)
    except Exception as e:
        logger.error(f"Failed to fetch top pairs: {e}")
        top_pairs = []
    
    # Fetch owned symbols
    owned_symbols = get_owned_symbols()
    
    # Merge and deduplicate (preserve order: top pairs first, then owned)
    seen: Set[str] = set()
    merged: List[str] = []
    
    for symbol in top_pairs:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
    
    # Track which owned symbols are being added
    added_held: List[str] = []
    for symbol in owned_symbols:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
            added_held.append(symbol)
    
    # Log held symbols being added
    if added_held:
        logger.info(f"Adding {len(added_held)} held symbols: {added_held}")
    
    # Cap total at max for WebSocket stability
    if len(merged) > max_total:
        logger.warning(f"Symbol list ({len(merged)}) exceeds max ({max_total}), truncating")
        merged = merged[:max_total]
    
    logger.info(f"Final symbol list ({len(merged)}): {merged}")
    return merged


def _fetch_ohlc_volume(pair: str) -> Optional[float]:
    """
    Fetch average daily volume for a pair from OHLC data.
    
    Args:
        pair: Trading pair in Kraken format (e.g., "XBTUSD", "ETHUSD")
        
    Returns:
        Average daily volume in USD over last 20 days, or None if unavailable
    """
    try:
        response = requests.get(
            KRAKEN_OHLC_URL,
            params={"pair": pair, "interval": OHLC_DAILY_INTERVAL},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("error"):
            return None
        
        result = data.get("result", {})
        # Result contains pair key (may differ from input) and "last" timestamp
        ohlc_data = None
        for key, value in result.items():
            if key != "last" and isinstance(value, list):
                ohlc_data = value
                break
        
        if not ohlc_data or len(ohlc_data) < RVOL_LOOKBACK_DAYS:
            return None
        
        # OHLC format: [time, open, high, low, close, vwap, volume, count]
        # Use most recent RVOL_LOOKBACK_DAYS candles (excluding today's incomplete candle)
        recent_candles = ohlc_data[-(RVOL_LOOKBACK_DAYS + 1):-1]
        if len(recent_candles) < RVOL_LOOKBACK_DAYS:
            recent_candles = ohlc_data[-RVOL_LOOKBACK_DAYS:]
        
        # Calculate average volume (volume is index 6, in base currency)
        # We also need price to convert to USD
        total_volume_usd = 0.0
        for candle in recent_candles:
            volume = float(candle[6])  # Volume in base currency
            close_price = float(candle[4])  # Close price
            total_volume_usd += volume * close_price
        
        avg_volume = total_volume_usd / len(recent_candles)
        return avg_volume
        
    except Exception as e:
        logger.debug(f"Failed to fetch OHLC for {pair}: {e}")
        return None


def _get_kraken_pair_name(symbol: str) -> str:
    """
    Convert standard symbol to Kraken API format.
    
    Args:
        symbol: Standard format (e.g., "BTC/USD", "ETH/USD")
        
    Returns:
        Kraken format (e.g., "XBTUSD", "ETHUSD")
    """
    # Remove slash
    pair = symbol.replace("/", "")
    # Convert BTC to XBT (Kraken's internal name)
    pair = pair.replace("BTC", "XBT")
    return pair


def fetch_symbols_by_rvol(
    limit: Optional[int] = None,
    min_rvol: Optional[float] = None,
) -> List[Tuple[str, float]]:
    """
    Fetch USD trading pairs sorted by RVOL (Relative Volume).
    
    RVOL = (24h_volume / avg_20d_volume) * 100
    
    Higher RVOL indicates unusual volume activity, potentially signaling
    increased interest or momentum in the asset.
    
    Args:
        limit: Maximum number of pairs to return (default: SYMBOL_LIMIT)
        min_rvol: Minimum RVOL threshold (default: MIN_RVOL_THRESHOLD)
        
    Returns:
        List of (symbol, rvol) tuples sorted by RVOL descending
    """
    if limit is None:
        limit = get_symbol_limit()
    if min_rvol is None:
        min_rvol = get_min_rvol_threshold()
    
    candidate_limit = get_rvol_candidate_limit()
    
    logger.info(f"Fetching symbols by RVOL (limit={limit}, min_rvol={min_rvol}%)")
    
    # Step 1: Get ticker data for all pairs (single API call)
    try:
        response = requests.get(KRAKEN_TICKER_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        if data.get("error"):
            raise ValueError(f"Kraken API error: {data['error']}")
        
        result = data.get("result", {})
    except Exception as e:
        logger.error(f"Failed to fetch ticker data: {e}")
        return []
    
    # Step 2: Filter USD pairs and get 24h volume + change data
    usd_pairs: List[Tuple[str, str, float]] = []  # (normalized, kraken_name, volume_24h_usd)
    volume_change_data: Dict[str, Dict[str, float]] = {}  # Store volume and change data for Redis
    
    for pair_name, ticker_data in result.items():
        if not pair_name.endswith("USD"):
            continue
        
        # Get 24h volume in base currency
        volume_24h_base = float(ticker_data.get("v", [0, 0])[1])
        last_price = float(ticker_data.get("c", [0, 0])[0])
        volume_24h_usd = volume_24h_base * last_price
        
        # Get 24h change percentage
        # Use same calculation method as fetch_top_usd_pairs_by_volume for consistency
        change_24h_pct = None
        try:
            # Method 1: Use 24h VWAP (volume-weighted average, best proxy for 24h price)
            vwap_24h = ticker_data.get("p")
            if vwap_24h and isinstance(vwap_24h, list) and len(vwap_24h) >= 2:
                vwap_24h_price = float(vwap_24h[1])  # 24h VWAP (second element)
                if vwap_24h_price > 0:
                    change_24h_pct = ((last_price - vwap_24h_price) / vwap_24h_price) * 100
            # Method 2: Use midpoint of 24h high/low range (more accurate than open price)
            elif 'h' in ticker_data and 'l' in ticker_data:
                high_24h = ticker_data.get("h")
                low_24h = ticker_data.get("l")
                if isinstance(high_24h, list) and len(high_24h) >= 2 and isinstance(low_24h, list) and len(low_24h) >= 2:
                    high_24h_price = float(high_24h[1])  # 24h high
                    low_24h_price = float(low_24h[1])    # 24h low
                    if low_24h_price > 0:
                        # Use midpoint as reference
                        midpoint_24h = (high_24h_price + low_24h_price) / 2
                        change_24h_pct = ((last_price - midpoint_24h) / midpoint_24h) * 100
            # Method 3: Fallback to today's open vs current price (daily change approximation)
            elif 'o' in ticker_data:
                open_price = ticker_data.get("o")
                if isinstance(open_price, list) and len(open_price) > 0:
                    open_price = float(open_price[0])
                else:
                    open_price = float(open_price) if open_price else None
                if open_price and open_price > 0:
                    change_24h_pct = ((last_price - open_price) / open_price) * 100
        except (ValueError, TypeError, IndexError):
            change_24h_pct = None
        
        # Normalize symbol
        if "/" not in pair_name and pair_name.endswith("USD"):
            pair_with_slash = pair_name[:-3] + "/USD"
        else:
            pair_with_slash = pair_name
        
        normalized = normalize_symbol(pair_with_slash)
        
        # Skip stablecoin pairs (USDC/USD, USDT/USD, etc.) - these are pegged to ~$1 and shouldn't be traded
        if is_stablecoin_pair(normalized):
            logger.debug(f"Skipping stablecoin pair: {normalized}")
            continue
        
        usd_pairs.append((normalized, pair_name, volume_24h_usd))
        # Store volume and change data for Redis
        volume_change_data[normalized] = {
            "volume_24h": volume_24h_usd,
            "change_24h_pct": change_24h_pct,
        }
    
    # Step 3: Sort by volume and take top candidates for RVOL calculation
    usd_pairs.sort(key=lambda x: x[2], reverse=True)
    candidates = usd_pairs[:candidate_limit]
    
    logger.info(f"Evaluating RVOL for top {len(candidates)} volume pairs")
    
    # Step 4: Fetch OHLC and calculate RVOL for each candidate
    rvol_results: List[Tuple[str, float]] = []
    
    for normalized, kraken_name, volume_24h_usd in candidates:
        # Rate limit to avoid API throttling
        time.sleep(OHLC_RATE_LIMIT_DELAY)
        
        avg_volume = _fetch_ohlc_volume(kraken_name)
        
        if avg_volume is None or avg_volume <= 0:
            logger.debug(f"Skipping {normalized}: no historical volume data")
            continue
        
        # Calculate RVOL: (24h_volume / avg_20d_volume) * 100
        rvol = (volume_24h_usd / avg_volume) * 100
        
        if rvol >= min_rvol:
            rvol_results.append((normalized, rvol))
            logger.debug(f"{normalized}: RVOL={rvol:.1f}% (24h=${volume_24h_usd:,.0f}, avg=${avg_volume:,.0f})")
    
    # Step 5: Sort by RVOL descending and take top N
    rvol_results.sort(key=lambda x: x[1], reverse=True)
    top_results = rvol_results[:limit]
    
    # Store volume and change data in Redis for all processed symbols (not just top results)
    # This ensures screener has access to 24h change % for all symbols
    if volume_change_data:
        try:
            _store_volume_data(volume_change_data)
            logger.info(f"Stored volume and change data for {len(volume_change_data)} symbols in Redis")
        except Exception as e:
            logger.warning(f"Failed to store volume/change data: {e}")
    
    if top_results:
        logger.info(
            f"Top RVOL symbols: {[(s, f'{r:.1f}%') for s, r in top_results[:5]]}"
        )
    else:
        logger.warning("No symbols met RVOL threshold")
    
    return top_results


def get_dynamic_symbols_by_rvol(limit: Optional[int] = None) -> List[str]:
    """
    Get dynamic symbol list based on RVOL + owned symbols.
    
    Fetches top USD pairs by RVOL (relative volume) and merges with symbols
    from account holdings. Owned symbols are always included regardless of RVOL.
    
    Args:
        limit: Number of top pairs to fetch by RVOL (default: SYMBOL_LIMIT)
        
    Returns:
        Merged and deduplicated list of symbols (capped at MAX_TOTAL_SYMBOLS)
    """
    if limit is None:
        limit = get_symbol_limit()
    
    max_total = get_max_total_symbols()
    
    # Fetch top pairs by RVOL
    try:
        rvol_pairs = fetch_symbols_by_rvol(limit=limit)
        top_pairs = [symbol for symbol, _ in rvol_pairs]
    except Exception as e:
        logger.error(f"Failed to fetch RVOL pairs: {e}")
        top_pairs = []
    
    # Fetch owned symbols (always included regardless of RVOL)
    owned_symbols = get_owned_symbols()
    
    # Merge: top RVOL pairs first, then owned
    seen: Set[str] = set()
    merged: List[str] = []
    
    for symbol in top_pairs:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
    
    # Track which owned symbols are being added
    added_held: List[str] = []
    for symbol in owned_symbols:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
            added_held.append(symbol)
    
    if added_held:
        logger.info(f"Adding {len(added_held)} held symbols (regardless of RVOL): {added_held}")
    
    # Cap total at max for WebSocket stability
    if len(merged) > max_total:
        logger.warning(f"Symbol list ({len(merged)}) exceeds max ({max_total}), truncating")
        merged = merged[:max_total]
    
    logger.info(f"Final RVOL-based symbol list ({len(merged)}): {merged}")
    return merged


def check_symbol_has_data(symbol: str, intervals: Optional[List[str]] = None) -> bool:
    """
    Check if a symbol has any OHLCV data in Redis streams.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USD")
        intervals: List of intervals to check (default: ["5m", "1h"])
        
    Returns:
        True if symbol has data for at least one interval, False otherwise
    """
    if intervals is None:
        intervals = ["5m", "1h"]
    
    try:
        client = get_redis_client()
        
        for interval in intervals:
            stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
            try:
                # Check if stream exists and has at least one message
                stream_info = client.xinfo_stream(stream_key)
                if stream_info and stream_info.get("length", 0) > 0:
                    return True
            except Exception:
                # Stream doesn't exist or has no data, try next interval
                continue
        
        # No data found for any interval
        return False
    except Exception as e:
        logger.warning(f"Error checking data for {symbol}: {e}")
        return False


def get_failed_symbols() -> Set[str]:
    """
    Get set of symbols that have been marked as failed (no data).
    
    Returns:
        Set of failed symbol strings
    """
    try:
        client = get_redis_client()
        failed = client.smembers(FAILED_SYMBOLS_KEY)
        return {s.decode() if isinstance(s, bytes) else s for s in failed}
    except Exception as e:
        logger.warning(f"Error getting failed symbols: {e}")
        return set()


def mark_symbol_failed(symbol: str) -> None:
    """
    Mark a symbol as failed (no data available).
    
    Args:
        symbol: Trading pair symbol to mark as failed
    """
    try:
        client = get_redis_client()
        client.sadd(FAILED_SYMBOLS_KEY, symbol)
        # Set expiration on the set itself (Redis doesn't support TTL on set members)
        client.expire(FAILED_SYMBOLS_KEY, FAILED_SYMBOLS_TTL)
        logger.info(f"Marked {symbol} as failed (no data available)")
    except Exception as e:
        logger.warning(f"Error marking {symbol} as failed: {e}")


def unmark_symbol_failed(symbol: str) -> None:
    """
    Remove a symbol from the failed list (data is now available).
    
    Args:
        symbol: Trading pair symbol to unmark
    """
    try:
        client = get_redis_client()
        removed = client.srem(FAILED_SYMBOLS_KEY, symbol)
        if removed:
            logger.info(f"Unmarked {symbol} from failed list (data now available)")
    except Exception as e:
        logger.warning(f"Error unmarking {symbol}: {e}")


def get_live_universe() -> List[str]:
    """
    Get the list of symbols allowed for live trading.
    
    Reads from environment variable LIVE_UNIVERSE_PAIRS (comma-separated).
    Defaults to top 5 high-liquidity pairs: BTC/USD, ETH/USD, SOL/USD, LINK/USD, DOT/USD.
    
    Also stores the universe in Redis for fast lookups.
    
    Returns:
        List of trading pair symbols in standard format (e.g., ["BTC/USD", "ETH/USD", ...])
    """
    # Read from environment variable
    env_pairs = os.getenv("LIVE_UNIVERSE_PAIRS", "BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOT/USD")
    
    # Parse comma-separated list and normalize symbols
    pairs = [normalize_symbol(pair.strip()) for pair in env_pairs.split(",") if pair.strip()]
    
    # Store in Redis for fast lookups (as a set)
    try:
        client = get_redis_client()
        # Clear existing set and add new pairs
        client.delete(LIVE_UNIVERSE_KEY)
        if pairs:
            client.sadd(LIVE_UNIVERSE_KEY, *pairs)
        logger.debug(f"Live universe updated in Redis: {pairs}")
    except Exception as e:
        logger.warning(f"Failed to store live universe in Redis: {e}")
    
    return pairs


def is_in_live_universe(symbol: str) -> bool:
    """
    Check if a symbol is in the live universe (allowed for live trading).
    
    First checks Redis cache, then falls back to environment variable.
    Normalizes the symbol before checking to ensure consistent matching.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USD", "XETHZ/USD")
        
    Returns:
        True if symbol is in live universe, False otherwise
    """
    # Normalize symbol for consistent matching
    normalized = normalize_symbol(symbol)
    
    # Check Redis cache first (fastest)
    try:
        client = get_redis_client()
        is_member = client.sismember(LIVE_UNIVERSE_KEY, normalized)
        if is_member:
            return True
        
        # If Redis set is empty, populate it from env and check again
        set_size = client.scard(LIVE_UNIVERSE_KEY)
        if set_size == 0:
            live_universe = get_live_universe()
            return normalized in live_universe
        
        return False
    except Exception as e:
        logger.debug(f"Failed to check live universe in Redis: {e}, falling back to env")
    
    # Fallback to environment variable
    live_universe = get_live_universe()
    return normalized in live_universe


def update_universe_with_hysteresis(
    current_universe: List[str],
    ranked_candidates: List[Tuple[str, float]],  # (symbol, rank_score)
    limit: Optional[int] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Update universe with hysteresis to prevent thrashing.
    
    Uses asymmetric confirmation-based logic:
    - Add: Requires 2 consecutive qualifies (strict)
    - Drop: Requires 2 consecutive fails OR immediate drop on hard failures
    
    Immediate drop conditions (hard failures, no confirmation needed):
    - Symbol delisted/unavailable (in failed_symbols set)
    - Volume < min_24h_volume_usd
    - Volume collapses below collapse_threshold of previous volume
    
    Args:
        current_universe: Current active symbols (canonical format)
        ranked_candidates: List of (symbol, score) tuples sorted by rank (best first)
                          Symbols must be in canonical format (normalized)
        limit: Maximum universe size (default: SYMBOL_LIMIT)
        
    Returns:
        Updated universe list with hysteresis applied
    """
    if limit is None:
        limit = get_symbol_limit()
    
    # Ensure all symbols are canonical (normalized) - critical for hysteresis state matching
    canonical_universe = [normalize_symbol(s) for s in current_universe]
    canonical_candidates = [(normalize_symbol(s), score) for s, score in ranked_candidates]
    
    # Convert ranked_candidates to dict for quick lookup: symbol -> rank (1-indexed)
    symbol_to_rank = {symbol: rank + 1 for rank, (symbol, _) in enumerate(canonical_candidates)}
    symbol_to_score = {symbol: score for symbol, score in canonical_candidates}
    
    # Get hysteresis thresholds
    add_threshold = get_universe_add_threshold_rank()
    add_confirmations = get_universe_add_confirmations()
    drop_threshold = get_universe_drop_threshold_rank()
    drop_confirmations = get_universe_drop_confirmations()
    min_volume_usd = get_min_24h_volume_usd()
    volume_collapse_threshold = get_volume_collapse_threshold()
    
    # Get failed symbols (delisted/unavailable)
    failed_symbols = get_failed_symbols()
    
    # Get volume data for immediate drop checks
    client = get_redis_client()
    volume_data_cache: Dict[str, Dict[str, float]] = {}
    try:
        all_volume_data = client.hgetall(SYMBOL_VOLUME_KEY)
        for symbol_bytes, data_bytes in all_volume_data.items():
            try:
                symbol = symbol_bytes.decode() if isinstance(symbol_bytes, bytes) else symbol_bytes
                symbol = normalize_symbol(symbol)  # Ensure canonical
                data = json.loads(data_bytes) if isinstance(data_bytes, bytes) else data_bytes
                volume_data_cache[symbol] = data
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Failed to load volume data for immediate drop checks: {e}")
    
    # Get owned symbols (always keep these, regardless of rank)
    owned_symbols = get_owned_symbols()
    owned_symbols_canonical = [normalize_symbol(s) for s in owned_symbols]
    
    # Load or initialize hysteresis state from Redis
    # Format: "universe:hysteresis:{symbol}" -> JSON with {"add_count": N, "drop_count": M, "last_volume": V}
    hysteresis_key_prefix = "universe:hysteresis:"
    
    # Load current hysteresis state (using canonical symbols)
    all_symbols_to_check = set(canonical_universe) | set(symbol_to_rank.keys())
    hysteresis_state: Dict[str, Dict[str, Any]] = {}
    for symbol in all_symbols_to_check:
        key = f"{hysteresis_key_prefix}{symbol}"
        try:
            data = client.get(key)
            if data:
                state = json.loads(data)
                # Ensure all required fields exist
                hysteresis_state[symbol] = {
                    "add_count": state.get("add_count", 0),
                    "drop_count": state.get("drop_count", 0),
                    "last_volume": state.get("last_volume", 0.0),
                }
            else:
                hysteresis_state[symbol] = {"add_count": 0, "drop_count": 0, "last_volume": 0.0}
        except Exception:
            hysteresis_state[symbol] = {"add_count": 0, "drop_count": 0, "last_volume": 0.0}
    
    # Update hysteresis counters based on current rankings
    updated_universe = list(canonical_universe)
    changes_made = False
    
    # Track statistics for logging
    adds: List[str] = []
    drops: List[str] = []
    adds_confirmed_count = 0
    drops_confirmed_count = 0
    
    # Check for immediate drops first (hard failures - no confirmation needed)
    for symbol in list(updated_universe):
        # Skip owned symbols (never drop them)
        if symbol in owned_symbols_canonical:
            continue
        
        # Immediate drop condition 1: Symbol delisted/unavailable
        if symbol in failed_symbols:
            updated_universe.remove(symbol)
            drops.append(symbol)
            drops_confirmed_count += 1  # Immediate drops count as confirmed
            changes_made = True
            logger.warning(f"Immediate drop: {symbol} (delisted/unavailable)")
            # Clear hysteresis state
            if symbol in hysteresis_state:
                hysteresis_state[symbol] = {"add_count": 0, "drop_count": 0, "last_volume": 0.0}
            continue
        
        # Immediate drop condition 2: Volume below minimum threshold
        volume_data = volume_data_cache.get(symbol, {})
        current_volume = volume_data.get("volume_24h", 0.0)
        if current_volume > 0 and current_volume < min_volume_usd:
            updated_universe.remove(symbol)
            drops.append(symbol)
            drops_confirmed_count += 1  # Immediate drops count as confirmed
            changes_made = True
            logger.warning(f"Immediate drop: {symbol} (volume ${current_volume:,.0f} < min ${min_volume_usd:,.0f})")
            # Clear hysteresis state
            if symbol in hysteresis_state:
                hysteresis_state[symbol] = {"add_count": 0, "drop_count": 0, "last_volume": 0.0}
            continue
        
        # Immediate drop condition 3: Volume collapse (drops below threshold of previous)
        last_volume = hysteresis_state.get(symbol, {}).get("last_volume", 0.0)
        if last_volume > 0 and current_volume > 0:
            volume_ratio = current_volume / last_volume
            if volume_ratio < volume_collapse_threshold:
                updated_universe.remove(symbol)
                changes_made = True
                logger.warning(
                    f"Immediate drop: {symbol} (volume collapse: ${current_volume:,.0f} "
                    f"({volume_ratio*100:.1f}% of previous ${last_volume:,.0f})"
                )
                # Clear hysteresis state
                if symbol in hysteresis_state:
                    hysteresis_state[symbol] = {"add_count": 0, "drop_count": 0, "last_volume": 0.0}
                continue
    
    # Process ranked candidates for additions and normal drops
    for symbol in symbol_to_rank.keys():
        rank = symbol_to_rank[symbol]
        is_in_universe = symbol in updated_universe
        
        # Update volume in state for collapse detection
        volume_data = volume_data_cache.get(symbol, {})
        current_volume = volume_data.get("volume_24h", 0.0)
        if symbol in hysteresis_state:
            hysteresis_state[symbol]["last_volume"] = current_volume
        
        if not is_in_universe:
            # Candidate for addition (strict: must qualify 2 consecutive times)
            if rank <= add_threshold:
                # Increment add counter
                hysteresis_state[symbol]["add_count"] = hysteresis_state[symbol].get("add_count", 0) + 1
                hysteresis_state[symbol]["drop_count"] = 0  # Reset drop counter
                
                # Add if confirmed (2 consecutive qualifies)
                if hysteresis_state[symbol]["add_count"] >= add_confirmations:
                    if len(updated_universe) < limit:
                        updated_universe.append(symbol)
                        adds.append(symbol)
                        adds_confirmed_count += 1
                        changes_made = True
                        logger.info(
                            f"Added {symbol} to universe (rank={rank}, "
                            f"confirmed after {hysteresis_state[symbol]['add_count']} consecutive qualifies)"
                        )
            else:
                # Reset add counter if not in top threshold (must be consecutive)
                hysteresis_state[symbol]["add_count"] = 0
        else:
            # Already in universe - check for normal drop (2 consecutive fails)
            # Skip owned symbols (never drop them)
            if symbol in owned_symbols_canonical:
                # Reset counters for owned symbols
                hysteresis_state[symbol]["drop_count"] = 0
                hysteresis_state[symbol]["add_count"] = 0
                continue
            
            if rank > drop_threshold:
                # Increment drop counter
                hysteresis_state[symbol]["drop_count"] = hysteresis_state[symbol].get("drop_count", 0) + 1
                hysteresis_state[symbol]["add_count"] = 0  # Reset add counter
                
                # Drop if confirmed (2 consecutive fails)
                if hysteresis_state[symbol]["drop_count"] >= drop_confirmations:
                    updated_universe.remove(symbol)
                    drops.append(symbol)
                    drops_confirmed_count += 1
                    changes_made = True
                    logger.info(
                        f"Dropped {symbol} from universe (rank={rank}, "
                        f"confirmed after {hysteresis_state[symbol]['drop_count']} consecutive fails)"
                    )
            else:
                # Reset drop counter if still above threshold (must be consecutive)
                hysteresis_state[symbol]["drop_count"] = 0
                hysteresis_state[symbol]["add_count"] = 0
    
    # Handle symbols in universe but not in ranked list (should be dropped unless owned)
    for symbol in list(updated_universe):
        if symbol not in symbol_to_rank and symbol not in owned_symbols_canonical:
            # Symbol disappeared from rankings - increment drop counter
            hysteresis_state[symbol]["drop_count"] = hysteresis_state[symbol].get("drop_count", 0) + 1
            hysteresis_state[symbol]["add_count"] = 0
            
            if hysteresis_state[symbol]["drop_count"] >= drop_confirmations:
                updated_universe.remove(symbol)
                changes_made = True
                logger.info(
                    f"Dropped {symbol} from universe (not in rankings, "
                    f"confirmed after {hysteresis_state[symbol]['drop_count']} consecutive fails)"
                )
    
    # Save updated hysteresis state to Redis (only for symbols with active state)
    for symbol, state in hysteresis_state.items():
        key = f"{hysteresis_key_prefix}{symbol}"
        try:
            # Persist if symbol is in universe, has active counters, or has volume history
            if (
                symbol in updated_universe
                or state.get("add_count", 0) > 0
                or state.get("drop_count", 0) > 0
                or state.get("last_volume", 0.0) > 0
            ):
                client.setex(key, 86400, json.dumps(state))  # 24h TTL
        except Exception as e:
            logger.debug(f"Failed to save hysteresis state for {symbol}: {e}")
    
    # Ensure we don't exceed limit (keep top-ranked symbols, but always include owned)
    owned_in_universe = [s for s in updated_universe if s in owned_symbols_canonical]
    non_owned_universe = [s for s in updated_universe if s not in owned_symbols_canonical]
    
    if len(non_owned_universe) > (limit - len(owned_in_universe)):
        # Sort by rank and keep top N (excluding owned)
        universe_with_ranks = [(s, symbol_to_rank.get(s, 999)) for s in non_owned_universe]
        universe_with_ranks.sort(key=lambda x: x[1])
        non_owned_universe = [s for s, _ in universe_with_ranks[:limit - len(owned_in_universe)]]
        changes_made = True
    
    # Recombine: owned symbols + top-ranked non-owned
    updated_universe = owned_in_universe + non_owned_universe
    
    if changes_made:
        logger.info(
            f"Universe updated: {len(updated_universe)} symbols "
            f"(was {len(canonical_universe)}, owned={len(owned_in_universe)})"
        )
    
    # Return updated universe and statistics
    stats = {
        "adds": adds,
        "drops": drops,
        "adds_confirmed_count": adds_confirmed_count,
        "drops_confirmed_count": drops_confirmed_count,
    }
    
    return updated_universe, stats


def get_dynamic_symbols_by_rvol_with_replacements(
    limit: Optional[int] = None,
    current_symbols: Optional[List[str]] = None,
) -> List[str]:
    """
    Get dynamic symbol list based on RVOL + owned symbols, replacing failed symbols.
    
    This function:
    1. Fetches top RVOL-ranked symbols
    2. Excludes symbols marked as failed
    3. Replaces failed symbols from current_symbols with next-best alternatives
    4. Includes owned symbols regardless of RVOL or failure status
    
    Args:
        limit: Number of top pairs to fetch by RVOL (default: SYMBOL_LIMIT)
        current_symbols: Current active symbols (to detect which need replacement)
        
    Returns:
        Merged and deduplicated list of symbols (capped at MAX_TOTAL_SYMBOLS)
    """
    if limit is None:
        limit = get_symbol_limit()
    
    max_total = get_max_total_symbols()
    
    # Get failed symbols to exclude
    failed_symbols = get_failed_symbols()
    
    # Fetch more candidates than needed to account for exclusions
    # Fetch extra to have replacements available
    fetch_limit = limit + len(failed_symbols) + 10
    
    # Fetch top pairs by RVOL
    try:
        rvol_pairs = fetch_symbols_by_rvol(limit=fetch_limit)
        # Filter out failed symbols
        top_pairs = [
            symbol for symbol, _ in rvol_pairs
            if symbol not in failed_symbols
        ]
    except Exception as e:
        logger.error(f"Failed to fetch RVOL pairs: {e}")
        top_pairs = []
    
    # Fetch owned symbols (always included regardless of RVOL or failure status)
    owned_symbols = get_owned_symbols()
    
    # Merge: top RVOL pairs first, then owned
    seen: Set[str] = set()
    merged: List[str] = []
    
    # Add top RVOL pairs (excluding failed)
    for symbol in top_pairs[:limit]:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
    
    # If we have current_symbols, replace any failed ones with alternatives
    if current_symbols:
        failed_in_current = [s for s in current_symbols if s in failed_symbols]
        if failed_in_current:
            logger.info(f"Replacing {len(failed_in_current)} failed symbols: {failed_in_current}")
            
            # Get replacement candidates (next best RVOL symbols not already included)
            replacement_candidates = [
                symbol for symbol, _ in rvol_pairs
                if symbol not in seen and symbol not in failed_symbols
            ]
            
            # Replace each failed symbol with next best alternative
            for failed_symbol in failed_in_current:
                if replacement_candidates:
                    replacement = replacement_candidates.pop(0)
                    if replacement not in seen:
                        seen.add(replacement)
                        merged.append(replacement)
                        logger.info(f"Replaced {failed_symbol} with {replacement}")
                else:
                    logger.warning(f"No replacement available for {failed_symbol}")
    
    # Add owned symbols (always included)
    added_held: List[str] = []
    for symbol in owned_symbols:
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
            added_held.append(symbol)
    
    if added_held:
        logger.info(f"Adding {len(added_held)} held symbols (regardless of RVOL): {added_held}")
    
    # Cap total at max for WebSocket stability
    if len(merged) > max_total:
        logger.warning(f"Symbol list ({len(merged)}) exceeds max ({max_total}), truncating")
        merged = merged[:max_total]
    
    logger.info(f"Final RVOL-based symbol list ({len(merged)}): {merged}")
    return merged
