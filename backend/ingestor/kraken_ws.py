"""Kraken WebSocket client for real-time market data ingestion."""

import asyncio
import json
import logging
import signal
import sys
import time
from typing import List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from backend.redis.keys import MARKET_RAW_STREAM
from backend.redis.streams import publish_to_stream

logger = logging.getLogger(__name__)

# Kraken WebSocket endpoint
KRAKEN_WS_URL = "wss://ws.kraken.com"


class KrakenWebSocketClient:
    """
    Kraken WebSocket client with automatic reconnection.
    
    Connects to Kraken public WebSocket, subscribes to ticker/OHLC channels,
    and publishes raw ticks to Redis Streams.
    """

    def __init__(self, symbols: List[str], reconnect_delay: float = 5.0):
        """
        Initialize the Kraken WebSocket client.
        
        Args:
            symbols: List of trading pairs (e.g., ["BTC/USD", "ETH/USD"])
            reconnect_delay: Fixed delay after exponential backoff (default: 5s)
        """
        self.symbols = symbols
        self.reconnect_delay = reconnect_delay
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self._reconnect_attempts = 0
        self._max_exponential_attempts = 3
        self._subscription_ids: Set[int] = set()

    async def connect(self) -> bool:
        """
        Connect to Kraken WebSocket endpoint.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to Kraken WebSocket at {KRAKEN_WS_URL}")
            self.websocket = await websockets.connect(
                KRAKEN_WS_URL,
                ping_interval=20,  # Keep-alive ping every 20 seconds
                ping_timeout=10,
            )
            logger.info("Connected to Kraken WebSocket")
            self._reconnect_attempts = 0
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Kraken WebSocket: {e}")
            return False

    async def subscribe(self) -> bool:
        """
        Subscribe to ticker and OHLC channels for configured symbols.
        
        Returns:
            True if subscription successful, False otherwise
        """
        if not self.websocket:
            logger.error("Cannot subscribe: WebSocket not connected")
            return False

        try:
            # Subscribe to ticker channel for each symbol
            for symbol in self.symbols:
                # Convert symbol format: BTC/USD -> XBT/USD (Kraken format)
                kraken_pair = self._normalize_symbol(symbol)
                
                # Subscribe to ticker
                ticker_sub = {
                    "event": "subscribe",
                    "pair": [kraken_pair],
                    "subscription": {"name": "ticker"},
                }
                await self.websocket.send(json.dumps(ticker_sub))
                logger.info(f"Subscribed to ticker for {symbol} (Kraken: {kraken_pair})")

                # Subscribe to OHLC (1-minute intervals for raw tick aggregation)
                ohlc_sub = {
                    "event": "subscribe",
                    "pair": [kraken_pair],
                    "subscription": {"name": "ohlc", "interval": 1},
                }
                await self.websocket.send(json.dumps(ohlc_sub))
                logger.info(f"Subscribed to OHLC for {symbol} (Kraken: {kraken_pair})")

            return True
        except Exception as e:
            logger.error(f"Failed to subscribe: {e}")
            return False

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
        
        Args:
            kraken_pair: Kraken-formatted pair (e.g., "XBT/USD")
            
        Returns:
            Standard format (e.g., "BTC/USD")
        """
        if kraken_pair.startswith("XBT/"):
            return kraken_pair.replace("XBT/", "BTC/")
        return kraken_pair

    async def _handle_message(self, message: str) -> None:
        """
        Handle incoming WebSocket message.
        
        Args:
            message: JSON string from WebSocket
        """
        try:
            data = json.loads(message)
            
            # Handle subscription confirmation
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
            
            # Handle error messages
            elif isinstance(data, dict) and "event" in data:
                event = data.get("event")
                if event == "error":
                    error_msg = data.get("errorMessage", "Unknown error")
                    logger.error(f"Kraken WebSocket error: {error_msg}")
                elif event == "heartbeat":
                    logger.debug("Received heartbeat from Kraken")
                    
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse WebSocket message: {e}, message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")

    async def _reconnect(self) -> None:
        """
        Reconnect to WebSocket with exponential backoff, then fixed delay.
        """
        while self.running:
            # Calculate delay: exponential backoff for first 3 attempts, then fixed
            if self._reconnect_attempts < self._max_exponential_attempts:
                delay = min(2 ** self._reconnect_attempts, 5.0)  # 1s, 2s, 4s, max 5s
            else:
                delay = self.reconnect_delay  # Fixed 5s after exponential attempts
            
            logger.warning(
                f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts + 1})..."
            )
            await asyncio.sleep(delay)
            
            if await self.connect():
                if await self.subscribe():
                    logger.info("Reconnected and resubscribed successfully")
                    return
                else:
                    logger.warning("Connected but subscription failed, retrying...")
            else:
                self._reconnect_attempts += 1

    async def run(self) -> None:
        """Main event loop: connect, subscribe, and process messages."""
        self.running = True
        
        # Initial connection
        if not await self.connect():
            logger.error("Initial connection failed, starting reconnection loop")
            await self._reconnect()
            return
        
        if not await self.subscribe():
            logger.error("Initial subscription failed, starting reconnection loop")
            await self._reconnect()
            return
        
        # Message processing loop
        while self.running:
            try:
                if not self.websocket:
                    logger.warning("WebSocket disconnected, reconnecting...")
                    await self._reconnect()
                    continue
                
                # Receive message with timeout to allow checking self.running
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(), timeout=1.0
                    )
                    await self._handle_message(message)
                except asyncio.TimeoutError:
                    # Timeout is expected, continue loop to check self.running
                    continue
                    
            except ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                if self.running:
                    await self._reconnect()
            except WebSocketException as e:
                logger.error(f"WebSocket error: {e}, reconnecting...")
                if self.running:
                    await self._reconnect()
            except Exception as e:
                logger.error(f"Unexpected error in message loop: {e}")
                if self.running:
                    await asyncio.sleep(1)  # Brief pause before retry

    async def stop(self) -> None:
        """Stop the WebSocket client gracefully."""
        logger.info("Stopping Kraken WebSocket client...")
        self.running = False
        
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info("WebSocket connection closed")
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")


async def main_async(symbols: List[str]) -> None:
    """
    Async main function for running the WebSocket client.
    
    Args:
        symbols: List of trading pairs to subscribe to
    """
    client = KrakenWebSocketClient(symbols=symbols)
    
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
