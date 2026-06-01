"""Unit tests for VWAP Mean Reversion Strategy."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from research.strategies.types import MarketDataEvent
from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import backtest as bt  # noqa: E402


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


def _synthetic_htf_bars(n: int, start: datetime) -> list[MarketDataEvent]:
    """Rising closes → elevated RSI on HTF series."""
    out: list[MarketDataEvent] = []
    for i in range(n):
        c = 50.0 + float(i) * 0.3
        out.append(
            MarketDataEvent(
                symbol="BTC/USD",
                interval="4h",
                open=c - 0.05,
                high=c + 0.1,
                low=c - 0.1,
                close=c,
                volume=1000.0,
                timestamp=(start + timedelta(hours=4 * i)).isoformat().replace("+00:00", "Z"),
            )
        )
    return out


class TestTaskLGates:
    """Task L — long_min_volume_ratio and htf_rsi_long_max."""

    def test_vwap_htf_rsi_at_returns_none_when_short(self):
        t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
        bars = [
            bt.Bar(
                timestamp=t0 + timedelta(hours=4 * i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            )
            for i in range(10)
        ]
        assert bt._vwap_htf_rsi_at(bars, 14, bars[-1].timestamp) is None

    def test_vwap_htf_rsi_at_computes_when_warm(self):
        t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
        bars = [
            bt.Bar(
                timestamp=t0 + timedelta(hours=4 * i),
                open=100.0 + i * 0.2,
                high=102.0 + i * 0.2,
                low=99.0 + i * 0.2,
                close=100.5 + i * 0.2,
                volume=1000.0,
            )
            for i in range(40)
        ]
        rsi = bt._vwap_htf_rsi_at(bars, 14, bars[-1].timestamp)
        assert rsi is not None
        assert rsi > 60.0

    def test_latest_htf_rsi_uses_fetch(self):
        cfg = VWAPMeanReversionConfig(
            symbol="BTC/USD",
            interval="1h",
            htf_interval="4h",
            htf_rsi_long_max=40.0,
        )
        strat = VWAPMeanReversionStrategy(cfg)
        fake = _synthetic_htf_bars(80, datetime(2024, 1, 1, tzinfo=timezone.utc))
        with patch.object(strat, "fetch_htf_bars", return_value=fake):
            rsi = strat._latest_htf_rsi("BTC/USD")
        assert rsi is not None
        assert rsi > 50.0
