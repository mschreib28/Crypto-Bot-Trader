"""BaseStrategy abstract class for implementing trading strategies.

This module provides the BaseStrategy abstraction that all strategies must inherit from.
Strategies must follow the constraints defined in docs/MSSD.md § 4.2.2:
- Must NOT track positions or account balances
- Must NOT submit or cancel orders
- Must NOT persist state across restarts
- Must NOT bypass the Risk Manager
"""

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.redis.keys import MARKET_OHLCV_STREAM, STRATEGY_PHASE_STATE_KEY, STRATEGY_PHASE_STATE_TTL
from backend.redis.streams import consume_stream, publish_to_stream

from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent

logger = logging.getLogger(__name__)

# Stream key for TradeIntentEvent (from contracts/events.md)
TRADE_INTENT_STREAM = "strategy:trade_intent"


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    
    Strategies consume market data from Redis streams and emit TradeIntent objects.
    All strategies must implement generate_signals() to define their trading logic.
    
    Constraints (from MSSD § 4.2.2):
    - Strategies must NOT track positions or account balances
    - Strategies must NOT submit or cancel orders
    - Strategies must NOT persist state across restarts
    - Strategies must NOT bypass the Risk Manager
    """
    
    def __init__(self, strategy_id: str):
        """
        Initialize the strategy.
        
        Args:
            strategy_id: Unique identifier for this strategy instance.
                        Must match a registered strategy in the strategies table.
        """
        self.strategy_id = strategy_id
        logger.info(f"Initialized strategy: {strategy_id}")
    
    @abstractmethod
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.
        
        This is the core method that strategies must implement. It receives
        a MarketDataEvent (OHLCV bar) and returns a TradeIntent if a signal
        is generated, or None if no signal is present.
        
        Args:
            bar: MarketDataEvent containing OHLCV data for the current bar
            
        Returns:
            TradeIntent if a signal is generated, None otherwise
            
        Note:
            Strategies should maintain only in-memory indicator state (e.g., rolling windows).
            State is lost on restart - strategies must not rely on persistence.
        """
        pass
    
    @abstractmethod
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate this strategy against the given symbol's bars.
        
        This method is symbol-agnostic - it can evaluate ANY symbol.
        
        Args:
            symbol: The trading pair symbol (e.g., "SOL/USD")
            bars: List of OHLCV bars for that symbol
            
        Returns:
            SignalResult with signal_type, confidence (0-100), and indicators
        """
        pass
    
    def consume_market_data(
        self,
        symbol: str,
        interval: str,
        consumer_group: str,
        consumer_name: Optional[str] = None,
        count: int = 1,
        block: Optional[int] = None,
    ) -> List[MarketDataEvent]:
        """
        Consume market data from Redis stream.
        
        Reads OHLCV bars from the stream `market:ohlcv:{symbol}:{interval}`
        and converts them to MarketDataEvent objects.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTC/USD")
            interval: Time interval (e.g., "4h", "1d")
            consumer_group: Name of the consumer group for this strategy
            consumer_name: Name of this consumer instance (defaults to strategy_id)
            count: Maximum number of messages to return (default: 1)
            block: Block for up to this many milliseconds if no messages available
                  (None = no blocking)
            
        Returns:
            List of MarketDataEvent objects parsed from the stream
            
        Raises:
            ValueError: If the stream data is invalid
            redis.RedisError: If consumption fails
        """
        if consumer_name is None:
            consumer_name = self.strategy_id
        
        # Format stream key: market:ohlcv:{symbol}:{interval}
        stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
        
        try:
            messages = consume_stream(
                stream_key=stream_key,
                consumer_group=consumer_group,
                consumer_name=consumer_name,
                count=count,
                block=block,
            )
            
            events = []
            for msg in messages:
                try:
                    # Parse message data (Redis returns dict with string keys/values)
                    data = msg["data"]
                    
                    # Convert string values to appropriate types
                    event = MarketDataEvent(
                        symbol=data["symbol"],
                        interval=data["interval"],
                        open=float(data["open"]),
                        high=float(data["high"]),
                        low=float(data["low"]),
                        close=float(data["close"]),
                        volume=float(data["volume"]),
                        timestamp=data["timestamp"],
                    )
                    events.append(event)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(
                        f"Failed to parse market data message {msg.get('id', 'unknown')}: {e}"
                    )
                    continue
            
            return events
            
        except Exception as e:
            logger.error(
                f"Failed to consume market data from {stream_key}: {e}"
            )
            raise
    
    def fetch_htf_bars(
        self,
        symbol: str,
        htf_interval: str,
        count: int = 200,
        consumer_group: str = "strategy_htf",
        consumer_name: Optional[str] = None
    ) -> List[MarketDataEvent]:
        """
        Fetch higher timeframe bars for regime filtering.
        
        This method fetches historical bars from a higher timeframe (e.g., 1h, 4h)
        to use for trend/regime analysis. Bars are returned in chronological order
        (oldest first).
        
        Args:
            symbol: Trading pair symbol (e.g., "BTC/USD")
            htf_interval: Higher timeframe interval (e.g., "1h", "4h")
            count: Maximum number of bars to fetch (default: 200)
            consumer_group: Redis consumer group name for HTF data
            consumer_name: Redis consumer name (defaults to strategy_id)
            
        Returns:
            List of MarketDataEvent objects, oldest first. Returns empty list
            if HTF data is not available (allows strategies to work without HTF filters).
            
        Note:
            This method uses a simple approach: consume from stream without blocking.
            For production, consider caching recent HTF bars to avoid repeated Redis calls.
        """
        if consumer_name is None:
            consumer_name = f"{self.strategy_id}_htf"
        
        try:
            # Use existing consume_market_data infrastructure
            # Note: consume_market_data reads from stream, but we need historical data
            # For now, we'll use the same approach but fetch multiple messages
            stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=htf_interval)
            
            # Try to fetch recent bars (up to count)
            # Note: Redis streams are append-only, so we read from oldest to newest
            # This is a simplified implementation - production should use XREVRANGE
            from backend.redis import get_redis_client
            redis_client = get_redis_client()
            
            # Use XRANGE to get historical bars (oldest first)
            # Format: XRANGE stream_key - + COUNT count
            messages = redis_client.xrange(stream_key, count=count)
            
            events = []
            for msg_id, data in messages:
                try:
                    event = MarketDataEvent(
                        symbol=data.get("symbol", symbol),
                        interval=data.get("interval", htf_interval),
                        open=float(data.get("open", 0)),
                        high=float(data.get("high", 0)),
                        low=float(data.get("low", 0)),
                        close=float(data.get("close", 0)),
                        volume=float(data.get("volume", 0)),
                        timestamp=data.get("timestamp", ""),
                    )
                    events.append(event)
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"Failed to parse HTF bar {msg_id}: {e}")
                    continue
            
            logger.debug(
                f"Fetched {len(events)} HTF bars for {symbol}/{htf_interval}"
            )
            return events
            
        except Exception as e:
            logger.warning(
                f"Failed to fetch HTF bars for {symbol}/{htf_interval}: {e}. "
                f"Strategy will continue without HTF filter."
            )
            return []
    
    def emit_trade_intent(self, intent: TradeIntent) -> str:
        """
        Emit a TradeIntentEvent to the strategy:trade_intent stream.
        
        Wraps the TradeIntent in a TradeIntentEvent and publishes it to Redis.
        The Risk Manager will consume this event and evaluate the intent.
        
        Args:
            intent: TradeIntent object to emit
            
        Returns:
            Message ID of the published event
            
        Raises:
            ValueError: If the intent is invalid
            redis.RedisError: If publishing fails
        """
        # Ensure intent has the correct strategy_id
        if intent.strategy_id != self.strategy_id:
            raise ValueError(
                f"Intent strategy_id ({intent.strategy_id}) does not match "
                f"strategy instance ({self.strategy_id})"
            )
        
        # Create TradeIntentEvent (from contracts/events.md)
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Serialize TradeIntent to dict (matching contracts/types.md)
        intent_dict = {
            "strategy_id": intent.strategy_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "intent_type": intent.intent_type,
            "notional_risk_pct": str(intent.notional_risk_pct),  # Redis stores as string
            "metadata": json.dumps(intent.metadata) if intent.metadata else "{}",
        }
        
        # Create event payload (matching contracts/events.md)
        # Redis streams store field-value pairs as strings, so serialize nested objects
        event_data = {
            "event_id": event_id,
            "intent": json.dumps(intent_dict),
            "timestamp": timestamp,
        }
        
        try:
            message_id = publish_to_stream(TRADE_INTENT_STREAM, event_data)
            logger.info(
                f"Emitted TradeIntent from {self.strategy_id}: "
                f"symbol={intent.symbol}, side={intent.side}, "
                f"intent_type={intent.intent_type}, risk_pct={intent.notional_risk_pct}"
            )
            return message_id
        except Exception as e:
            logger.error(f"Failed to emit TradeIntent: {e}")
            raise
    
    def get_phase_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get strategy phase state from Redis (for multi-phase strategies).
        
        This allows strategies to persist phase state (e.g., compression → breakout → retest)
        across restarts. State is stored with TTL for automatic cleanup.
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Phase state dictionary or None if not found
        """
        try:
            from backend.redis import get_redis_client
            redis_client = get_redis_client()
            key = STRATEGY_PHASE_STATE_KEY.format(strategy_id=self.strategy_id, symbol=symbol)
            data = redis_client.get(key)
            if data:
                import json
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning(f"Failed to get phase state for {symbol}: {e}")
            return None
    
    def set_phase_state(self, symbol: str, state: Dict[str, Any]) -> None:
        """
        Store strategy phase state in Redis (for multi-phase strategies).
        
        State persists across restarts with TTL for automatic cleanup.
        This makes strategies restart-safe and auditable.
        
        Args:
            symbol: Trading pair symbol
            state: Phase state dictionary (must be JSON-serializable)
        """
        try:
            from backend.redis import get_redis_client
            import json
            redis_client = get_redis_client()
            key = STRATEGY_PHASE_STATE_KEY.format(strategy_id=self.strategy_id, symbol=symbol)
            redis_client.setex(key, STRATEGY_PHASE_STATE_TTL, json.dumps(state))
            logger.debug(f"Stored phase state for {symbol}: {state}")
        except Exception as e:
            logger.warning(f"Failed to store phase state for {symbol}: {e}")
    
    def clear_phase_state(self, symbol: str) -> None:
        """
        Clear strategy phase state from Redis.
        
        Args:
            symbol: Trading pair symbol
        """
        try:
            from backend.redis import get_redis_client
            redis_client = get_redis_client()
            key = STRATEGY_PHASE_STATE_KEY.format(strategy_id=self.strategy_id, symbol=symbol)
            redis_client.delete(key)
            logger.debug(f"Cleared phase state for {symbol}")
        except Exception as e:
            logger.warning(f"Failed to clear phase state for {symbol}: {e}")