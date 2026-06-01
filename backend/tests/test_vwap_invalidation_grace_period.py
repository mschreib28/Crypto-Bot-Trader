"""Regression test: VWAP invalidation must NOT fire within 1 candle of entry.

Bug: After a BUY position opens, the screener stops evaluating the symbol
(to prevent duplicate entries). The PositionMonitor reads stale pre-entry
screener indicators that already show price far from VWAP (exactly the entry
signal for mean-reversion). Without a grace period, the VWAP invalidation
immediately fires, closing the position at a loss.

Fix: Added a 1-candle minimum grace period before VWAP invalidation can trigger.

This test:
1. Creates a fresh position (entry_time = now)
2. Loads screener results where price is already > 2 ATR from VWAP
   (simulating stale pre-entry data)
3. Calls _check_invalidation_exit()
4. Asserts NO forced exit happens during the grace period
5. Simulates time elapsed > 1 candle, asserts exit CAN trigger when warranted
"""

import asyncio
import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    # Wire the submodule as an attribute on the parent so patch() can traverse it.
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)
    return mod


def _setup_stubs():
    redis_client = MagicMock()
    redis_client.get.return_value = None
    redis_client.set.return_value = True
    redis_client.exists.return_value = False
    redis_client.hgetall.return_value = {}

    _stub_module("backend")
    _stub_module("backend.redis", get_redis_client=lambda: redis_client)
    _stub_module("backend.redis.keys",
                 POSITION_KEY="pos:{symbol}",
                 POSITION_STOP_LOSS_KEY="pos:sl:{symbol}",
                 POSITION_TP1_PRICE_KEY="pos:tp1:{symbol}",
                 POSITION_TP1_HIT_KEY="pos:tp1_hit:{symbol}",
                 POSITION_STATUS_KEY="pos:status:{symbol}",
                 POSITION_COOLDOWN_KEY="pos:cooldown:{symbol}",
                 POSITION_EXIT_REASON_KEY="pos:exit_reason:{symbol}",
                 POSITION_PENDING_ORDER_KEY="pos:pending_order:{symbol}",
                 SCREENER_STRATEGY_RESULTS_KEY="screener:results:{strategy_id}",
                 TRADING_ENABLED_KEY="trading:enabled",
                 SHADOW_LIVE_MODE_KEY="shadow:live",
                 HALT_KEY="halt")
    _stub_module("backend.intervals")
    _stub_module("backend.intervals.config",
                 POSITION_MONITOR_INTERVAL_SECONDS=10)
    db_session = MagicMock()
    _stub_module("backend.db", get_session=MagicMock(return_value=db_session))
    _stub_module("backend.db.models", Strategy=MagicMock(), Order=MagicMock())
    _stub_module("backend.execution")
    _stub_module("backend.execution.executor", execute_trade=AsyncMock(return_value=None))
    _stub_module("backend.execution.panic", execute_panic_sequence=lambda: {})
    _stub_module("backend.execution.models",
                 Fill=MagicMock(), TradeIntent=MagicMock())
    _stub_module("backend.screener")
    _stub_module("backend.screener.service")
    _stub_module("backend.risk")
    _stub_module("backend.risk.halt", is_halted=lambda: False)
    _stub_module("backend.positions")
    _stub_module("backend.positions.models",
                 Position=MagicMock())

    return redis_client, db_session


def _make_position(entry_time: datetime, symbol="BABY/USD"):
    """Create a minimal Position-like object."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.side = "long"
    pos.quantity = 100.0
    pos.entry_price = 0.015
    pos.entry_time = entry_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    pos.stop_loss_price = 0.014
    pos.opened_by_strategy_id = "db9e0675-3a78-467e-8227-354b0fac866b"
    pos.unrealized_pnl = -0.004
    return pos


def _make_screener_results(symbol="BABY/USD", vwap=0.016, atr=0.0008):
    """Simulate stale screener cache with price already 2.5 ATR from VWAP."""
    return json.dumps({
        "results": [
            {
                "symbol": symbol,
                "indicators": {
                    "vwap": vwap,
                    "atr": atr,
                    "rsi": 18.0,
                    "current_price": 0.014,  # 2.5 ATR below VWAP
                }
            }
        ]
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _make_monitor(redis_client, db_session, screener_results_json, strategy_mock):
    """Load PositionMonitor from file with stubs in place and return a test instance."""
    import importlib.util, os, sys

    # Configure redis: return screener results for strategy keys
    def redis_get_side_effect(key):
        if isinstance(key, bytes):
            key = key.decode()
        if "screener:results:" in key:
            return screener_results_json.encode()
        return None

    redis_client.get.side_effect = redis_get_side_effect

    # Configure db session: return strategy_mock for any Strategy query
    db_session.query.return_value.filter.return_value.first.return_value = strategy_mock

    # Load the real monitor module (avoid import-path issues with stub "backend" package)
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _monitor_path = os.path.join(_project_root, "backend", "positions", "monitor.py")
    spec = importlib.util.spec_from_file_location("backend.positions.monitor", _monitor_path)
    monitor_mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.positions.monitor"] = monitor_mod
    spec.loader.exec_module(monitor_mod)

    monitor = monitor_mod.PositionMonitor.__new__(monitor_mod.PositionMonitor)
    monitor._force_exit_position = AsyncMock()
    return monitor


class TestVWAPInvalidationGracePeriod:

    def test_vwap_invalidation_skipped_within_1_candle(self):
        """Grace period: no forced exit when position is < 1 candle old."""
        redis_client, db_session = _setup_stubs()
        screener_data = _make_screener_results(vwap=0.016, atr=0.0008)

        strategy_mock = MagicMock()
        strategy_mock.name = "vwap_meanreversion"
        strategy_mock.config = {"interval": "5m", "invalidation_vwap_atr_mult": 2.0}

        monitor = _make_monitor(redis_client, db_session, screener_data, strategy_mock)

        # entry_time = 2 minutes ago (less than 1 candle on 5m bars)
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=2)
        position = _make_position(entry_time)
        current_price = 0.014  # 2.5 ATR below VWAP — stale entry indicators

        asyncio.run(
            monitor._check_invalidation_exit(position, current_price)
        )

        # Must NOT force exit during grace period
        monitor._force_exit_position.assert_not_called()

    def test_vwap_invalidation_fires_after_grace_period(self):
        """After 1+ candle, VWAP invalidation CAN trigger if threshold exceeded."""
        redis_client, db_session = _setup_stubs()
        screener_data = _make_screener_results(vwap=0.016, atr=0.0008)

        strategy_mock = MagicMock()
        strategy_mock.name = "vwap_meanreversion"
        strategy_mock.config = {"interval": "5m", "invalidation_vwap_atr_mult": 2.0}

        monitor = _make_monitor(redis_client, db_session, screener_data, strategy_mock)

        # entry_time = 10 minutes ago (2 candles on 5m bars)
        entry_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        position = _make_position(entry_time)
        current_price = 0.012  # 5.0 ATR below VWAP — well beyond threshold

        asyncio.run(
            monitor._check_invalidation_exit(position, current_price)
        )

        # SHOULD force exit after grace period when threshold exceeded
        monitor._force_exit_position.assert_called_once()
        call_kwargs = monitor._force_exit_position.call_args.kwargs
        assert call_kwargs["reason"] == "invalidation_vwap"
        # candles_held must be the actual value (not hardcoded 0.0)
        assert call_kwargs["candles_held"] >= 1.0, (
            f"Expected candles_held >= 1.0 but got {call_kwargs['candles_held']}"
        )

    def test_vwap_invalidation_no_exit_when_within_threshold_after_grace(self):
        """No exit if price is within ATR threshold after grace period."""
        redis_client, db_session = _setup_stubs()

        # Price only 1.5 ATR from VWAP — within 2.0 threshold
        screener_data = _make_screener_results(vwap=0.016, atr=0.0008)

        strategy_mock = MagicMock()
        strategy_mock.name = "vwap_meanreversion"
        strategy_mock.config = {"interval": "5m", "invalidation_vwap_atr_mult": 2.0}

        monitor = _make_monitor(redis_client, db_session, screener_data, strategy_mock)

        entry_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        position = _make_position(entry_time)
        current_price = 0.0148  # 1.5 ATR below VWAP

        asyncio.run(
            monitor._check_invalidation_exit(position, current_price)
        )

        monitor._force_exit_position.assert_not_called()
