"""Unit tests for BaseStrategy abstract class."""

import json
from unittest.mock import MagicMock, patch

import pytest

from research.strategies.base import BaseStrategy, TRADE_INTENT_STREAM
from research.strategies.types import MarketDataEvent, TradeIntent


class ConcreteStrategy(BaseStrategy):
    """Concrete implementation for testing."""
    
    def __init__(self, strategy_id: str, should_signal: bool = False):
        super().__init__(strategy_id)
        self.should_signal = should_signal
    
    def generate_signals(self, bar: MarketDataEvent) -> TradeIntent | None:
        """Generate a signal if should_signal is True."""
        if self.should_signal:
            return TradeIntent(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side="buy",
                intent_type="enter",
                notional_risk_pct=5.0,
                metadata={"test": True},
            )
        return None


class TestBaseStrategy:
    """Test suite for BaseStrategy."""
    
    def test_base_strategy_is_abstract(self):
        """Test that BaseStrategy cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseStrategy("test_strategy")
    
    def test_concrete_strategy_initialization(self):
        """Test that a concrete strategy can be initialized."""
        strategy = ConcreteStrategy("test_strategy")
        assert strategy.strategy_id == "test_strategy"
    
    def test_generate_signals_abstract(self):
        """Test that generate_signals must be implemented."""
        strategy = ConcreteStrategy("test_strategy", should_signal=True)
        bar = MarketDataEvent(
            symbol="BTC/USD",
            interval="4h",
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50500.0,
            volume=100.0,
            timestamp="2024-01-01T00:00:00Z",
        )
        
        intent = strategy.generate_signals(bar)
        assert intent is not None
        assert intent.strategy_id == "test_strategy"
        assert intent.symbol == "BTC/USD"
        assert intent.side == "buy"
        assert intent.intent_type == "enter"
        assert intent.notional_risk_pct == 5.0
    
    def test_generate_signals_returns_none(self):
        """Test that generate_signals can return None."""
        strategy = ConcreteStrategy("test_strategy", should_signal=False)
        bar = MarketDataEvent(
            symbol="BTC/USD",
            interval="4h",
            open=50000.0,
            high=51000.0,
            low=49000.0,
            close=50500.0,
            volume=100.0,
            timestamp="2024-01-01T00:00:00Z",
        )
        
        intent = strategy.generate_signals(bar)
        assert intent is None
    
    @patch("research.strategies.base.consume_stream")
    def test_consume_market_data_success(self, mock_consume):
        """Test successful consumption of market data."""
        strategy = ConcreteStrategy("test_strategy")
        
        # Mock Redis stream response
        mock_consume.return_value = [
            {
                "id": "12345-0",
                "data": {
                    "symbol": "BTC/USD",
                    "interval": "4h",
                    "open": "50000.0",
                    "high": "51000.0",
                    "low": "49000.0",
                    "close": "50500.0",
                    "volume": "100.0",
                    "timestamp": "2024-01-01T00:00:00Z",
                },
            }
        ]
        
        events = strategy.consume_market_data(
            symbol="BTC/USD",
            interval="4h",
            consumer_group="test_group",
        )
        
        assert len(events) == 1
        assert events[0].symbol == "BTC/USD"
        assert events[0].interval == "4h"
        assert events[0].open == 50000.0
        assert events[0].high == 51000.0
        assert events[0].low == 49000.0
        assert events[0].close == 50500.0
        assert events[0].volume == 100.0
        assert events[0].timestamp == "2024-01-01T00:00:00Z"
        
        # Verify consume_stream was called with correct parameters
        mock_consume.assert_called_once()
        call_kwargs = mock_consume.call_args[1]
        assert call_kwargs["stream_key"] == "market:ohlcv:BTC/USD:4h"
        assert call_kwargs["consumer_group"] == "test_group"
        assert call_kwargs["consumer_name"] == "test_strategy"
        assert call_kwargs["count"] == 1
    
    @patch("research.strategies.base.consume_stream")
    def test_consume_market_data_custom_consumer_name(self, mock_consume):
        """Test consumption with custom consumer name."""
        strategy = ConcreteStrategy("test_strategy")
        mock_consume.return_value = []
        
        strategy.consume_market_data(
            symbol="BTC/USD",
            interval="4h",
            consumer_group="test_group",
            consumer_name="custom_consumer",
        )
        
        call_kwargs = mock_consume.call_args[1]
        assert call_kwargs["consumer_name"] == "custom_consumer"
    
    @patch("research.strategies.base.consume_stream")
    def test_consume_market_data_invalid_message(self, mock_consume):
        """Test that invalid messages are skipped."""
        strategy = ConcreteStrategy("test_strategy")
        
        # Mock response with invalid message
        mock_consume.return_value = [
            {
                "id": "12345-0",
                "data": {
                    "symbol": "BTC/USD",
                    # Missing required fields
                },
            }
        ]
        
        events = strategy.consume_market_data(
            symbol="BTC/USD",
            interval="4h",
            consumer_group="test_group",
        )
        
        # Invalid message should be skipped
        assert len(events) == 0
    
    @patch("research.strategies.base.consume_stream")
    def test_consume_market_data_multiple_messages(self, mock_consume):
        """Test consumption of multiple messages."""
        strategy = ConcreteStrategy("test_strategy")
        
        mock_consume.return_value = [
            {
                "id": "12345-0",
                "data": {
                    "symbol": "BTC/USD",
                    "interval": "4h",
                    "open": "50000.0",
                    "high": "51000.0",
                    "low": "49000.0",
                    "close": "50500.0",
                    "volume": "100.0",
                    "timestamp": "2024-01-01T00:00:00Z",
                },
            },
            {
                "id": "12346-0",
                "data": {
                    "symbol": "ETH/USD",
                    "interval": "4h",
                    "open": "3000.0",
                    "high": "3100.0",
                    "low": "2900.0",
                    "close": "3050.0",
                    "volume": "500.0",
                    "timestamp": "2024-01-01T04:00:00Z",
                },
            },
        ]
        
        events = strategy.consume_market_data(
            symbol="BTC/USD",
            interval="4h",
            consumer_group="test_group",
            count=2,
        )
        
        assert len(events) == 2
        assert events[0].symbol == "BTC/USD"
        assert events[1].symbol == "ETH/USD"
    
    @patch("research.strategies.base.publish_to_stream")
    def test_emit_trade_intent_success(self, mock_publish):
        """Test successful emission of trade intent."""
        strategy = ConcreteStrategy("test_strategy")
        mock_publish.return_value = "12345-0"
        
        intent = TradeIntent(
            strategy_id="test_strategy",
            symbol="BTC/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=5.0,
            metadata={"signal_strength": 0.8},
        )
        
        message_id = strategy.emit_trade_intent(intent)
        
        assert message_id == "12345-0"
        mock_publish.assert_called_once()
        
        # Verify the event data structure
        call_args = mock_publish.call_args
        assert call_args[0][0] == TRADE_INTENT_STREAM
        event_data = call_args[0][1]
        
        assert "event_id" in event_data
        assert "intent" in event_data
        assert "timestamp" in event_data
        
        # Verify intent is serialized correctly
        intent_dict = json.loads(event_data["intent"])
        assert intent_dict["strategy_id"] == "test_strategy"
        assert intent_dict["symbol"] == "BTC/USD"
        assert intent_dict["side"] == "buy"
        assert intent_dict["intent_type"] == "enter"
        assert float(intent_dict["notional_risk_pct"]) == 5.0
    
    def test_emit_trade_intent_strategy_id_mismatch(self):
        """Test that emit_trade_intent validates strategy_id."""
        strategy = ConcreteStrategy("test_strategy")
        
        intent = TradeIntent(
            strategy_id="different_strategy",  # Mismatch!
            symbol="BTC/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=5.0,
            metadata={},
        )
        
        with pytest.raises(ValueError, match="strategy_id.*does not match"):
            strategy.emit_trade_intent(intent)
    
    @patch("research.strategies.base.publish_to_stream")
    def test_emit_trade_intent_redis_error(self, mock_publish):
        """Test handling of Redis errors during emission."""
        strategy = ConcreteStrategy("test_strategy")
        
        import redis
        mock_publish.side_effect = redis.RedisError("Connection failed")
        
        intent = TradeIntent(
            strategy_id="test_strategy",
            symbol="BTC/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=5.0,
            metadata={},
        )
        
        with pytest.raises(redis.RedisError):
            strategy.emit_trade_intent(intent)
