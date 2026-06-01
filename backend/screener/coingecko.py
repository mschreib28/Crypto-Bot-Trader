"""CoinGecko API client for market cap and supply data (backup data source)."""

import json
import logging
import time
from typing import Any, Dict, Iterable, Optional

import requests

from backend.redis import get_redis_client

logger = logging.getLogger(__name__)

# CoinGecko API endpoint
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
COINGECKO_COIN_ENDPOINT = f"{COINGECKO_API_URL}/coins"

# Cache TTL (24 hours minimum to reduce API calls and avoid rate limits)
COINGECKO_CACHE_TTL = 86400
COINGECKO_NEGATIVE_CACHE_TTL = 86400  # 24h for empty/not-found lookups
COINGECKO_CACHE_KEY_PREFIX = "coingecko:market_data:"
COINGECKO_ID_MAPPING_KEY_PREFIX = "coingecko:id_mapping:"
COINGECKO_ID_MAPPING_TTL = 604800  # 7 days (mappings rarely change)

# Rate limiting: CoinGecko free tier allows 10-50 calls/minute
# Use conservative rate: 30 calls/minute = 1 call every 2 seconds
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2
RATE_LIMIT_DELAY = 2.0  # Seconds between API calls

# Batch endpoint for fetching multiple coins at once
COINGECKO_MARKETS_ENDPOINT = f"{COINGECKO_API_URL}/coins/markets"
BATCH_SIZE = 50  # CoinGecko allows up to 50 coins per batch request

# Track last API call time for rate limiting
_last_api_call_time = 0.0

# Symbol to CoinGecko ID mapping (common pairs)
SYMBOL_TO_COINGECKO_ID = {
    "BTC/USD": "bitcoin",
    "ETH/USD": "ethereum",
    "SOL/USD": "solana",
    "LINK/USD": "chainlink",
    "DOT/USD": "polkadot",
    "ADA/USD": "cardano",
    "XRP/USD": "ripple",
    "DOGE/USD": "dogecoin",
    "MATIC/USD": "matic-network",
    "AVAX/USD": "avalanche-2",
    "ATOM/USD": "cosmos",
    "ALGO/USD": "algorand",
    "UNI/USD": "uniswap",
    "AAVE/USD": "aave",
    "SNX/USD": "havven",
    "NEAR/USD": "near",
    "FTM/USD": "fantom",
    "SAND/USD": "the-sandbox",
    "MANA/USD": "decentraland",
    "GRT/USD": "the-graph",
    "CRV/USD": "curve-dao-token",
    "COMP/USD": "compound-governance-token",
    "MKR/USD": "maker",
    "YFI/USD": "yearn-finance",
    "SUSHI/USD": "sushi",
    "1INCH/USD": "1inch",
    "ENJ/USD": "enjincoin",
    "CHZ/USD": "chiliz",
    "BAT/USD": "basic-attention-token",
    "ZRX/USD": "0x",
    "KNC/USD": "kyber-network-crystal",
    "STORJ/USD": "storj",
    "OMG/USD": "omisego",
    "ZEC/USD": "zcash",
    "XMR/USD": "monero",
    "LTC/USD": "litecoin",
    "ETC/USD": "ethereum-classic",
    "BCH/USD": "bitcoin-cash",
    "BSV/USD": "bitcoin-sv",
}


def _rate_limit_wait() -> None:
    """Enforce minimum delay between CoinGecko API calls."""
    global _last_api_call_time
    time_since_last_call = time.time() - _last_api_call_time
    if time_since_last_call < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - time_since_last_call)
    _last_api_call_time = time.time()


def _request_with_retry(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[requests.Response]:
    """
    GET request with timeout after timeout, connection errors, 429, and 5xx.

    Returns None on final failure.
    """
    for attempt in range(MAX_RETRIES + 1):
        _rate_limit_wait()
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                backoff = 2 ** (attempt + 1)
                logger.debug(f"CoinGecko request failed (attempt {attempt + 1}): {exc}, retrying in {backoff}s")
                time.sleep(backoff)
                continue
            logger.warning(f"CoinGecko request failed after {MAX_RETRIES + 1} attempts: {exc}")
            return None

        if response.status_code == 429 or response.status_code >= 500:
            if attempt < MAX_RETRIES:
                backoff = 2 ** (attempt + 1)
                logger.debug(
                    f"CoinGecko HTTP {response.status_code} (attempt {attempt + 1}), retrying in {backoff}s"
                )
                time.sleep(backoff)
                continue
            logger.warning(f"CoinGecko HTTP {response.status_code} after {MAX_RETRIES + 1} attempts")
            return response

        return response

    return None


def _symbol_to_coingecko_id(symbol: str) -> Optional[str]:
    """
    Map Kraken symbol to CoinGecko coin ID.

    Args:
        symbol: Trading pair (e.g., "BTC/USD", "ETH/USD")

    Returns:
        CoinGecko coin ID or None if not found
    """
    # Direct mapping
    if symbol in SYMBOL_TO_COINGECKO_ID:
        return SYMBOL_TO_COINGECKO_ID[symbol]

    # Try to extract base asset and search
    if "/" in symbol:
        base_asset = symbol.split("/")[0].upper()
        # Common mappings
        base_to_id = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "LINK": "chainlink",
            "DOT": "polkadot",
            "ADA": "cardano",
            "XRP": "ripple",
            "DOGE": "dogecoin",
            "MATIC": "matic-network",
            "AVAX": "avalanche-2",
            "ATOM": "cosmos",
            "ALGO": "algorand",
            "UNI": "uniswap",
            "AAVE": "aave",
            "SNX": "havven",
            "NEAR": "near",
            "LINEA": "linea",
            "ENA": "ethena",
            # GIGA not available on CoinGecko
        }
        coin_id = base_to_id.get(base_asset)
        if coin_id:
            return coin_id

        # Dynamic search with caching (only searches once per symbol, then caches)
        coin_id = _search_coingecko_id(base_asset)
        if coin_id:
            return coin_id

    return None


def build_symbol_to_coin_id(symbols: Iterable[str]) -> Dict[str, str]:
    """Resolve all symbols to CoinGecko IDs. Safe to run in a thread."""
    result: Dict[str, str] = {}
    for sym in symbols:
        cid = _symbol_to_coingecko_id(sym)
        if cid:
            result[sym] = cid
    return result


def _get_cache_key(symbol: str) -> str:
    """Get Redis cache key for symbol."""
    return f"{COINGECKO_CACHE_KEY_PREFIX}{symbol}"


def _get_id_mapping_key(symbol: str) -> str:
    """Get Redis cache key for symbol-to-ID mapping."""
    return f"{COINGECKO_ID_MAPPING_KEY_PREFIX}{symbol}"


def _search_coingecko_id(base_asset: str) -> Optional[str]:
    """
    Search CoinGecko for coin ID by base asset symbol.

    Uses CoinGecko's /search endpoint to find matching coins.
    Caches results in Redis to avoid repeated API calls.

    Args:
        base_asset: Base asset symbol (e.g., "MYX", "BTC")

    Returns:
        CoinGecko coin ID or None if not found
    """
    # Check cache first
    try:
        client = get_redis_client()
        cache_key = _get_id_mapping_key(base_asset)
        cached_id = client.get(cache_key)

        if cached_id:
            if isinstance(cached_id, bytes):
                cached_id = cached_id.decode()
            logger.debug(f"Using cached CoinGecko ID for {base_asset}: {cached_id}")
            return cached_id if cached_id != "None" else None
    except Exception as e:
        logger.debug(f"Failed to read ID mapping cache for {base_asset}: {e}")

    # Search CoinGecko API
    try:
        search_url = f"{COINGECKO_API_URL}/search"
        params = {"query": base_asset}
        response = _request_with_retry(search_url, params=params)

        if response is None:
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, "None")
            except Exception:
                pass
            return None

        if response.status_code == 429:
            logger.warning(f"CoinGecko rate limited during ID search for {base_asset}")
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, "None")
            except Exception:
                pass
            return None
        elif response.status_code != 200:
            logger.debug(f"CoinGecko search API error for {base_asset}: HTTP {response.status_code}")
            return None

        data = response.json()
        coins = data.get("coins", [])

        if not coins:
            logger.debug(f"No CoinGecko results for {base_asset}")
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, "None")
            except Exception:
                pass
            return None

        # Find best match (exact symbol match preferred)
        best_match = None
        for coin in coins:
            coin_id = coin.get("id")
            symbol_match = coin.get("symbol", "").upper()
            name_match = coin.get("name", "").upper()

            # Prefer exact symbol match
            if symbol_match == base_asset.upper():
                best_match = coin_id
                break
            # Fallback to name match if starts with base asset
            elif not best_match and name_match.startswith(base_asset.upper()):
                best_match = coin_id

        # If no exact match, use first result (most popular)
        if not best_match and coins:
            best_match = coins[0].get("id")

        # Cache the result
        if best_match:
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, COINGECKO_ID_MAPPING_TTL, best_match)
                logger.debug(f"Cached CoinGecko ID for {base_asset}: {best_match}")
            except Exception as e:
                logger.debug(f"Failed to cache ID mapping for {base_asset}: {e}")

        return best_match

    except Exception as e:
        logger.debug(f"Error searching CoinGecko for {base_asset}: {e}")
        return None


def batch_get_market_data(symbol_to_coin_id: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """
    Batch fetch market data for multiple symbols using CoinGecko's /coins/markets endpoint.

    This is much more efficient than individual API calls and avoids rate limiting.

    Args:
        symbol_to_coin_id: Dictionary mapping symbol -> CoinGecko coin ID

    Returns:
        Dictionary mapping symbol -> market data dict (same format as get_market_data)
    """
    results = {symbol: {
        "market_cap": None,
        "circulating_supply": None,
        "total_supply": None,
        "supply_ratio": None,
        "change_24h_pct": None,
    } for symbol in symbol_to_coin_id.keys()}

    if not symbol_to_coin_id:
        return results

    # Filter out symbols that already have cached data
    symbols_to_fetch = []
    coin_ids_to_fetch = []
    symbol_by_coin_id = {}

    try:
        client = get_redis_client()
        for symbol, coin_id in symbol_to_coin_id.items():
            # Check cache first
            cache_key = _get_cache_key(symbol)
            cached_data = client.get(cache_key)

            if cached_data:
                try:
                    if isinstance(cached_data, bytes):
                        cached_data = cached_data.decode()
                    parsed = json.loads(cached_data)
                    # Only use cache if it has actual data
                    has_data = any([
                        parsed.get("market_cap") is not None,
                        parsed.get("circulating_supply") is not None,
                        parsed.get("total_supply") is not None,
                    ])
                    if has_data:
                        results[symbol] = parsed
                        continue
                except Exception:
                    pass

            # Need to fetch this symbol
            symbols_to_fetch.append(symbol)
            coin_ids_to_fetch.append(coin_id)
            symbol_by_coin_id[coin_id] = symbol
    except Exception as e:
        logger.debug(f"Error checking cache for batch fetch: {e}")
        # Fall through to fetch all

    if not coin_ids_to_fetch:
        return results

    # Batch fetch in chunks of BATCH_SIZE
    for i in range(0, len(coin_ids_to_fetch), BATCH_SIZE):
        batch_ids = coin_ids_to_fetch[i:i + BATCH_SIZE]
        batch_symbols = symbols_to_fetch[i:i + BATCH_SIZE]

        try:
            params = {
                "vs_currency": "usd",
                "ids": ",".join(batch_ids),
                "order": "market_cap_desc",
                "per_page": len(batch_ids),
                "page": 1,
            }

            response = _request_with_retry(COINGECKO_MARKETS_ENDPOINT, params=params)

            if response is None:
                logger.warning(f"CoinGecko batch fetch timed out for {len(batch_ids)} coins, skipping batch")
                try:
                    client = get_redis_client()
                    for symbol in batch_symbols:
                        cache_key = _get_cache_key(symbol)
                        client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, json.dumps(results[symbol]))
                except Exception:
                    pass
                continue

            if response.status_code == 429:
                logger.warning(f"CoinGecko rate limited during batch fetch for {len(batch_ids)} coins")
                try:
                    client = get_redis_client()
                    for symbol in batch_symbols:
                        cache_key = _get_cache_key(symbol)
                        client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, json.dumps(results[symbol]))
                except Exception:
                    pass
                continue
            elif response.status_code != 200:
                logger.debug(f"CoinGecko batch API error: HTTP {response.status_code}")
                continue

            data = response.json()

            # Map results back to symbols
            coin_id_to_data = {coin.get("id"): coin for coin in data if coin.get("id")}

            for coin_id, coin_data in coin_id_to_data.items():
                symbol = symbol_by_coin_id.get(coin_id)
                if not symbol:
                    continue

                # Extract market data — /coins/markets returns all these fields
                market_cap = coin_data.get("market_cap")
                if market_cap is not None:
                    try:
                        results[symbol]["market_cap"] = float(market_cap)
                    except (ValueError, TypeError):
                        pass

                circ = coin_data.get("circulating_supply")
                total = coin_data.get("total_supply")
                if circ is not None:
                    try:
                        results[symbol]["circulating_supply"] = float(circ)
                    except (ValueError, TypeError):
                        pass
                if total is not None:
                    try:
                        results[symbol]["total_supply"] = float(total)
                    except (ValueError, TypeError):
                        pass
                if circ and total and float(total) > 0:
                    try:
                        results[symbol]["supply_ratio"] = float(circ) / float(total)
                    except (ValueError, TypeError):
                        pass

                change = coin_data.get("price_change_percentage_24h")
                if change is not None:
                    try:
                        results[symbol]["change_24h_pct"] = float(change)
                    except (ValueError, TypeError):
                        pass

            # Cache results
            try:
                client = get_redis_client()
                for symbol in batch_symbols:
                    cache_key = _get_cache_key(symbol)
                    cache_ttl = COINGECKO_CACHE_TTL if results[symbol].get("market_cap") else COINGECKO_NEGATIVE_CACHE_TTL
                    client.setex(cache_key, cache_ttl, json.dumps(results[symbol]))
            except Exception as e:
                logger.debug(f"Failed to cache batch results: {e}")

        except Exception as e:
            logger.debug(f"Error in batch fetch: {e}")

    return results


def get_market_data(symbol: str) -> Dict[str, Any]:
    """
    Get market cap and supply data from CoinGecko API (backup data source).

    Caches results in Redis to avoid rate limits.

    Args:
        symbol: Trading pair (e.g., "BTC/USD", "ETH/USD")

    Returns:
        Dictionary with:
        - market_cap: Market cap in USD (float or None)
        - circulating_supply: Circulating supply (float or None)
        - total_supply: Total supply (float or None)
        - supply_ratio: circulating_supply / total_supply (float or None)
    """
    result = {
        "market_cap": None,
        "circulating_supply": None,
        "total_supply": None,
        "supply_ratio": None,
    }

    # Check cache first, but only use if it has actual data
    try:
        client = get_redis_client()
        cache_key = _get_cache_key(symbol)
        cached_data = client.get(cache_key)

        if cached_data:
            if isinstance(cached_data, bytes):
                cached_data = cached_data.decode()
            parsed = json.loads(cached_data)
            has_data = any([
                parsed.get("market_cap") is not None,
                parsed.get("circulating_supply") is not None,
                parsed.get("total_supply") is not None,
            ])
            has_market_cap_only = (
                parsed.get("market_cap") is not None
                and parsed.get("circulating_supply") is None
                and parsed.get("total_supply") is None
            )
            if has_data and not has_market_cap_only:
                logger.debug(f"Using cached CoinGecko data for {symbol}")
                return parsed
            elif has_market_cap_only:
                logger.debug(f"Cache for {symbol} has market_cap but no supply data, fetching fresh data")
            else:
                logger.debug(f"Ignoring cached empty result for {symbol}, fetching fresh data")
    except Exception as e:
        logger.debug(f"Failed to read cache for {symbol}: {e}")

    # Map symbol to CoinGecko ID
    coin_id = _symbol_to_coingecko_id(symbol)
    if not coin_id:
        logger.debug(f"No CoinGecko ID mapping for {symbol}")
        return result

    # Fetch from CoinGecko API
    try:
        url = f"{COINGECKO_COIN_ENDPOINT}/{coin_id}"
        response = _request_with_retry(url)

        if response is None:
            logger.warning(f"CoinGecko request timed out for {symbol}, skipping")
            try:
                client = get_redis_client()
                cache_key = _get_cache_key(symbol)
                client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, json.dumps(result))
            except Exception:
                pass
            return result

        if response.status_code == 429:
            logger.warning(f"CoinGecko rate limited for {symbol}, caching empty result")
            try:
                client = get_redis_client()
                cache_key = _get_cache_key(symbol)
                client.setex(cache_key, COINGECKO_NEGATIVE_CACHE_TTL, json.dumps(result))
            except Exception:
                pass
            return result
        elif response.status_code != 200:
            logger.debug(f"CoinGecko API error for {symbol}: HTTP {response.status_code}")
            return result

        data = response.json()

        # Extract market data
        market_data = data.get("market_data", {})

        # Market cap in USD - handle both nested dict and direct value
        market_cap_dict = market_data.get("market_cap", {})
        if isinstance(market_cap_dict, dict):
            market_cap_usd = market_cap_dict.get("usd")
        else:
            market_cap_usd = market_cap_dict

        if market_cap_usd is not None:
            try:
                result["market_cap"] = float(market_cap_usd)
            except (ValueError, TypeError):
                logger.debug(f"Invalid market_cap value for {symbol}: {market_cap_usd}")

        # Circulating supply
        circulating_supply = market_data.get("circulating_supply")
        if circulating_supply is not None:
            try:
                result["circulating_supply"] = float(circulating_supply)
            except (ValueError, TypeError):
                logger.debug(f"Invalid circulating_supply value for {symbol}: {circulating_supply}")

        # Total supply
        total_supply = market_data.get("total_supply")
        if total_supply is not None:
            try:
                result["total_supply"] = float(total_supply)
            except (ValueError, TypeError):
                logger.debug(f"Invalid total_supply value for {symbol}: {total_supply}")

        # Calculate supply ratio
        if result["circulating_supply"] is not None and result["total_supply"] is not None:
            if result["total_supply"] > 0:
                result["supply_ratio"] = result["circulating_supply"] / result["total_supply"]

        has_any_data = any([
            result["market_cap"] is not None,
            result["circulating_supply"] is not None,
            result["total_supply"] is not None,
        ])

        try:
            client = get_redis_client()
            cache_key = _get_cache_key(symbol)
            cache_ttl = COINGECKO_CACHE_TTL if has_any_data else COINGECKO_NEGATIVE_CACHE_TTL
            client.setex(cache_key, cache_ttl, json.dumps(result))
            if has_any_data:
                logger.debug(f"Cached CoinGecko data for {symbol}")
            else:
                logger.debug(f"Cached empty CoinGecko result for {symbol} (TTL=24h)")
        except Exception as e:
            logger.debug(f"Failed to cache CoinGecko data for {symbol}: {e}")

        return result

    except Exception as e:
        logger.debug(f"Error fetching CoinGecko data for {symbol}: {e}")
        return result
