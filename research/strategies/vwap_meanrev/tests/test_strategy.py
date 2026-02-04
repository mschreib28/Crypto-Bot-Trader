"""Unit tests for VWAP Mean Reversion Strategy."""

import pytest
from datetime import datetime, timezone

from research.strategies.types import MarketDataEvent
from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy


def create_bar(symbol: str, open: float, high: float, low: float, close: float, volume: float = 1000.0) -> MarketDataEvent:
    """Helper to create MarketDataEvent."""
    return MarketDataEvent(
        symbol=symbol,
        interval="15m",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


class TestVWAPMeanReversionStrategy:
    """Test suite for VWAPMeanReversionStrategy."""
    
    def test_strategy_initialization(self):
        """Test strategy initializes correctly."""
        config = VWAPMeanReversionConfig(symbol="BTC/USD")
        strategy = VWAPMeanReversionStrategy(config)
        
        assert strategy.strategy_id == config.strategy_id
        assert strategy.config.symbol == "BTC/USD"
        assert len(strategy._bars) == 0
    
    def test_insufficient_data_returns_none(self):
        """Test that insufficient data returns None."""
        config = VWAPMeanReversionConfig(symbol="BTC/USD")
        strategy = VWAPMeanReversionStrategy(config)
        
        # Not enough bars
        bar = create_bar("BTC/USD", 100.0, 101.0, 99.0, 100.5)
        result = strategy.generate_signals(bar)
        
        assert result is None
    
    def test_symbol_mismatch_returns_none(self):
        """Test that symbol mismatch returns None."""
        config = VWAPMeanReversionConfig(symbol="BTC/USD")
        strategy = VWAPMeanReversionStrategy(config)
        
        # Add enough bars for calculation
        for i in range(60):
            price = 100.0 + (i * 0.1)
            bar = create_bar("BTC/USD", price, price + 0.5, price - 0.5, price)
            strategy._bars.append(bar)
        
        # Wrong symbol
        bar = create_bar("ETH/USD", 100.0, 101.0, 99.0, 100.5)
        result = strategy.generate_signals(bar)
        
        assert result is None
    
    def test_evaluate_insufficient_data(self):
        """Test evaluate with insufficient data."""
        config = VWAPMeanReversionConfig(symbol="BTC/USD")
        strategy = VWAPMeanReversionStrategy(config)
        
        bars = [create_bar("BTC/USD", 100.0, 101.0, 99.0, 100.5) for _ in range(10)]
        result = strategy.evaluate("BTC/USD", bars)
        
        assert result.signal_type == "NONE"
        assert result.confidence == 0.0
        assert "insufficient_data" in result.indicators.get("error", "")
