"""Tests for TP1-gated breakeven guard activation."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.positions.models import Position
from backend.positions.monitor import PositionMonitor


def _make_position(**overrides) -> Position:
    defaults = {
        "symbol": "NEAR/USD",
        "side": "long",
        "quantity": 7.0,
        "entry_price": 2.50,
        "entry_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "opened_by_strategy_id": "vwap_meanrev",
        "stop_loss_price": 2.38,
    }
    defaults.update(overrides)
    return Position(**defaults)


@pytest.fixture
def redis_store():
    return {}


@pytest.fixture
def mock_redis(redis_store):
    client = MagicMock()

    def _get(key):
        val = redis_store.get(key)
        if val is None:
            return None
        return val.encode() if isinstance(val, str) else val

    def _set(key, value, *args, **kwargs):
        redis_store[key] = value
        return True

    client.get.side_effect = _get
    client.set.side_effect = _set
    client.hset.return_value = True
    return client


@pytest.fixture
def monitor(mock_redis):
    m = PositionMonitor()
    m._update_kraken_stop_loss = AsyncMock()
    with patch("backend.redis.get_redis_client", return_value=mock_redis), patch(
        "backend.api.routes.events.log_activity"
    ):
        yield m


class TestBreakevenRequiresTp1:
    @pytest.mark.asyncio
    async def test_no_breakeven_before_tp1_despite_profit(self, monitor, redis_store):
        pos = _make_position()
        redis_store["position:tp1_price:NEAR/USD"] = "2.625"
        current_price = 2.55

        with patch("backend.config.BREAKEVEN_REQUIRES_TP1", True):
            await monitor._check_breakeven_guard(pos, current_price)

        assert pos.breakeven_guard_active is False
        assert "position:tp1_hit:NEAR/USD" not in redis_store

    @pytest.mark.asyncio
    async def test_tp1_hit_activates_breakeven(self, monitor, redis_store):
        pos = _make_position()
        tp1_price = 2.625
        redis_store["position:tp1_price:NEAR/USD"] = str(tp1_price)

        with patch("backend.config.BREAKEVEN_REQUIRES_TP1", True), patch(
            "backend.config.KRAKEN_FEE_PCT", 0.26
        ):
            await monitor._check_tp1_hit(pos, tp1_price)

        assert redis_store.get("position:tp1_hit:NEAR/USD") == "1"
        assert pos.breakeven_guard_active is True
        expected_be = pos.entry_price * 1.0026
        assert abs(pos.breakeven_stop_price - expected_be) < 0.0001
        monitor._update_kraken_stop_loss.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_post_tp1_pullback_exits_breakeven_stop(self, monitor):
        entry = 2.50
        be_stop = entry * 1.0026
        pos = _make_position(
            entry_price=entry,
            stop_loss_price=be_stop,
            breakeven_guard_active=True,
            breakeven_stop_price=be_stop,
        )

        with patch.object(monitor, "_force_exit_position", new_callable=AsyncMock) as mock_force:
            await monitor._check_stop_loss_exit(pos, entry)

        mock_force.assert_awaited_once()
        assert mock_force.await_args.kwargs["reason"] == "breakeven_stop"

    @pytest.mark.asyncio
    async def test_legacy_profit_pct_mode_without_tp1(self, monitor):
        pos = _make_position()
        current_price = pos.entry_price * 1.02

        with patch("backend.config.BREAKEVEN_REQUIRES_TP1", False), patch(
            "backend.config.BREAKEVEN_GUARD_TRIGGER_PCT", 1.0
        ), patch("backend.config.KRAKEN_FEE_PCT", 0.26):
            await monitor._check_breakeven_guard(pos, current_price)

        assert pos.breakeven_guard_active is True

    @pytest.mark.asyncio
    async def test_tp1_hit_does_not_duplicate_activation(self, monitor, redis_store):
        pos = _make_position(breakeven_guard_active=True, breakeven_stop_price=2.5065)
        redis_store["position:tp1_price:NEAR/USD"] = "2.625"
        redis_store["position:tp1_hit:NEAR/USD"] = "1"

        with patch("backend.config.BREAKEVEN_REQUIRES_TP1", True):
            await monitor._check_tp1_hit(pos, 2.63)

        monitor._update_kraken_stop_loss.assert_not_awaited()
