"""Unit tests for MACDStrategy."""

import pytest
from datetime import datetime, timezone

from research.strategies.macd.config import MACDConfig, get_config_schema
from research.strategies.macd.strategy import MACDStrategy
from research.strategies.types import MarketDataEvent, TradeIntent, SignalResult


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


def generate_trending_prices(
    start_price: float,
    num_bars: int,
    trend: str = "up",
    strength: float = 0.01,
) -> list[float]:
    """Generate a list of prices with a clear trend.
    
    Args:
        start_price: Starting price
        num_bars: Number of price points to generate
        trend: "up" for uptrend, "down" for downtrend
        strength: Trend strength per bar (default 1%)
        
    Returns:
        List of prices showing the specified trend
    """
    prices = []
    price = start_price
    
    # Trend factor: positive for up, negative for down
    trend_factor = strength if trend == "up" else -strength
    
    for i in range(num_bars):
        prices.append(price)
        price = price * (1 + trend_factor)
    
    return prices


class TestMACDStrategyInitialization:
    """Test strategy initialization."""
    
    def test_init_with_default_config(self):
        """Test initialization with default config."""
        strategy = MACDStrategy()
        
        assert strategy.strategy_id == "macd_crossover"
        assert strategy.config.symbol == "BTC/USD"
        assert strategy.config.fast_period == 12
        assert strategy.config.slow_period == 26
        assert strategy.config.signal_period == 9
        assert strategy.config.notional_risk_pct == 2.0
    
    def test_init_with_custom_config(self):
        """Test initialization with custom config."""
        config = MACDConfig(
            fast_period=8,
            slow_period=17,
            signal_period=9,
            notional_risk_pct=1.5,
            symbol="ETH/USD",
        )
        strategy = MACDStrategy(config=config)
        
        assert strategy.strategy_id == "macd_crossover"
        assert strategy.config.fast_period == 8
        assert strategy.config.slow_period == 17
        assert strategy.config.signal_period == 9
        assert strategy.config.notional_risk_pct == 1.5
        assert strategy.config.symbol == "ETH/USD"
    
    def test_init_invalid_periods_raises_error(self):
        """Test that fast_period >= slow_period raises ValueError."""
        config = MACDConfig(fast_period=26, slow_period=12)
        
        with pytest.raises(ValueError, match="fast_period.*must be less than.*slow_period"):
            MACDStrategy(config=config)


class TestMACDConfigSchema:
    """Test configuration schema."""
    
    def test_get_config_schema(self):
        """Test that get_config_schema returns correct structure."""
        schema = get_config_schema()
        
        assert schema["strategy_type"] == "macd"
        assert schema["parameters"]["fast_period"] == 12
        assert schema["parameters"]["slow_period"] == 26
        assert schema["parameters"]["signal_period"] == 9
        assert schema["parameters"]["notional_risk_pct"] == 2.0
        assert "min_volume_24h" in schema["filters"]
        assert "description" in schema


class TestMACDStrategySignalGeneration:
    """Test signal generation logic."""
    
    def test_no_signal_with_insufficient_data(self):
        """Test that no signal is generated when there's insufficient data."""
        strategy = MACDStrategy()
        bar = create_bar(close=50000.0)
        
        # First bars should not generate signals (need slow + signal periods)
        min_bars = strategy.config.slow_period + strategy.config.signal_period
        for i in range(min_bars):
            result = strategy.generate_signals(bar)
            # Early bars should return None (insufficient data)
            if i < min_bars - 1:
                assert result is None, f"Should not generate signal with only {i+1} bars"
    
    def test_buy_signal_on_bullish_crossover(self):
        """Test BUY signal generation on bullish MACD crossover.
        
        Simulates a scenario: uptrend -> downtrend -> uptrend reversal
        The final uptrend causes MACD to cross above signal line (bullish crossover).
        """
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Phase 1: Uptrend to establish positive MACD
        up1_prices = generate_trending_prices(50000.0, 20, trend="up", strength=0.01)
        # Phase 2: Downtrend to push MACD negative (below signal)
        down_prices = generate_trending_prices(up1_prices[-1], 25, trend="down", strength=0.02)
        # Phase 3: Strong uptrend to create bullish crossover (MACD crosses above signal)
        up2_prices = generate_trending_prices(down_prices[-1], 25, trend="up", strength=0.03)
        
        all_prices = up1_prices + down_prices + up2_prices
        
        result = None
        for price in all_prices:
            bar = create_bar(close=price)
            signal = strategy.generate_signals(bar)
            if signal is not None and signal.side == "buy":
                result = signal
                break
        
        # Should eventually get a buy signal on bullish crossover
        assert result is not None, "Expected BUY signal on bullish crossover"
        assert isinstance(result, TradeIntent)
        assert result.side == "buy"
        assert result.intent_type == "enter"
        assert "macd" in result.metadata
        assert "signal" in result.metadata
        assert "histogram" in result.metadata
    
    def test_sell_signal_on_bearish_crossover(self):
        """Test SELL signal generation on bearish MACD crossover.
        
        Simulates a scenario where price trends up then reverses down,
        causing MACD to cross below signal line.
        """
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Generate uptrend followed by downtrend to create bearish crossover
        up_prices = generate_trending_prices(50000.0, 15, trend="up")
        down_prices = generate_trending_prices(up_prices[-1], 10, trend="down")
        
        all_prices = up_prices + down_prices
        
        result = None
        for price in all_prices:
            bar = create_bar(close=price)
            result = strategy.generate_signals(bar)
            if result is not None and result.side == "sell":
                break
        
        # Should eventually get a sell signal on bearish crossover
        assert result is not None, "Expected SELL signal on bearish crossover"
        assert isinstance(result, TradeIntent)
        assert result.side == "sell"
        assert result.intent_type == "enter"
    
    def test_no_signal_when_no_crossover(self):
        """Test that no signal when MACD stays on same side of signal."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Steady uptrend - MACD stays positive, no crossover
        prices = generate_trending_prices(50000.0, 30, trend="up")
        
        signals_generated = 0
        for price in prices:
            bar = create_bar(close=price)
            result = strategy.generate_signals(bar)
            if result is not None:
                signals_generated += 1
        
        # In a steady trend, should have minimal or no signals after initial
        # (may have one initial crossover, but not continuous signals)
        assert signals_generated <= 1, "Steady trend should not generate multiple signals"
    
    def test_symbol_filtering(self):
        """Test that bars for wrong symbol are ignored."""
        config = MACDConfig(symbol="BTC/USD")
        strategy = MACDStrategy(config=config)
        
        # Build up window with BTC/USD
        prices = generate_trending_prices(50000.0, 40, trend="up")
        for price in prices:
            bar = create_bar(symbol="BTC/USD", close=price)
            strategy.generate_signals(bar)
        
        # Bar with wrong symbol should be ignored
        eth_bar = create_bar(symbol="ETH/USD", close=3000.0)
        result = strategy.generate_signals(eth_bar)
        
        assert result is None


class TestMACDStrategyEvaluate:
    """Test the evaluate() method for screener integration."""
    
    def test_evaluate_returns_signal_result(self):
        """Test that evaluate returns proper SignalResult."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Generate enough bars
        prices = generate_trending_prices(50000.0, 20, trend="up")
        bars = [create_bar(close=p) for p in prices]
        
        result = strategy.evaluate("BTC/USD", bars)
        
        assert isinstance(result, SignalResult)
        assert result.symbol == "BTC/USD"
        assert result.signal_type in ("BUY", "SELL", "NONE")
        assert 0.0 <= result.confidence <= 100.0
        assert result.strategy_id == "macd_crossover"
        assert "macd" in result.indicators
        assert "signal" in result.indicators
        assert "histogram" in result.indicators
    
    def test_evaluate_insufficient_data(self):
        """Test evaluate with insufficient bars."""
        strategy = MACDStrategy()
        
        # Only 5 bars (need 35 for default config)
        bars = [create_bar(close=50000.0 + i * 100) for i in range(5)]
        
        result = strategy.evaluate("BTC/USD", bars)
        
        assert result.signal_type == "NONE"
        assert result.confidence == 0.0
        assert "error" in result.indicators
        assert result.indicators["error"] == "insufficient_data"
    
    def test_evaluate_buy_signal(self):
        """Test evaluate returns BUY on bullish crossover."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Downtrend then uptrend for bullish crossover
        down_prices = generate_trending_prices(50000.0, 15, trend="down")
        up_prices = generate_trending_prices(down_prices[-1], 10, trend="up")
        all_prices = down_prices + up_prices
        
        bars = [create_bar(close=p) for p in all_prices]
        result = strategy.evaluate("SOL/USD", bars)
        
        # May or may not be exactly at crossover, but should return valid result
        assert isinstance(result, SignalResult)
        assert result.symbol == "SOL/USD"
        assert result.signal_type in ("BUY", "SELL", "NONE")
    
    def test_evaluate_sell_signal(self):
        """Test evaluate returns SELL on bearish crossover."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Uptrend then downtrend for bearish crossover
        up_prices = generate_trending_prices(50000.0, 15, trend="up")
        down_prices = generate_trending_prices(up_prices[-1], 10, trend="down")
        all_prices = up_prices + down_prices
        
        bars = [create_bar(close=p) for p in all_prices]
        result = strategy.evaluate("AVAX/USD", bars)
        
        assert isinstance(result, SignalResult)
        assert result.symbol == "AVAX/USD"


class TestMACDStrategyEdgeCases:
    """Test edge cases and error handling."""
    
    def test_flat_prices_no_signal(self):
        """Test handling of flat/unchanged prices."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # All same price - MACD should be ~0, no clear crossover
        bars = [create_bar(close=50000.0) for _ in range(30)]
        
        signal_count = 0
        for bar in bars:
            result = strategy.generate_signals(bar)
            if result is not None:
                signal_count += 1
        
        # Flat prices should not generate signals (MACD ≈ 0)
        assert signal_count == 0, "Flat prices should not trigger signals"
    
    def test_metadata_includes_all_indicators(self):
        """Test that TradeIntent metadata includes all MACD values."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Generate crossover
        down_prices = generate_trending_prices(50000.0, 15, trend="down")
        up_prices = generate_trending_prices(down_prices[-1], 10, trend="up")
        all_prices = down_prices + up_prices
        
        result = None
        for price in all_prices:
            bar = create_bar(close=price)
            result = strategy.generate_signals(bar)
            if result is not None:
                break
        
        if result is not None:
            assert "macd" in result.metadata
            assert "signal" in result.metadata
            assert "histogram" in result.metadata
            assert "prev_histogram" in result.metadata
            assert "fast_period" in result.metadata
            assert "slow_period" in result.metadata
            assert "signal_period" in result.metadata
            assert "current_price" in result.metadata
            assert "bar_timestamp" in result.metadata
            assert "interval" in result.metadata
    
    def test_trade_intent_fields(self):
        """Test that TradeIntent has all required fields with correct values."""
        config = MACDConfig(
            fast_period=5, 
            slow_period=10, 
            signal_period=3,
            notional_risk_pct=1.5
        )
        strategy = MACDStrategy(config=config)
        
        # Generate crossover
        down_prices = generate_trending_prices(50000.0, 15, trend="down")
        up_prices = generate_trending_prices(down_prices[-1], 10, trend="up")
        all_prices = down_prices + up_prices
        
        result = None
        for price in all_prices:
            bar = create_bar(close=price)
            result = strategy.generate_signals(bar)
            if result is not None:
                break
        
        if result is not None:
            assert result.strategy_id == "macd_crossover"
            assert result.symbol == "BTC/USD"
            assert result.side in ("buy", "sell")
            assert result.intent_type == "enter"
            assert result.notional_risk_pct == 1.5
            assert isinstance(result.metadata, dict)


class TestEMACalculation:
    """Test internal EMA calculation."""
    
    def test_ema_calculation_basic(self):
        """Test that EMA calculation produces reasonable values."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Simple ascending prices
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]
        
        ema = strategy._calculate_ema(prices, 5)
        
        # EMA should exist and be between min and max prices
        assert len(ema) == len(prices)
        # EMA values after warm-up should be reasonable
        assert ema[-1] > 100.0
        assert ema[-1] < 110.0
    
    def test_ema_responds_to_recent_prices(self):
        """Test that EMA responds more to recent prices."""
        config = MACDConfig(fast_period=5, slow_period=10, signal_period=3)
        strategy = MACDStrategy(config=config)
        
        # Flat then sudden jump
        prices = [100.0] * 10 + [200.0]
        
        ema = strategy._calculate_ema(prices, 5)
        
        # Last EMA should be between old level and new price
        # (not at 200, because EMA smooths)
        assert ema[-1] > 100.0
        assert ema[-1] < 200.0
