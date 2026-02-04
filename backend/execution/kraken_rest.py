"""Kraken REST API client for order execution."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from backend.execution.auth import get_auth_headers, generate_nonce, KRAKEN_API_URL
from backend.redis import get_redis_client
from backend.redis.keys import ASSET_PAIRS_CACHE_KEY, ASSET_PAIRS_CACHE_TTL

logger = logging.getLogger(__name__)

# Kraken rate limits (approximate, adjust based on actual limits)
# Kraken typically allows ~15 requests per 15 seconds for private endpoints
KRAKEN_RATE_LIMIT_DELAY = 1.0  # Minimum delay between requests (seconds)
_last_request_time: float = 0.0


def _rate_limit() -> None:
    """
    Enforce rate limiting to respect Kraken API limits.
    
    Implements a simple delay mechanism to ensure minimum time between requests.
    For production, consider using a token bucket or more sophisticated rate limiter.
    """
    global _last_request_time
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    
    if time_since_last < KRAKEN_RATE_LIMIT_DELAY:
        sleep_time = KRAKEN_RATE_LIMIT_DELAY - time_since_last
        logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
        time.sleep(sleep_time)
    
    _last_request_time = time.time()


def _create_session() -> requests.Session:
    """
    Create a requests session with retry logic for transient errors.
    
    Returns:
        Configured requests.Session
    """
    session = requests.Session()
    
    # Retry strategy for transient errors
    retry_strategy = Retry(
        total=3,  # Maximum 3 retries
        backoff_factor=1,  # Wait 1s, 2s, 4s between retries
        status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
        allowed_methods=["POST"],  # Only retry POST requests
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    
    return session


def _normalize_symbol(symbol: str) -> str:
    """
    Convert symbol format to Kraken format.
    
    Args:
        symbol: Symbol in format "BTC/USD" or "ETH/USD"
        
    Returns:
        Kraken-formatted pair (e.g., "XBTUSD" for BTC/USD)
    """
    # Kraken uses XBT instead of BTC and no slash in pair names
    normalized = symbol.replace("/", "")
    if normalized.startswith("BTC"):
        normalized = normalized.replace("BTC", "XBT", 1)
    return normalized


def _denormalize_symbol(kraken_pair: str) -> str:
    """
    Convert Kraken format back to standard format.
    
    Args:
        kraken_pair: Kraken-formatted pair (e.g., "XBTUSD")
        
    Returns:
        Standard format (e.g., "BTC/USD")
    """
    # Add slash and convert XBT back to BTC
    if kraken_pair.startswith("XBT"):
        normalized = kraken_pair.replace("XBT", "BTC", 1)
    else:
        normalized = kraken_pair
    
    # Insert slash before USD, EUR, etc.
    if len(normalized) >= 6:
        base = normalized[:-3]
        quote = normalized[-3:]
        return f"{base}/{quote}"
    return normalized


class KrakenClient:
    """
    Kraken REST API client for private endpoints.
    
    Handles authentication, rate limiting, and error handling for:
    - AddOrder: Place new orders
    - CancelOrder: Cancel existing orders
    - QueryOrders: Query order status
    - Balance: Get account balance (for testing auth)
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        """
        Initialize Kraken REST client.
        
        Args:
            api_key: Kraken API key (optional, uses config if not provided)
            api_secret: Kraken API secret (optional, uses config if not provided)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = _create_session()
    
    def _make_request(
        self, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated request to Kraken private API.
        
        Args:
            endpoint: API endpoint path (e.g., "Balance", "AddOrder")
            params: Request parameters (excluding nonce, which is auto-generated)
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            requests.RequestException: On HTTP errors
            ValueError: On authentication errors or invalid responses
        """
        # Enforce rate limiting
        _rate_limit()
        
        # Prepare POST data
        post_data: Dict[str, str] = {
            "nonce": generate_nonce(),
        }
        
        if params:
            # Convert all params to strings (Kraken API requirement)
            for key, value in params.items():
                post_data[str(key)] = str(value)
        
        # Build URI path
        uri_path = f"/0/private/{endpoint}"
        url = f"{KRAKEN_API_URL}{uri_path}"
        
        # Get authentication headers
        try:
            headers = get_auth_headers(
                uri_path, 
                post_data,
                api_key=self.api_key,
                api_secret=self.api_secret
            )
        except ValueError as e:
            logger.error(f"Authentication error: {e}")
            raise
        
        # Make request
        try:
            response = self.session.post(url, headers=headers, data=post_data, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            
            # Check for Kraken API errors
            if isinstance(result, dict) and "error" in result:
                errors = result.get("error", [])
                if errors:
                    error_msg = "; ".join(errors)
                    logger.error(f"Kraken API error: {error_msg}")
                    raise ValueError(f"Kraken API error: {error_msg}")
            
            return result
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            if status_code == 401:
                logger.error("Authentication failed: Invalid API credentials")
                raise ValueError("Authentication failed: Invalid API credentials") from e
            elif status_code == 403:
                logger.error("Authentication failed: API key lacks required permissions")
                raise ValueError("Authentication failed: API key lacks required permissions") from e
            else:
                logger.error(f"HTTP error {status_code}: {e}")
                raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise
    
    def get_balance(self) -> Optional[Dict[str, str]]:
        """
        Get account balance (for testing authentication).
        
        Returns:
            Dictionary mapping currency to balance, or None on error
        """
        try:
            result = self._make_request("Balance")
            if "result" in result:
                return result["result"]
            return None
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def add_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "market",
        volume: Optional[float] = None,
        price: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Place a new order on Kraken.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            side: "buy" or "sell"
            order_type: Order type ("market", "limit", etc.)
            volume: Order volume (base currency)
            price: Limit price (required for limit orders)
            **kwargs: Additional Kraken order parameters
            
        Returns:
            Order response from Kraken API
        """
        # Normalize symbol
        kraken_pair = _normalize_symbol(symbol)
        
        # Prepare order parameters
        params = {
            "pair": kraken_pair,
            "type": side,  # "buy" or "sell"
            "ordertype": order_type,
        }
        
        if volume is not None:
            params["volume"] = str(volume)
        
        if price is not None:
            # Round price to reasonable precision (Kraken typically requires 1-8 decimals depending on pair)
            # For stop-loss orders, round to 3 decimals as a safe default for USD pairs
            # This prevents "Invalid price" errors due to excessive precision
            if order_type == "stop-loss":
                price_rounded = round(float(price), 3)
                params["price"] = str(price_rounded)
            else:
                params["price"] = str(price)
        
        # Add any additional parameters
        params.update({str(k): str(v) for k, v in kwargs.items()})
        
        logger.info(f"Placing {order_type} {side} order for {volume} {symbol}")
        result = self._make_request("AddOrder", params)
        
        if "result" in result:
            txid = result["result"].get("txid", [])
            logger.info(f"Order placed successfully: txid={txid}")
        
        return result
    
    def cancel_order(self, txid: str) -> Dict[str, Any]:
        """
        Cancel an existing order.
        
        Args:
            txid: Kraken transaction ID (order ID)
            
        Returns:
            Cancellation response from Kraken API
        """
        params = {"txid": txid}
        
        logger.info(f"Cancelling order: {txid}")
        result = self._make_request("CancelOrder", params)
        
        if "result" in result:
            count = result["result"].get("count", 0)
            logger.info(f"Order cancelled: count={count}")
        
        return result
    
    def query_orders(self, txid: Optional[str] = None, trades: bool = False) -> Dict[str, Any]:
        """
        Query order status.
        
        Args:
            txid: Specific transaction ID to query (optional, queries all if not provided)
            trades: Whether to include trade information
            
        Returns:
            Order query response from Kraken API
        """
        params: Dict[str, Any] = {}
        
        if txid:
            params["txid"] = txid
        
        if trades:
            params["trades"] = "true"
        
        result = self._make_request("QueryOrders", params)
        return result
    
    def get_open_orders(self, trades: bool = False) -> Dict[str, Any]:
        """
        Get all open orders.
        
        Args:
            trades: Whether to include trade information
            
        Returns:
            Open orders response from Kraken API
        """
        params: Dict[str, Any] = {}
        
        if trades:
            params["trades"] = "true"
        
        logger.info("Querying open orders")
        result = self._make_request("OpenOrders", params)
        
        if "result" in result:
            open_orders = result["result"].get("open", {})
            count = len(open_orders)
            logger.info(f"Found {count} open order(s)")
        
        return result

    def get_ticker(self, pair: str) -> Optional[Dict[str, Any]]:
        """
        Get current ticker price for a trading pair (public endpoint).
        
        Args:
            pair: Trading pair (e.g., "ETH/USD" or "ETHUSD")
            
        Returns:
            Ticker data with 'c' (last trade close) price, or None on error
        """
        _rate_limit()
        
        kraken_pair = _normalize_symbol(pair)
        url = f"{KRAKEN_API_URL}/0/public/Ticker?pair={kraken_pair}"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("error"):
                logger.error(f"Kraken ticker error: {result['error']}")
                return None
            
            return result.get("result", {})
        except Exception as e:
            logger.error(f"Failed to get ticker for {pair}: {e}")
            return None

    def get_trade_balance(self, asset: str = "ZUSD") -> Optional[Dict[str, str]]:
        """
        Get trade balance (available funds and equity).
        
        Args:
            asset: Base asset for calculations (default: ZUSD for USD)
            
        Returns:
            Trade balance info including 'eb' (equivalent balance), 
            'tb' (trade balance), 'mf' (free margin), or None on error
        """
        try:
            result = self._make_request("TradeBalance", {"asset": asset})
            if "result" in result:
                return result["result"]
            return None
        except Exception as e:
            logger.error(f"Failed to get trade balance: {e}")
            return None

    def get_account_balance(self) -> Dict[str, Any]:
        """
        Get comprehensive account balance with USD conversion.
        
        Returns:
            {
                "total_usd": 50.0,          # Total portfolio value in USD
                "available_usd": 45.0,      # Available for trading (minus open orders)
                "holdings": [
                    {"symbol": "USD", "quantity": 45.0, "value_usd": 45.0},
                    {"symbol": "ETH", "quantity": 0.01, "value_usd": 32.0},
                ]
            }
            
        Note: Returns zeros if API fails.
        """
        result = {
            "total_usd": 0.0,
            "available_usd": 0.0,
            "holdings": [],
        }
        
        # Get raw balances
        raw_balance = self.get_balance()
        if not raw_balance:
            logger.warning("Failed to fetch balance from Kraken")
            return result
        
        logger.info(f"Fetched Kraken balance: {len(raw_balance)} asset(s)")
        
        # Get trade balance for available funds
        trade_balance = self.get_trade_balance()
        
        holdings = []
        total_usd = 0.0
        
        # Kraken asset name mappings
        usd_assets = {"ZUSD", "USD"}
        
        for asset, balance_str in raw_balance.items():
            quantity = float(balance_str)
            if quantity <= 0:
                continue
            
            # Normalize asset name (remove leading X/Z for crypto/fiat)
            symbol = asset
            if len(asset) == 4 and asset[0] in ("X", "Z"):
                symbol = asset[1:]
            
            # Convert XBT to BTC for display
            if symbol == "XBT":
                symbol = "BTC"
            
            value_usd = 0.0
            
            # USD holdings
            if asset in usd_assets or symbol == "USD":
                value_usd = quantity
            else:
                # Get price for crypto assets
                pair = f"{symbol}/USD"
                ticker = self.get_ticker(pair)
                if ticker:
                    # Ticker result keys vary; try common patterns
                    for key in ticker:
                        ticker_data = ticker[key]
                        if isinstance(ticker_data, dict) and "c" in ticker_data:
                            # 'c' is last trade closed [price, lot volume]
                            price = float(ticker_data["c"][0])
                            value_usd = quantity * price
                            break
            
            total_usd += value_usd
            holdings.append({
                "symbol": symbol,
                "quantity": round(quantity, 8),
                "value_usd": round(value_usd, 2),
            })
        
        result["total_usd"] = round(total_usd, 2)
        result["holdings"] = holdings
        
        # Calculate available USD from trade balance
        if trade_balance:
            # 'mf' = free margin, 'tb' = trade balance
            available = float(trade_balance.get("mf", trade_balance.get("tb", "0")))
            result["available_usd"] = round(available, 2)
        else:
            # Fallback: available = total (assume no margin used)
            result["available_usd"] = result["total_usd"]
        
        logger.info(
            f"Account balance: total=${result['total_usd']}, "
            f"available=${result['available_usd']}, holdings={len(holdings)}"
        )
        
        return result
    
    def get_asset_pairs(self) -> Dict[str, float]:
        """
        Get costmin for all trading pairs from Kraken AssetPairs API.
        
        Calls the public AssetPairs endpoint and extracts costmin field for each pair.
        Results are cached in Redis with 1-hour TTL.
        
        Returns:
            Dictionary mapping normalized pair to costmin (e.g., {"XBTUSD": 0.50, "ETHUSD": 0.50})
            Returns empty dict if API fails (caller should use default $0.50)
        """
        # Fetch from API
        try:
            _rate_limit()  # Respect rate limits for public endpoint too
            
            url = f"{KRAKEN_API_URL}/0/public/AssetPairs"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Check for API errors
            if data.get("error") and len(data["error"]) > 0:
                error_msg = ", ".join(data["error"])
                logger.warning(f"AssetPairs API error: {error_msg}, using default costmin $0.50")
                return {}
            
            result = data.get("result", {})
            if not result:
                logger.warning("Empty result from AssetPairs API, using default costmin $0.50")
                return {}
            
            # Extract costmin for each pair
            asset_pairs = {}
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            
            for pair_name, pair_info in result.items():
                # Skip darkpool pairs (contain ".d" suffix)
                if ".d" in pair_name:
                    continue
                
                # Get costmin (minimum order cost in quote currency)
                costmin_str = pair_info.get("costmin", "0.5")
                try:
                    costmin = float(costmin_str)
                except (ValueError, TypeError):
                    costmin = 0.5  # Default fallback
                
                # Normalize pair name (remove any special formatting)
                normalized_pair = pair_name
                
                # Cache individual pair in Redis
                try:
                    redis_client = get_redis_client()
                    cache_key = ASSET_PAIRS_CACHE_KEY.format(pair=normalized_pair)
                    # Redis hash values must be strings
                    cache_data = {
                        "costmin": str(costmin),
                        "updated_at": timestamp,
                    }
                    redis_client.hset(cache_key, mapping=cache_data)
                    redis_client.expire(cache_key, ASSET_PAIRS_CACHE_TTL)
                except Exception as e:
                    logger.debug(f"Failed to cache asset pair {normalized_pair}: {e}")
                
                asset_pairs[normalized_pair] = costmin
            
            logger.info(f"Fetched costmin for {len(asset_pairs)} trading pairs from Kraken")
            return asset_pairs
            
        except requests.RequestException as e:
            logger.warning(f"AssetPairs query failed: {e}, using default costmin $0.50")
            return {}
        except Exception as e:
            logger.warning(f"Unexpected error fetching AssetPairs: {e}, using default costmin $0.50")
            return {}
    
    def get_costmin(self, pair: str) -> float:
        """
        Get costmin for a specific trading pair.
        
        Checks Redis cache first, then falls back to API if cache miss.
        Uses default $0.50 if all else fails.
        
        Args:
            pair: Trading pair in Kraken format (e.g., "XBTUSD", "ETHUSD")
                  or standard format (e.g., "BTC/USD", "ETH/USD")
        
        Returns:
            Costmin value in USD (default: 0.50)
        """
        # Normalize pair to Kraken format
        normalized_pair = _normalize_symbol(pair) if "/" in pair else pair
        
        # Try cache first
        try:
            redis_client = get_redis_client()
            cache_key = ASSET_PAIRS_CACHE_KEY.format(pair=normalized_pair)
            cached_data = redis_client.hgetall(cache_key)
            
            if cached_data:
                costmin_str = cached_data.get("costmin")
                if costmin_str:
                    try:
                        costmin = float(costmin_str)
                        logger.debug(f"Cache hit: costmin for {normalized_pair} = ${costmin:.2f}")
                        return costmin
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.debug(f"Cache lookup failed for {normalized_pair}: {e}")
        
        # Cache miss - fetch from API
        logger.debug(f"Cache miss for {normalized_pair}, fetching from API")
        asset_pairs = self.get_asset_pairs()
        
        # Return costmin for this pair if found, otherwise default
        if asset_pairs and normalized_pair in asset_pairs:
            costmin = asset_pairs[normalized_pair]
            logger.debug(f"Costmin for {normalized_pair}: ${costmin:.2f}")
            return costmin
        
        # API failed or pair not found - use default
        logger.warning(f"AssetPairs query failed or pair {normalized_pair} not found, using default costmin $0.50")
        return 0.50