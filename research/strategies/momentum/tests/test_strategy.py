"""Unit tests for MomentumStrategy."""

import pytest
from datetime import datetime, timezone

from research.strategies.momentum.config import MomentumConfig
from research.strategies.momentum.strategy import MomentumStrategy
from research.strategies.types import MarketDataEvent, TradeIntent


def create_bar(
    symbol: str = "BTC/USD",
    interval: str = "4h",
    open: float = 50000.0,
    high: float = None,
    low: float = None,
    close: float = 50500.0,
    volume: float = 100.0,
    timestamp: str = None,
) -> MarketDataEvent:
    """Helper to create MarketDataEvent for testing.
    
    Automatically calculates valid high/low values based on open/close
    if not explicitly provided.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Calculate valid high/low based on open/close if not provided
    if high is None:
        high = max(open, close) * 1.01  # 1% above max
    if low is None:
        low = min(open, close) * 0.99  # 1% below min
    
    return MarketDataEvent(
        symbol=symbol,
        interval=interval,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        timestamp=timestamp,
    )


class TestMomentumStrategyInitialization:
    """Test strategy initialization."""
    
    def test_init_with_default_config(self):
        """Test initialization with default config."""
        strategy = MomentumStrategy()
        
        assert strategy.strategy_id == "momentum_btc"
        assert strategy.config.symbol == "BTC/USD"
        assert strategy.config.lookback_period == 20
        assert strategy.config.roc_threshold == 2.0
        assert strategy.config.notional_risk_pct == 2.0
    
    def test_init_with_custom_config(self):
        """Test initialization with custom config."""
        config = MomentumConfig(
            lookback_period=10,
            roc_threshold=3.0,
            notional_risk_pct=1.5,
        )
        strategy = MomentumStrategy(config=config)
        
        assert strategy.strategy_id == "momentum_btc"
        assert strategy.config.lookback_period == 10
        assert strategy.config.roc_threshold == 3.0
        assert strategy.config.notional_risk_pct == 1.5


class TestMomentumStrategySignalGeneration:
    """Test signal generation logic."""
    
    def test_no_signal_with_insufficient_data(self):
        """Test that no signal is generated when there's insufficient data."""
        strategy = MomentumStrategy()
        bar = create_bar(close=50000.0)
        
        # First few bars should not generate signals
        for i in range(strategy.config.lookback_period):
            result = strategy.generate_signals(bar)
            assert result is None, f"Should not generate signal with only {i+1} bars"
    
    def test_buy_signal_on_bullish_momentum(self):
        """Test buy signal generation on bullish momentum."""
        config = MomentumConfig(lookback_period=5, roc_threshold=2.0)
        strategy = MomentumStrategy(config=config)
        
        # Build up price window: start at 50000, end at 51020 (2.04% increase)
        # ROC = (51020 - 50000) / 50000 * 100 = 2.04%
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price + (i * 204.0)  # 204 per step = 1020 total = 2.04%
            bars.append(create_bar(close=price))
        
        # Process bars to build window
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        # Last bar should trigger buy signal (ROC = 2.04% >= 2.0%)
        result = strategy.generate_signals(bars[-1])
        
        assert result is not None
        assert isinstance(result, TradeIntent)
        assert result.strategy_id == "momentum_btc"
        assert result.symbol == "BTC/USD"
        assert result.side == "buy"
        assert result.intent_type == "enter"
        assert result.notional_risk_pct == 2.0
        assert "roc" in result.metadata
        assert result.metadata["roc"] >= 2.0
    
    def test_sell_signal_on_bearish_momentum(self):
        """Test sell signal generation on bearish momentum."""
        config = MomentumConfig(lookback_period=5, roc_threshold=2.0)
        strategy = MomentumStrategy(config=config)
        
        # Build up price window: start at 50000, end at 48980 (2.04% decrease)
        # ROC = (48980 - 50000) / 50000 * 100 = -2.04%
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price - (i * 204.0)  # 204 per step = 1020 total = -2.04%
            bars.append(create_bar(close=price))
        
        # Process bars to build window
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        # Last bar should trigger sell signal (ROC = -2.0% <= -2.0%)
        result = strategy.generate_signals(bars[-1])
        
        assert result is not None
        assert isinstance(result, TradeIntent)
        assert result.strategy_id == "momentum_btc"
        assert result.symbol == "BTC/USD"
        assert result.side == "sell"
        assert result.intent_type == "enter"
        assert result.notional_risk_pct == 2.0
        assert "roc" in result.metadata
        assert result.metadata["roc"] <= -2.0
    
    def test_no_signal_when_momentum_below_threshold(self):
        """Test that no signal is generated when momentum is below threshold."""
        config = MomentumConfig(lookback_period=5, roc_threshold=5.0)
        strategy = MomentumStrategy(config=config)
        
        # Build up price window with small change (1% increase, below 5% threshold)
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price + (i * 10.0)  # Small increase
            bars.append(create_bar(close=price))
        
        # Process bars to build window
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        # Last bar should NOT trigger signal (ROC < 5.0%)
        result = strategy.generate_signals(bars[-1])
        
        assert result is None
    
    def test_metadata_includes_indicator_values(self):
        """Test that TradeIntent metadata includes indicator values."""
        config = MomentumConfig(lookback_period=5, roc_threshold=1.0)
        strategy = MomentumStrategy(config=config)
        
        # Build up price window with 1.5% increase
        # ROC = (50750 - 50000) / 50000 * 100 = 1.5%
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price + (i * 150.0)  # 150 per step = 750 total = 1.5%
            bars.append(create_bar(close=price))
        
        # Process bars
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        result = strategy.generate_signals(bars[-1])
        
        assert result is not None
        assert "roc" in result.metadata
        assert "lookback_period" in result.metadata
        assert "roc_threshold" in result.metadata
        assert "current_price" in result.metadata
        assert "bar_timestamp" in result.metadata
        assert "interval" in result.metadata
        
        assert result.metadata["lookback_period"] == 5
        assert result.metadata["roc_threshold"] == 1.0
        assert result.metadata["current_price"] == bars[-1].close
        assert result.metadata["interval"] == bars[-1].interval
    
    def test_symbol_filtering(self):
        """Test that bars for wrong symbol are ignored."""
        strategy = MomentumStrategy()
        
        # Build up window with BTC/USD
        for i in range(21):
            bar = create_bar(symbol="BTC/USD", close=50000.0 + i)
            strategy.generate_signals(bar)
        
        # Bar with wrong symbol should be ignored
        eth_bar = create_bar(symbol="ETH/USD", close=3000.0)
        result = strategy.generate_signals(eth_bar)
        
        assert result is None
    
    def test_rolling_window_behavior(self):
        """Test that the price window maintains correct size."""
        config = MomentumConfig(lookback_period=5)
        strategy = MomentumStrategy(config=config)
        
        # Add more bars than lookback_period
        for i in range(10):
            bar = create_bar(close=50000.0 + i)
            strategy.generate_signals(bar)
        
        # Window should only contain last 6 values (lookback_period + 1)
        assert len(strategy._price_window) == 6
    
    def test_trade_intent_fields(self):
        """Test that TradeIntent has all required fields with correct values."""
        config = MomentumConfig(lookback_period=5, roc_threshold=1.0, notional_risk_pct=1.5)
        strategy = MomentumStrategy(config=config)
        
        # Build up price window with 1.5% increase
        # ROC = (50750 - 50000) / 50000 * 100 = 1.5%
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price + (i * 150.0)  # 150 per step = 750 total = 1.5%
            bars.append(create_bar(close=price))
        
        # Process bars
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        result = strategy.generate_signals(bars[-1])
        
        assert result is not None
        assert result.strategy_id == "momentum_btc"
        assert result.symbol == "BTC/USD"
        assert result.side in ("buy", "sell")
        assert result.intent_type == "enter"
        assert result.notional_risk_pct == 1.5
        assert isinstance(result.metadata, dict)


class TestMomentumStrategyEdgeCases:
    """Test edge cases and error handling."""
    
    def test_zero_price_handling(self):
        """Test handling of zero price (should not crash)."""
        config = MomentumConfig(lookback_period=5)
        strategy = MomentumStrategy(config=config)
        
        # Build window with normal prices
        for i in range(5):
            bar = create_bar(close=50000.0 + i)
            strategy.generate_signals(bar)
        
        # Add bar with zero price (should not crash, but may not calculate ROC)
        zero_bar = create_bar(close=0.0)
        result = strategy.generate_signals(zero_bar)
        
        # Should handle gracefully (either None or valid signal)
        assert result is None or isinstance(result, TradeIntent)
    
    def test_very_large_price_change(self):
        """Test handling of very large price changes."""
        config = MomentumConfig(lookback_period=5, roc_threshold=10.0)
        strategy = MomentumStrategy(config=config)
        
        # Build window with large price increase (50%)
        base_price = 50000.0
        bars = []
        for i in range(6):
            price = base_price * (1.0 + i * 0.1)  # 10% per step
            bars.append(create_bar(close=price))
        
        # Process bars
        for bar in bars[:-1]:
            strategy.generate_signals(bar)
        
        result = strategy.generate_signals(bars[-1])
        
        # Should generate buy signal (ROC > 10%)
        assert result is not None
        assert result.side == "buy"
        assert result.metadata["roc"] > 10.0
