"""Tests for research.strategies.btc_daily_regime (BTC 200d EMA bull gate)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.strategies.btc_daily_regime import (
    btc_daily_bars_pass_bull_filter_at_ts,
    btc_daily_close_above_ema_at_ts,
    slice_btc_daily_bars_up_to_ts,
)
from research.strategies.types import MarketDataEvent


def _day(i: int) -> datetime:
    return datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)


def test_close_above_ema_insufficient_history() -> None:
    ts = [_day(i) for i in range(50)]
    closes = [100.0 + i * 0.01 for i in range(50)]
    assert not btc_daily_close_above_ema_at_ts(ts, closes, _day(49), 200)


def test_close_above_ema_bull_pass() -> None:
    n = 220
    ts = [_day(i) for i in range(n)]
    closes = [100.0] * (n - 5) + [101.0, 102.0, 103.0, 104.0, 105.0]
    entry = ts[-1]
    assert btc_daily_close_above_ema_at_ts(ts, closes, entry, 200)


def test_close_above_ema_bear_fail() -> None:
    n = 220
    ts = [_day(i) for i in range(n)]
    closes = [200.0] * (n - 5) + [90.0, 88.0, 85.0, 82.0, 80.0]
    entry = ts[-1]
    assert not btc_daily_close_above_ema_at_ts(ts, closes, entry, 200)


def test_bar_objects_pass_and_slice() -> None:
    n = 210
    bars = [
        MarketDataEvent(
            symbol="BTC/USD",
            interval="1d",
            open=100.0 + i * 0.05,
            high=102.0 + i * 0.05,
            low=99.0 + i * 0.05,
            close=100.0 + i * 0.05,
            volume=1.0,
            timestamp=_day(i).isoformat().replace("+00:00", "Z"),
        )
        for i in range(n)
    ]
    entry_ts = bars[-1].timestamp
    assert btc_daily_bars_pass_bull_filter_at_ts(bars, entry_ts, 200)

    cut = _day(100)
    sliced = slice_btc_daily_bars_up_to_ts(bars, cut)
    assert len(sliced) == 101
    assert all(
        datetime.fromisoformat(b.timestamp.replace("Z", "+00:00")) <= cut
        for b in sliced
    )


def test_pass_on_simple_namespace_bars() -> None:
    n = 205
    bars = [SimpleNamespace(timestamp=_day(i), close=50.0 + float(i) * 0.5) for i in range(n)]
    assert btc_daily_bars_pass_bull_filter_at_ts(bars, _day(n - 1), 200)


def test_empty_bars_fail() -> None:
    assert not btc_daily_bars_pass_bull_filter_at_ts([], _day(0), 200)
