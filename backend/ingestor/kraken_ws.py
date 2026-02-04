"""Kraken WebSocket client for real-time market data ingestion."""

import asyncio
import json
import logging
import os
import signal
import socket
import ssl
import sys
import time
from typing import List, Optional, Set

import requests
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from backend.ingestor.config import get_max_symbols_per_ws
from backend.redis.keys import MARKET_RAW_STREAM
from backend.redis.streams import publish_to_stream

logger = logging.getLogger(__name__)

# Kraken WebSocket endpoint
KRAKEN_WS_URL = "wss://ws.kraken.com"

# Kraken OHLC interval mapping (interval string -> Kraken interval value in minutes)
KRAKEN_INTERVAL_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

# Reconnection backoff settings
RECONNECT_BASE_DELAY = 1.0  # Initial delay in seconds
RECONNECT_MAX_DELAY = 60.0  # Maximum delay cap

# Keepalive settings
HEARTBEAT_LOG_INTERVAL = 60  # Log heartbeat stats every 60 seconds
KEEPALIVE_TIMEOUT = 30  # Consider connection stale after 30s without messages

# Self-healing: force reconnect if no market data for this duration
NO_DATA_RECONNECT_TIMEOUT = 120  # 2 minutes

# Kraken REST API for connectivity test
KRAKEN_REST_API_URL = "https://api.kraken.com/0/public/Time"

# SSL context for secure WebSocket connection
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = True
SSL_CONTEXT.verify_mode = ssl.CERT_REQUIRED

# Headers to avoid connection blocking (Kraken may reject bot-like connections)
WS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)",
    "Origin": "https://www.kraken.com",
}


def _sync_check_kraken_rest_api() -> tuple[bool, str]:
    """
    Synchronous helper to check Kraken REST API.
    
    Returns:
        Tuple of (success, message)
    """
    try:
        resp = requests.get(KRAKEN_REST_API_URL, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            server_time = data.get("result", {}).get("unixtime", "unknown")
            return True, f"status={resp.status_code}, server_time={server_time}"
        else:
            return False, f"non-200 status: {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "timeout after 10s"
    except requests.exceptions.ConnectionError as e:
        return False, f"connection error: type={type(e).__name__}, msg={str(e)}"
    except requests.exceptions.RequestException as e:
        return False, f"request error: type={type(e).__name__}, msg={str(e)}"
    except Exception as e:
        return False, f"unexpected error: type={type(e).__name__}, msg={str(e)}"


async def check_kraken_connectivity() -> bool:
    """
    Check if Kraken API is reachable before attempting WebSocket connection.
    
    Performs DNS resolution and REST API health check.
    
    Returns:
        True if Kraken is reachable, False otherwise
    """
    # 1. DNS resolution check
    try:
        ip = socket.gethostbyname("ws.kraken.com")
        logger.info(f"DNS resolved ws.kraken.com -> {ip}")
    except socket.gaierror as e:
        logger.error(f"DNS resolution failed for ws.kraken.com: type={type(e).__name__}, msg={e}, repr={repr(e)}")
        return False
    except Exception as e:
        logger.error(f"DNS resolution error: type={type(e).__name__}, msg={e}, repr={repr(e)}")
        return False
    
    # 2. REST API reachability check (run sync requests in thread to avoid blocking)
    try:
        success, message = await asyncio.to_thread(_sync_check_kraken_rest_api)
        if success:
            logger.info(f"Kraken REST API reachable: {message}")
            return True
        else:
            logger.warning(f"Kraken REST API check failed: {message}")
            return False
    except Exception as e:
        logger.error(f"Kraken REST API check failed: type={type(e).__name__}, msg={e}, repr={repr(e)}")
        return False


class KrakenWebSocketClient:
    """
    Kraken WebSocket client with automatic reconnection.
    
    Connects to Kraken public WebSocket, subscribes to OHLC channels,
    and publishes raw ticks to Redis Streams.
    """

    def __init__(
        self,
        symbols: List[str],
        intervals: Optional[List[str]] = None,
        reconnect_delay: float = 5.0,
        connection_id: int = 0,
    ):
        """
        Initialize the Kraken WebSocket client.
        
        Args:
            symbols: List of trading pairs (e.g., ["BTC/USD", "ETH/USD"])
            intervals: List of OHLC intervals to subscribe to (e.g., ["1m", "5m"])
            reconnect_delay: Fixed delay after exponential backoff (default: 5s)
            connection_id: Identifier for this connection (for logging)
        """
        self.symbols = symbols
        self.intervals = intervals or ["1m", "5m"]
        self.reconnect_delay = reconnect_delay
        self.connection_id = connection_id
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self._reconnect_attempts = 0
        self._subscription_ids: Set[int] = set()
        self._subscribed_count = 0
        
        # Heartbeat and keepalive tracking
        self._last_message_time: float = 0.0
        self._last_heartbeat_time: float = 0.0
        self._heartbeat_count: int = 0
        self._last_heartbeat_log_time: float = 0.0
        
        # Self-healing: track last market data received (separate from heartbeats)
        self._last_data_received: float = 0.0

    async def connect(self) -> bool:
        """
        Connect to Kraken WebSocket endpoint.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(
                f"[Conn-{self.connection_id}] Connecting to Kraken WebSocket at {KRAKEN_WS_URL}"
            )
            # Use SSL context and headers to avoid connection blocking
            # Disable websockets library ping - Kraken uses application-level heartbeats
            # Use longer open_timeout for initial handshake (Kraken can be slow)
            self.websocket = await websockets.connect(
                KRAKEN_WS_URL,
                ssl=SSL_CONTEXT,
                additional_headers=WS_HEADERS,  # websockets 16.x uses additional_headers
                ping_interval=None,  # Disable protocol-level ping, use Kraken heartbeat
                ping_timeout=30,
                open_timeout=60,  # 60s for initial handshake
                close_timeout=10,
            )
            logger.info(f"[Conn-{self.connection_id}] Connected to Kraken WebSocket")
            self._reconnect_attempts = 0
            self._last_message_time = time.time()
            self._last_data_received = time.time()
            self._heartbeat_count = 0
            self._last_heartbeat_log_time = time.time()
            return True
        except ConnectionResetError as e:
            logger.error(
                f"[Conn-{self.connection_id}] TLS handshake reset by Kraken: "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            logger.error(
                f"[Conn-{self.connection_id}] This may indicate Kraken is blocking the connection. "
                "Check: 1) SSL context 2) User-Agent headers 3) Network/firewall"
            )
            return False
        except ConnectionRefusedError as e:
            logger.error(
                f"[Conn-{self.connection_id}] WebSocket connection refused: "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            return False
        except OSError as e:
            logger.error(
                f"[Conn-{self.connection_id}] WebSocket OS/network error: "
                f"type={type(e).__name__}, errno={getattr(e, 'errno', 'N/A')}, msg={str(e)}, repr={repr(e)}"
            )
            return False
        except asyncio.TimeoutError as e:
            logger.error(
                f"[Conn-{self.connection_id}] WebSocket connection timeout (60s): "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            return False
        except WebSocketException as e:
            logger.error(
                f"[Conn-{self.connection_id}] WebSocket protocol error: "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            return False
        except Exception as e:
            logger.error(
                f"[Conn-{self.connection_id}] Failed to connect to Kraken WebSocket: "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            return False

    async def subscribe(self) -> bool:
        """
        Subscribe to OHLC channels for configured symbols and intervals.
        
        Subscriptions are batched to avoid overwhelming Kraken's WebSocket.
        
        Returns:
            True if subscription successful, False otherwise
        """
        if not self.websocket:
            logger.error(f"[Conn-{self.connection_id}] Cannot subscribe: WebSocket not connected")
            return False

        try:
            self._subscribed_count = 0
            
            # Batch subscriptions to avoid timeout/rate limiting
            BATCH_SIZE = 10
            total_symbols = len(self.symbols)
            
            for batch_start in range(0, total_symbols, BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, total_symbols)
                batch = self.symbols[batch_start:batch_end]
                batch_num = (batch_start // BATCH_SIZE) + 1
                total_batches = (total_symbols + BATCH_SIZE - 1) // BATCH_SIZE
                
                logger.info(
                    f"[Conn-{self.connection_id}] Subscribing batch {batch_num}/{total_batches} "
                    f"({len(batch)} symbols)"
                )
                
                await self._subscribe_batch(batch)
                
                # Delay between batches (skip after last batch)
                if batch_end < total_symbols:
                    await asyncio.sleep(1.0)
            
            logger.info(
                f"[Conn-{self.connection_id}] Subscribed to {self._subscribed_count} OHLC channels "
                f"({len(self.symbols)} symbols x {len(self.intervals)} intervals)"
            )
            # Reset keepalive timer after subscription to avoid immediate stale detection
            self._last_message_time = time.time()
            return True
        except Exception as e:
            logger.error(f"[Conn-{self.connection_id}] Failed to subscribe: {e}")
            return False

    async def _subscribe_batch(self, symbols: List[str]) -> None:
        """
        Subscribe to OHLC channels for a batch of symbols.
        
        Args:
            symbols: List of symbols in this batch
        """
        for symbol in symbols:
            # Convert symbol format: BTC/USD -> XBT/USD (Kraken format)
            kraken_pair = self._normalize_symbol(symbol)
            
            for interval in self.intervals:
                # Get Kraken interval value (in minutes)
                kraken_interval = KRAKEN_INTERVAL_MAP.get(interval)
                if kraken_interval is None:
                    logger.warning(
                        f"[Conn-{self.connection_id}] Unknown interval {interval}, skipping"
                    )
                    continue
                
                # Subscribe to OHLC for this interval
                ohlc_sub = {
                    "event": "subscribe",
                    "pair": [kraken_pair],
                    "subscription": {"name": "ohlc", "interval": kraken_interval},
                }
                await self.websocket.send(json.dumps(ohlc_sub))
                self._subscribed_count += 1
                
                # Small delay within batch to avoid burst
                await asyncio.sleep(0.05)

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Convert symbol format to Kraken format.
        
        Args:
            symbol: Symbol in format "BTC/USD" or "ETH/USD"
            
        Returns:
            Kraken-formatted pair (e.g., "XBT/USD" for BTC/USD)
        """
        # Kraken uses XBT instead of BTC
        if symbol.startswith("BTC/"):
            return symbol.replace("BTC/", "XBT/")
        return symbol

    def _denormalize_symbol(self, kraken_pair: str) -> str:
        """
        Convert Kraken format back to standard format.
        
        Handles various Kraken naming conventions:
        - XBT -> BTC
        - X prefix removal (XETH/USD -> ETH/USD)
        - Z suffix removal (XETHZ/USD -> ETH/USD)
        
        Args:
            kraken_pair: Kraken-formatted pair (e.g., "XBT/USD", "XETHZ/USD")
            
        Returns:
            Standard format (e.g., "BTC/USD", "ETH/USD")
        """
        from backend.ingestor.symbols import normalize_symbol
        return normalize_symbol(kraken_pair)

    async def _handle_message(self, message: str) -> None:
        """
        Handle incoming WebSocket message.
        
        Args:
            message: JSON string from WebSocket
        """
        # Track last message time for keepalive monitoring
        self._last_message_time = time.time()
        
        try:
            data = json.loads(message)
            
            # Handle event messages (heartbeat, ping, error, subscriptionStatus)
            if isinstance(data, dict) and "event" in data:
                await self._handle_event_message(data)
                return
            
            # Handle subscription confirmation and market data
            if isinstance(data, list) and len(data) > 0:
                channel_id = data[0]
                
                # Subscription confirmation (channel ID is a number)
                if isinstance(channel_id, int) and len(data) >= 2:
                    if isinstance(data[1], dict) and "event" in data[1]:
                        event = data[1].get("event")
                        if event == "subscriptionStatus":
                            status = data[1].get("status")
                            channel_name = data[1].get("channelName", "")
                            pair = data[1].get("pair", "")
                            
                            if status == "subscribed":
                                self._subscription_ids.add(channel_id)
                                logger.info(
                                    f"Subscription confirmed: channel={channel_id}, "
                                    f"name={channel_name}, pair={pair}"
                                )
                            elif status == "error":
                                error_msg = data[1].get("errorMessage", "Unknown error")
                                logger.error(f"Subscription error: {error_msg}")
                    else:
                        # Market data message: [channel_id, data, channel_name, pair]
                        if len(data) >= 4:
                            channel_id, payload, channel_name, pair = (
                                data[0],
                                data[1],
                                data[2],
                                data[3],
                            )
                            
                            # Track market data received for self-healing
                            self._last_data_received = time.time()
                            
                            # Normalize pair back to standard format
                            normalized_pair = self._denormalize_symbol(pair)
                            
                            # Publish raw tick to Redis Stream
                            stream_key = MARKET_RAW_STREAM.format(symbol=normalized_pair)
                            
                            # Prepare message data
                            tick_data = {
                                "channel_id": str(channel_id),
                                "channel_name": channel_name,
                                "pair": normalized_pair,
                                "kraken_pair": pair,
                                "payload": json.dumps(payload) if isinstance(payload, (list, dict)) else str(payload),
                                "timestamp": time.time(),
                            }
                            
                            # If payload is a list/array (OHLC or ticker data), include structured fields
                            if isinstance(payload, list) and len(payload) > 0:
                                if channel_name == "ticker":
                                    # Ticker format: [a, b, c, v, p, t, l, h, o]
                                    # a=ask, b=bid, c=last trade, v=volume, p=vwap, t=trades, l=low, h=high, o=open
                                    tick_data["type"] = "ticker"
                                    if len(payload) >= 9:
                                        tick_data["ask"] = str(payload[0][0]) if isinstance(payload[0], list) else str(payload[0])
                                        tick_data["bid"] = str(payload[1][0]) if isinstance(payload[1], list) else str(payload[1])
                                        tick_data["last"] = str(payload[2][0]) if isinstance(payload[2], list) else str(payload[2])
                                        tick_data["volume"] = str(payload[3][1]) if len(payload) > 3 and isinstance(payload[3], list) else ""
                                elif "ohlc" in channel_name.lower():
                                    # OHLC format: [time, etime, open, high, low, close, vwap, volume, count]
                                    tick_data["type"] = "ohlc"
                                    if len(payload) >= 9:
                                        tick_data["time"] = str(payload[0])
                                        tick_data["etime"] = str(payload[1])
                                        tick_data["open"] = str(payload[2])
                                        tick_data["high"] = str(payload[3])
                                        tick_data["low"] = str(payload[4])
                                        tick_data["close"] = str(payload[5])
                                        tick_data["vwap"] = str(payload[6])
                                        tick_data["volume"] = str(payload[7])
                                        tick_data["count"] = str(payload[8])
                            
                            try:
                                message_id = publish_to_stream(stream_key, tick_data)
                                logger.debug(
                                    f"Published tick to {stream_key}: message_id={message_id}"
                                )
                            except Exception as e:
                                logger.error(f"Failed to publish to Redis stream {stream_key}: {e}")
                    
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse WebSocket message: {e}, message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")

    async def _handle_event_message(self, data: dict) -> None:
        """
        Handle Kraken event messages (heartbeat, ping, error, etc.).
        
        Args:
            data: Parsed JSON event message
        """
        event = data.get("event")
        
        if event == "heartbeat":
            self._last_heartbeat_time = time.time()
            self._heartbeat_count += 1
            
            # Log heartbeat periodically (every HEARTBEAT_LOG_INTERVAL seconds)
            now = time.time()
            if now - self._last_heartbeat_log_time >= HEARTBEAT_LOG_INTERVAL:
                logger.info(
                    f"[Conn-{self.connection_id}] Heartbeat OK - "
                    f"received {self._heartbeat_count} heartbeats, "
                    f"connection alive for {int(now - self._last_heartbeat_log_time)}s"
                )
                self._last_heartbeat_log_time = now
                self._heartbeat_count = 0
            else:
                logger.debug(f"[Conn-{self.connection_id}] Received heartbeat from Kraken")
                
        elif event == "ping":
            # Respond to Kraken ping with pong
            logger.debug(f"[Conn-{self.connection_id}] Received ping, sending pong")
            if self.websocket:
                try:
                    pong_message = {"event": "pong"}
                    # Include reqid if provided by Kraken
                    if "reqid" in data:
                        pong_message["reqid"] = data["reqid"]
                    await self.websocket.send(json.dumps(pong_message))
                except Exception as e:
                    logger.warning(f"[Conn-{self.connection_id}] Failed to send pong: {e}")
                    
        elif event == "pong":
            # Response to our ping (if we sent one)
            logger.debug(f"[Conn-{self.connection_id}] Received pong from Kraken")
            
        elif event == "systemStatus":
            status = data.get("status", "unknown")
            version = data.get("version", "unknown")
            logger.info(
                f"[Conn-{self.connection_id}] Kraken system status: {status}, version: {version}"
            )
            
        elif event == "subscriptionStatus":
            status = data.get("status")
            channel_name = data.get("channelName", "")
            pair = data.get("pair", "")
            
            if status == "subscribed":
                logger.info(
                    f"[Conn-{self.connection_id}] Subscription confirmed: "
                    f"name={channel_name}, pair={pair}"
                )
            elif status == "error":
                error_msg = data.get("errorMessage", "Unknown error")
                logger.error(f"[Conn-{self.connection_id}] Subscription error: {error_msg}")
                
        elif event == "error":
            error_msg = data.get("errorMessage", "Unknown error")
            logger.error(f"[Conn-{self.connection_id}] Kraken WebSocket error: {error_msg}")

    async def _reconnect(self) -> None:
        """
        Reconnect to WebSocket with exponential backoff (1s, 2s, 4s, ... max 60s).
        """
        start_attempt = self._reconnect_attempts
        
        while self.running:
            self._reconnect_attempts += 1
            attempt_num = self._reconnect_attempts
            
            # Calculate delay: exponential backoff with max cap
            # 1s, 2s, 4s, 8s, 16s, 32s, 60s, 60s, ...
            delay = min(
                RECONNECT_BASE_DELAY * (2 ** (attempt_num - 1)),
                RECONNECT_MAX_DELAY
            )
            
            logger.warning(
                f"WebSocket disconnected, reconnecting (attempt {attempt_num})..."
            )
            await asyncio.sleep(delay)
            
            if await self.connect():
                if await self.subscribe():
                    total_attempts = attempt_num - start_attempt
                    logger.info(
                        f"WebSocket reconnected successfully after {total_attempts} attempt(s)"
                    )
                    return
                else:
                    logger.warning(
                        f"[Conn-{self.connection_id}] Connected but subscription failed, retrying..."
                    )

    async def _check_keepalive(self) -> bool:
        """
        Check if connection is still alive based on last message time.
        
        Returns:
            True if connection is healthy, False if stale
        """
        if self._last_message_time == 0:
            return True
        
        elapsed = time.time() - self._last_message_time
        if elapsed > KEEPALIVE_TIMEOUT:
            logger.warning(
                f"[Conn-{self.connection_id}] Connection stale - no messages for {elapsed:.1f}s"
            )
            return False
        return True

    def _check_data_received(self) -> bool:
        """
        Check if market data has been received recently (self-healing).
        
        If no market data for 2 minutes, force reconnect.
        
        Returns:
            True if data received recently, False if stale
        """
        if self._last_data_received == 0:
            return True
        
        elapsed = time.time() - self._last_data_received
        if elapsed > NO_DATA_RECONNECT_TIMEOUT:
            logger.warning(
                f"No data received for 2 minutes, forcing reconnect"
            )
            return False
        return True

    async def run(self) -> None:
        """Main event loop: connect, subscribe, and process messages."""
        self.running = True
        
        # Pre-connection diagnostics (only for first connection)
        if self.connection_id == 0:
            logger.info(f"[Conn-{self.connection_id}] Running pre-connection diagnostics...")
            connectivity_ok = await check_kraken_connectivity()
            if not connectivity_ok:
                logger.error(
                    f"[Conn-{self.connection_id}] Pre-connection diagnostics failed. "
                    "Check network/firewall/DNS settings."
                )
                # Continue anyway - WebSocket might still work
        
        # Initial connection
        if not await self.connect():
            logger.error(
                f"[Conn-{self.connection_id}] Initial connection failed, starting reconnection loop"
            )
            await self._reconnect()
            return
        
        if not await self.subscribe():
            logger.error(
                f"[Conn-{self.connection_id}] Initial subscription failed, starting reconnection loop"
            )
            await self._reconnect()
            return
        
        # Message processing loop
        while self.running:
            try:
                if not self.websocket:
                    logger.warning(
                        f"[Conn-{self.connection_id}] WebSocket disconnected, reconnecting..."
                    )
                    await self._reconnect()
                    continue
                
                # Check keepalive - reconnect if no messages received recently
                if not await self._check_keepalive():
                    logger.warning(
                        f"[Conn-{self.connection_id}] Keepalive check failed, reconnecting..."
                    )
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                    await self._reconnect()
                    continue
                
                # Self-healing: check if market data received recently
                if not self._check_data_received():
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                    await self._reconnect()
                    continue
                
                # Receive message with timeout to allow checking self.running and keepalive
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(), timeout=5.0
                    )
                    await self._handle_message(message)
                except asyncio.TimeoutError:
                    # Timeout is expected, continue loop to check self.running and keepalive
                    continue
                    
            except ConnectionClosed as e:
                logger.warning(
                    f"[Conn-{self.connection_id}] WebSocket connection closed: "
                    f"type={type(e).__name__}, code={getattr(e, 'code', 'N/A')}, "
                    f"reason={getattr(e, 'reason', 'N/A')}, repr={repr(e)}, reconnecting..."
                )
                self.websocket = None
                if self.running:
                    await self._reconnect()
            except WebSocketException as e:
                logger.error(
                    f"[Conn-{self.connection_id}] WebSocket error: "
                    f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}, reconnecting..."
                )
                self.websocket = None
                if self.running:
                    await self._reconnect()
            except Exception as e:
                logger.error(
                    f"[Conn-{self.connection_id}] Unexpected error in message loop: "
                    f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
                )
                if self.running:
                    await asyncio.sleep(1)  # Brief pause before retry

    async def stop(self) -> None:
        """Stop the WebSocket client gracefully."""
        logger.info(f"[Conn-{self.connection_id}] Stopping Kraken WebSocket client...")
        self.running = False
        
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info(f"[Conn-{self.connection_id}] WebSocket connection closed")
            except Exception as e:
                logger.warning(f"[Conn-{self.connection_id}] Error closing WebSocket: {e}")
    
    def get_subscription_count(self) -> int:
        """Return the number of subscriptions for this connection."""
        return self._subscribed_count


class MultiConnectionManager:
    """
    Manages multiple Kraken WebSocket connections to handle subscription limits.
    
    Kraken limits subscriptions per connection. This manager splits symbols
    across multiple connections to subscribe to all required pairs.
    """

    def __init__(
        self,
        symbols: List[str],
        intervals: Optional[List[str]] = None,
        max_symbols_per_connection: Optional[int] = None,
    ):
        """
        Initialize the multi-connection manager.
        
        Args:
            symbols: Full list of trading pairs to subscribe to
            intervals: List of OHLC intervals to subscribe to (e.g., ["1m", "5m"])
            max_symbols_per_connection: Maximum symbols per WebSocket connection
        """
        # Debug mode: limit to single symbol for isolation testing
        if os.getenv("DEBUG_WS") == "1":
            original_count = len(symbols)
            symbols = symbols[:1]  # Just first symbol (usually BTC/USD)
            logger.warning(
                f"DEBUG_WS=1: Limiting symbols from {original_count} to 1 ({symbols[0]}) for debugging"
            )
        
        self.symbols = symbols
        self.intervals = intervals or ["1m", "5m"]
        self.max_symbols_per_connection = max_symbols_per_connection or get_max_symbols_per_ws()
        self.clients: List[KrakenWebSocketClient] = []
        self.running = False
        
        # Split symbols into chunks for multiple connections
        self._create_clients()

    def _create_clients(self) -> None:
        """Create WebSocket clients for each chunk of symbols."""
        # Split symbols into chunks
        chunks = [
            self.symbols[i : i + self.max_symbols_per_connection]
            for i in range(0, len(self.symbols), self.max_symbols_per_connection)
        ]
        
        logger.info(
            f"Creating {len(chunks)} WebSocket connection(s) for {len(self.symbols)} symbols "
            f"(max {self.max_symbols_per_connection} symbols per connection)"
        )
        
        for i, chunk in enumerate(chunks):
            client = KrakenWebSocketClient(
                symbols=chunk,
                intervals=self.intervals,
                connection_id=i,
            )
            self.clients.append(client)
            logger.info(
                f"[Conn-{i}] Assigned {len(chunk)} symbols: "
                f"{chunk[:3]}{'...' if len(chunk) > 3 else ''}"
            )

    async def run(self) -> None:
        """Run all WebSocket clients concurrently."""
        if not self.clients:
            logger.error("No WebSocket clients to run")
            return
        
        self.running = True
        
        logger.info(
            f"Starting {len(self.clients)} WebSocket connection(s) "
            f"for {len(self.symbols)} symbols with intervals {self.intervals}"
        )
        
        # Run all clients concurrently
        tasks = [asyncio.create_task(client.run()) for client in self.clients]
        
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Log any exceptions from individual tasks
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"[Conn-{i}] Task failed: type={type(result).__name__}, "
                        f"msg={str(result)}, repr={repr(result)}"
                    )
        except Exception as e:
            logger.error(
                f"Fatal error in multi-connection manager: "
                f"type={type(e).__name__}, msg={str(e)}, repr={repr(e)}"
            )
            raise
        finally:
            self.running = False

    async def stop(self) -> None:
        """Stop all WebSocket clients gracefully."""
        logger.info(f"Stopping {len(self.clients)} WebSocket connection(s)...")
        self.running = False
        
        # Stop all clients concurrently
        stop_tasks = [client.stop() for client in self.clients]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        logger.info("All WebSocket connections stopped")

    def get_total_subscriptions(self) -> int:
        """Return total number of subscriptions across all connections."""
        return sum(client.get_subscription_count() for client in self.clients)

    def get_connection_count(self) -> int:
        """Return number of WebSocket connections."""
        return len(self.clients)


async def main_async(symbols: List[str], intervals: Optional[List[str]] = None) -> None:
    """
    Async main function for running the WebSocket client(s).
    
    Args:
        symbols: List of trading pairs to subscribe to
        intervals: List of OHLC intervals to subscribe to
    """
    # Use MultiConnectionManager for handling multiple connections
    manager = MultiConnectionManager(symbols=symbols, intervals=intervals)
    client = manager  # For compatibility with existing shutdown logic
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, initiating graceful shutdown...")
        shutdown_event.set()
    
    # Register signal handlers
    if sys.platform != "win32":  # Signal handlers not supported on Windows
        loop.add_signal_handler(signal.SIGTERM, signal_handler, signal.SIGTERM, None)
        loop.add_signal_handler(signal.SIGINT, signal_handler, signal.SIGINT, None)
    else:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    # Run client in background task
    client_task = asyncio.create_task(client.run())
    
    try:
        # Wait for shutdown signal or client task completion
        done, pending = await asyncio.wait(
            [client_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        
        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Stop client gracefully
        await client.stop()
        
        # Wait for client task to finish
        if not client_task.done():
            try:
                await asyncio.wait_for(client_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Client task did not stop within timeout")
                client_task.cancel()
                
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        await client.stop()
    except Exception as e:
        logger.error(f"Fatal error in WebSocket client: {e}")
        await client.stop()
        raise
