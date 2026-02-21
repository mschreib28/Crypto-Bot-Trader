"""CoinGecko API client for market cap and supply data (backup data source)."""

import json
import logging
from typing import Any, Dict, Optional

import requests

from backend.redis import get_redis_client

logger = logging.getLogger(__name__)

# CoinGecko API endpoint
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
COINGECKO_COIN_ENDPOINT = f"{COINGECKO_API_URL}/coins"

# Cache TTL (24 hours to reduce API calls and avoid rate limits)
COINGECKO_CACHE_TTL = 86400
COINGECKO_CACHE_KEY_PREFIX = "coingecko:market_data:"
COINGECKO_ID_MAPPING_KEY_PREFIX = "coingecko:id_mapping:"
COINGECKO_ID_MAPPING_TTL = 604800  # 7 days (mappings rarely change)

# Rate limiting: CoinGecko free tier allows 10-50 calls/minute
REQUEST_TIMEOUT = 10

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
        response = requests.get(search_url, params=params, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 429:
            logger.warning(f"CoinGecko rate limited during ID search for {base_asset}")
            # Cache "None" briefly to avoid repeated searches
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, 300, "None")  # Cache for 5 minutes
            except:
                pass
            return None
        elif response.status_code != 200:
            logger.debug(f"CoinGecko search API error for {base_asset}: HTTP {response.status_code}")
            return None
        
        data = response.json()
        coins = data.get("coins", [])
        
        if not coins:
            logger.debug(f"No CoinGecko results for {base_asset}")
            # Cache "None" to avoid repeated searches
            try:
                client = get_redis_client()
                cache_key = _get_id_mapping_key(base_asset)
                client.setex(cache_key, COINGECKO_ID_MAPPING_TTL, "None")
            except:
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
        
    except requests.RequestException as e:
        logger.debug(f"CoinGecko search request failed for {base_asset}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Error searching CoinGecko for {base_asset}: {e}")
        return None


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
    
    # Check cache first
    try:
        client = get_redis_client()
        cache_key = _get_cache_key(symbol)
        cached_data = client.get(cache_key)
        
        if cached_data:
            if isinstance(cached_data, bytes):
                cached_data = cached_data.decode()
            parsed = json.loads(cached_data)
            logger.debug(f"Using cached CoinGecko data for {symbol}")
            return parsed
    except Exception as e:
        logger.debug(f"Failed to read cache for {symbol}: {e}")
    
    # Map symbol to CoinGecko ID
    coin_id = _symbol_to_coingecko_id(symbol)
    if not coin_id:
        logger.debug(f"No CoinGecko ID mapping for {symbol}")
        # #region agent log
        import time
        try:
            with open("/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/.cursor/debug-d22363.log", "a") as f:
                log_entry = {
                    "sessionId": "d22363",
                    "runId": "initial",
                    "hypothesisId": "A",
                    "location": "coingecko.py:166",
                    "message": "No CoinGecko ID found",
                    "data": {"symbol": symbol, "coin_id": None},
                    "timestamp": int(time.time() * 1000)
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass
        # #endregion
        return result
    
    # #region agent log
    import time
    try:
        with open("/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/.cursor/debug-d22363.log", "a") as f:
            log_entry = {
                "sessionId": "d22363",
                "runId": "initial",
                "hypothesisId": "A",
                "location": "coingecko.py:169",
                "message": "Found CoinGecko ID, fetching data",
                "data": {"symbol": symbol, "coin_id": coin_id},
                "timestamp": int(time.time() * 1000)
            }
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass
    # #endregion
    
    # Fetch from CoinGecko API
    try:
        url = f"{COINGECKO_COIN_ENDPOINT}/{coin_id}"
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 429:
            # Rate limited - cache empty result briefly to avoid hammering API
            logger.warning(f"CoinGecko rate limited for {symbol}, caching empty result")
            try:
                client = get_redis_client()
                cache_key = _get_cache_key(symbol)
                client.setex(cache_key, 300, json.dumps(result))  # Cache for 5 minutes
            except:
                pass
            return result
        elif response.status_code != 200:
            logger.debug(f"CoinGecko API error for {symbol}: HTTP {response.status_code}")
            return result
        
        data = response.json()
        
        # Extract market data
        market_data = data.get("market_data", {})
        
        # Market cap in USD
        market_cap_usd = market_data.get("market_cap", {}).get("usd")
        if market_cap_usd is not None:
            result["market_cap"] = float(market_cap_usd)
        
        # Circulating supply
        circulating_supply = market_data.get("circulating_supply")
        if circulating_supply is not None:
            result["circulating_supply"] = float(circulating_supply)
        
        # Total supply
        total_supply = market_data.get("total_supply")
        if total_supply is not None:
            result["total_supply"] = float(total_supply)
        
        # Calculate supply ratio
        if result["circulating_supply"] is not None and result["total_supply"] is not None:
            if result["total_supply"] > 0:
                result["supply_ratio"] = result["circulating_supply"] / result["total_supply"]
        
        # #region agent log
        import time
        try:
            with open("/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/.cursor/debug-d22363.log", "a") as f:
                log_entry = {
                    "sessionId": "d22363",
                    "runId": "initial",
                    "hypothesisId": "B",
                    "location": "coingecko.py:210",
                    "message": "CoinGecko API response parsed",
                    "data": {
                        "symbol": symbol,
                        "coin_id": coin_id,
                        "market_cap": result["market_cap"],
                        "circulating_supply": result["circulating_supply"],
                        "total_supply": result["total_supply"],
                        "supply_ratio": result["supply_ratio"]
                    },
                    "timestamp": int(time.time() * 1000)
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass
        # #endregion
        
        # Cache the result
        try:
            client = get_redis_client()
            cache_key = _get_cache_key(symbol)
            client.setex(cache_key, COINGECKO_CACHE_TTL, json.dumps(result))
            logger.debug(f"Cached CoinGecko data for {symbol}")
        except Exception as e:
            logger.debug(f"Failed to cache CoinGecko data for {symbol}: {e}")
        
        return result
        
    except requests.RequestException as e:
        logger.debug(f"CoinGecko API request failed for {symbol}: {e}")
        return result
    except Exception as e:
        logger.debug(f"Error fetching CoinGecko data for {symbol}: {e}")
        return result
