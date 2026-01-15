"""Kraken REST API client for order execution."""

import logging
import time
from typing import Dict, List, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from backend.execution.auth import get_auth_headers, generate_nonce, KRAKEN_API_URL

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
