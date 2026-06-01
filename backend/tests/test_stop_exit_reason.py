"""Tests for stop exit reason classification (breakeven vs initial stop)."""

import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.positions.models import Position


def _make_position(**overrides) -> Position:
    defaults = {
        "symbol": "NEAR/USD",
        "side": "long",
        "quantity": 7.0,
        "entry_price": 2.425,
        "entry_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "opened_by_strategy_id": "vwap_meanrev",
        "stop_loss_price": 2.389,
    }
    defaults.update(overrides)
    return Position(**defaults)


class TestStopExitReason:
    def test_initial_stop_loss_long(self):
        pos = _make_position(stop_loss_price=2.389, breakeven_guard_active=False)
        assert pos.stop_exit_reason() == "stop_loss"

    def test_breakeven_stop_long(self):
        pos = _make_position(
            stop_loss_price=2.431,
            breakeven_guard_active=True,
            breakeven_stop_price=2.431,
        )
        assert pos.stop_exit_reason() == "breakeven_stop"

    def test_breakeven_guard_active_but_stop_below_entry_still_stop_loss(self):
        pos = _make_position(
            stop_loss_price=2.389,
            breakeven_guard_active=True,
        )
        assert pos.stop_exit_reason() == "stop_loss"

    def test_trailing_stop_when_active(self):
        pos = _make_position(
            stop_loss_price=2.45,
            trailing_stop_active=True,
            trailing_stop_price=2.45,
            breakeven_guard_active=False,
        )
        assert pos.stop_exit_reason() == "trailing_stop"

    def test_breakeven_takes_precedence_over_trailing(self):
        pos = _make_position(
            stop_loss_price=2.431,
            breakeven_guard_active=True,
            breakeven_stop_price=2.431,
            trailing_stop_active=True,
            trailing_stop_price=2.44,
        )
        assert pos.stop_exit_reason() == "breakeven_stop"

    def test_breakeven_stop_short(self):
        pos = _make_position(
            side="short",
            entry_price=100.0,
            stop_loss_price=99.74,
            breakeven_guard_active=True,
            breakeven_stop_price=99.74,
        )
        assert pos.stop_exit_reason() == "breakeven_stop"


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)
    return mod


@pytest.fixture
def monitor_stubs(monkeypatch):
    """Minimal stubs so PositionMonitor can be imported and tested in isolation."""
    redis_client = MagicMock()
    redis_client.get.return_value = None
    redis_client.set.return_value = True
    redis_client.setex.return_value = True
    redis_client.exists.return_value = False
    redis_client.delete.return_value = True

    _stub_module("backend")
    _stub_module("backend.redis", get_redis_client=lambda: redis_client)
    _stub_module(
        "backend.redis.keys",
        POSITION_KEY="pos:{symbol}",
        POSITION_TP1_PRICE_KEY="position:tp1_price:{symbol}",
        POSITION_TP1_HIT_KEY="position:tp1_hit:{symbol}",
        POSITION_EXIT_REASON_KEY="pos:exit_reason:{symbol}",
        POSITION_EXIT_REASON_TTL=300,
        POSITION_EXIT_ATTEMPT_KEY="pos:exit_attempt:{symbol}",
        POSITION_EXIT_ATTEMPT_TTL=60,
        POSITION_EXIT_FAIL_COUNT_KEY="pos:exit_fail:{symbol}",
        POSITION_EXIT_FAIL_MAX=3,
        FORCED_EXIT_COOLDOWN_KEY="forced_exit:{symbol}:{strategy_id}",
        FORCED_EXIT_COOLDOWN_TTL=2700,
        SIGNAL_EXECUTED_KEY_LEGACY="signal:executed:{strategy_id}:{symbol}",
        SIGNAL_COOLDOWN_SECONDS=900,
    )
    _stub_module("backend.intervals")
    _stub_module("backend.intervals.config", POSITION_MONITOR_INTERVAL_SECONDS=10)
    db_session = MagicMock()
    strategy = MagicMock()
    strategy.name = "vwap_meanrev"
    db_session.query.return_value.filter.return_value.first.return_value = strategy
    _stub_module("backend.db", get_session=MagicMock(return_value=db_session))
    _stub_module("backend.db.models", Strategy=MagicMock())
    _stub_module("backend.execution")
    _stub_module("backend.execution.executor", execute_trade=AsyncMock(return_value=None))
    _stub_module("backend.api.routes.events", log_activity=MagicMock())
    _stub_module(
        "backend.positions.tracker",
        get_position_tracker=MagicMock(),
        get_redis_client=lambda: redis_client,
    )

    for mod in list(sys.modules):
        if mod == "backend.positions.monitor" or mod.startswith("backend.positions.monitor."):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    from backend.positions.monitor import PositionMonitor

    return PositionMonitor(), redis_client


@pytest.mark.asyncio
async def test_check_stop_loss_exit_uses_breakeven_reason(monitor_stubs):
    monitor, _redis = monitor_stubs
    pos = _make_position(
        stop_loss_price=2.431,
        breakeven_guard_active=True,
        breakeven_stop_price=2.431,
    )
    current_price = 2.430

    with patch.object(monitor, "_force_exit_position", new_callable=AsyncMock) as mock_force:
        await monitor._check_stop_loss_exit(pos, current_price)

    mock_force.assert_awaited_once()
    assert mock_force.await_args.kwargs["reason"] == "breakeven_stop"
    assert mock_force.await_args.kwargs["current_price"] == current_price


@pytest.mark.asyncio
async def test_check_stop_loss_exit_uses_initial_stop_reason(monitor_stubs):
    monitor, _redis = monitor_stubs
    pos = _make_position(stop_loss_price=2.389, breakeven_guard_active=False)
    current_price = 2.388

    with patch.object(monitor, "_force_exit_position", new_callable=AsyncMock) as mock_force:
        await monitor._check_stop_loss_exit(pos, current_price)

    mock_force.assert_awaited_once()
    assert mock_force.await_args.kwargs["reason"] == "stop_loss"
