"""OHLCV bar aggregation logic for normalizing raw market data."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.ingestor.bar_builder import BarBuilder
from backend.redis.keys import MARKET_RAW_STREAM, MARKET_OHLCV_STREAM
from backend.redis.streams import consume_stream, publish_to_stream

logger = logging.getLogger(__name__)

# Consumer group and name for Redis Streams
CONSUMER_GROUP = "normalizer"
CONSUMER_NAME = "normalizer-1"


class Normalizer:
    """
    Consumes raw ticks from Redis Streams and aggregates them into OHLCV bars.
    
    Reads from market:raw:{symbol} streams and publishes normalized bars to
    market:ohlcv:{symbol}:{interval} streams.
    """

    def __init__(self, symbols: List[str], intervals: List[str]):
        """
        Initialize the normalizer.
        
        Args:
            symbols: List of trading pairs (e.g., ["BTC/USD", "ETH/USD"])
            intervals: List of intervals to aggregate (e.g., ["4h", "1d"])
        """
        self.symbols = symbols
        self.intervals = intervals
        self.running = False
        
        # Create bar builders for each symbol-interval combination
        self.bar_builders: Dict[str, Dict[str, BarBuilder]] = {}
        for symbol in symbols:
            self.bar_builders[symbol] = {}
            for interval in intervals:
                self.bar_builders[symbol][interval] = BarBuilder(interval)

    def _parse_tick_data(self, message_data: Dict) -> Optional[Dict]:
        """
        Parse raw tick message from Redis Stream into structured tick data.
        
        Args:
            message_data: Raw message data from Redis Stream
            
        Returns:
            Dict with price, volume, timestamp, symbol, or None if parsing fails
        """
        try:
            # Extract symbol from pair field
            symbol = message_data.get("pair", "")
            if not symbol:
                return None
            
            # Extract price and volume based on message type
            msg_type = message_data.get("type", "")
            price = None
            volume = 0.0
            
            if msg_type == "ticker":
                # Ticker format: use last trade price
                last_str = message_data.get("last", "")
                if last_str:
                    price = float(last_str)
                volume_str = message_data.get("volume", "0")
                if volume_str:
                    volume = float(volume_str)
            elif msg_type == "ohlc":
                # OHLC format: use close price
                close_str = message_data.get("close", "")
                if close_str:
                    price = float(close_str)
                volume_str = message_data.get("volume", "0")
                if volume_str:
                    volume = float(volume_str)
            else:
                # Try to extract price from payload if available
                payload_str = message_data.get("payload", "")
                if payload_str:
                    try:
                        payload = json.loads(payload_str)
                        if isinstance(payload, list) and len(payload) >= 5:
                            # Assume OHLC format: [time, etime, open, high, low, close, ...]
                            price = float(payload[5])  # close price
                            if len(payload) >= 8:
                                volume = float(payload[7])
                    except (json.JSONDecodeError, (ValueError, IndexError)):
                        pass
            
            if price is None:
                return None
            
            # Extract timestamp
            timestamp_str = message_data.get("timestamp", "")
            if timestamp_str:
                try:
                    # Try parsing as Unix timestamp (float)
                    timestamp = datetime.fromtimestamp(float(timestamp_str), tz=timezone.utc)
                except (ValueError, OSError):
                    # Try parsing as ISO format
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    except ValueError:
                        logger.warning(f"Could not parse timestamp: {timestamp_str}")
                        timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
            
            return {
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "timestamp": timestamp,
            }
        except Exception as e:
            logger.warning(f"Failed to parse tick data: {e}, message_data keys: {list(message_data.keys())}")
            return None

    async def _process_tick(self, symbol: str, tick_data: Dict) -> None:
        """
        Process a single tick and update all bar builders for the symbol.
        
        Args:
            symbol: Trading pair symbol
            tick_data: Parsed tick data with price, volume, timestamp
        """
        price = tick_data["price"]
        volume = tick_data["volume"]
        timestamp = tick_data["timestamp"]
        
        # Update all bar builders for this symbol
        for interval, bar_builder in self.bar_builders[symbol].items():
            completed_bar = bar_builder.add_tick(price, volume, timestamp, symbol)
            
            # If a bar was completed, publish it
            if completed_bar:
                stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
                try:
                    message_id = publish_to_stream(stream_key, completed_bar)
                    logger.debug(
                        f"Published OHLCV bar to {stream_key}: "
                        f"interval={interval}, timestamp={completed_bar['timestamp']}, "
                        f"message_id={message_id}"
                    )
                except Exception as e:
                    logger.error(f"Failed to publish OHLCV bar to {stream_key}: {e}")

    async def _consume_symbol(self, symbol: str) -> None:
        """
        Consume raw ticks for a single symbol and process them.
        
        Args:
            symbol: Trading pair symbol to consume
        """
        stream_key = MARKET_RAW_STREAM.format(symbol=symbol)
        
        logger.info(f"Starting to consume raw ticks from {stream_key}")
        
        while self.running:
            try:
                # Consume messages with blocking (wait up to 1 second)
                # Use asyncio.to_thread to avoid blocking the event loop
                messages = await asyncio.to_thread(
                    consume_stream,
                    stream_key=stream_key,
                    consumer_group=CONSUMER_GROUP,
                    consumer_name=CONSUMER_NAME,
                    count=10,  # Process up to 10 messages at a time
                    block=1000,  # Block for 1 second if no messages
                )
                
                for message in messages:
                    tick_data = self._parse_tick_data(message["data"])
                    if tick_data:
                        await self._process_tick(symbol, tick_data)
                    else:
                        logger.debug(f"Skipping unparseable message: {message.get('id', 'unknown')}")
                
                # Small delay to prevent tight loop if no messages
                if not messages:
                    await asyncio.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Error consuming from {stream_key}: {e}")
                # Wait before retrying
                await asyncio.sleep(1)

    async def run(self) -> None:
        """Run the normalizer for all configured symbols."""
        self.running = True
        logger.info(f"Starting normalizer for symbols: {', '.join(self.symbols)}, intervals: {', '.join(self.intervals)}")
        
        # Start consumer tasks for each symbol
        tasks = [self._consume_symbol(symbol) for symbol in self.symbols]
        
        try:
            # Run all consumers concurrently
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Fatal error in normalizer: {e}", exc_info=True)
            raise
        finally:
            self.running = False
            logger.info("Normalizer stopped")

    async def stop(self) -> None:
        """Stop the normalizer gracefully."""
        logger.info("Stopping normalizer...")
        self.running = False
        
        # Flush any incomplete bars
        for symbol in self.symbols:
            for interval, bar_builder in self.bar_builders[symbol].items():
                completed_bar = bar_builder.flush_bar()
                if completed_bar:
                    stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
                    try:
                        publish_to_stream(stream_key, completed_bar)
                        logger.info(f"Flushed final bar for {symbol} {interval}")
                    except Exception as e:
                        logger.warning(f"Failed to flush bar for {symbol} {interval}: {e}")


async def main_async(symbols: List[str], intervals: List[str]) -> None:
    """
    Async main function for running the normalizer.
    
    Args:
        symbols: List of trading pairs to process
        intervals: List of intervals to aggregate (e.g., ["4h", "1d"])
    """
    normalizer = Normalizer(symbols=symbols, intervals=intervals)
    
    try:
        await normalizer.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        await normalizer.stop()
    except Exception as e:
        logger.error(f"Fatal error in normalizer: {e}")
        await normalizer.stop()
        raise
