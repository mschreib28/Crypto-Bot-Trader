"""Tests for shadow account reset (position purge, metrics scan, SHADOW gating)."""

from fnmatch import fnmatch
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.positions.models import Position
from backend.positions.tracker import purge_all_position_redis_keys
from backend.risk.metrics import clear_all_strategy_metrics_and_r_multiples, delete_keys_by_pattern


class FakeRedis:
    """Minimal Redis stub for scan_iter + delete."""

    def __init__(self, keys):
        self._data = {k: b"1" for k in keys}

    def scan_iter(self, match):
        for k in list(self._data.keys()):
            if fnmatch(k, match):
                yield k

    def delete(self, *keys):
        n = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                n += 1
        return n

    def set(self, key, value):
        self._data[key] = value if isinstance(value, bytes) else str(value).encode("utf-8")
        return True


def test_purge_all_position_redis_keys_deletes_namespace():
    r = FakeRedis(
        [
            "position:ETH/USD",
            "position:status:ETH/USD",
            "position:cooldown:ETH/USD",
            "other:key",
        ]
    )
    n = purge_all_position_redis_keys(r)
    assert n == 3
    assert "other:key" in r._data
    assert not any(k.startswith("position:") for k in r._data)


def test_delete_keys_by_pattern_and_clear_metrics():
    from backend.redis.keys import METRICS_OPEN_TRADES_KEY

    r = FakeRedis(
        ["metrics:strategy:uuid-1", "strategy:r_multiples:uuid-1", METRICS_OPEN_TRADES_KEY]
    )
    assert delete_keys_by_pattern(r, "metrics:strategy:*") == 1
    assert METRICS_OPEN_TRADES_KEY in r._data
    r2 = FakeRedis(["metrics:strategy:a", "strategy:r_multiples:b", METRICS_OPEN_TRADES_KEY])
    assert clear_all_strategy_metrics_and_r_multiples(r2) == 3
    assert len(r2._data) == 0


@pytest.mark.asyncio
async def test_set_shadow_balance_purges_when_shadow():
    from backend.api.routes.account import set_shadow_balance, ShadowBalanceRequest

    fake_redis = FakeRedis(
        [
            "position:SOL/USD",
            "metrics:strategy:test-id",
            "strategy:r_multiples:test-id",
        ]
    )

    pos = Position(
        symbol="SOL/USD",
        side="long",
        quantity=1.0,
        entry_price=100.0,
        entry_time="2026-01-01T00:00:00+00:00",
        unrealized_pnl=0.0,
        opened_by_strategy_id="strat-uuid",
    )

    with patch("backend.api.routes.account.get_bot_mode", return_value="SHADOW"):
        with patch("backend.api.routes.account.get_redis_client", return_value=fake_redis):
            with patch(
                "backend.positions.tracker.get_position_tracker",
                return_value=MagicMock(get_all_positions=MagicMock(return_value=[pos])),
            ):
                with patch(
                    "backend.positions.tracker.purge_all_position_redis_keys",
                    wraps=purge_all_position_redis_keys,
                ) as purge_mock:
                    with patch(
                        "backend.risk.metrics.clear_all_strategy_metrics_and_r_multiples",
                        wraps=clear_all_strategy_metrics_and_r_multiples,
                    ) as clear_mock:
                        with patch("backend.risk.metrics.reset_strategy_metrics_for_ids") as reset_mock:
                            with patch("backend.db.get_session") as gs:
                                session = MagicMock()
                                session.query.return_value.all.return_value = []
                                session.close = MagicMock()
                                gs.return_value = session
                                with patch(
                                    "backend.execution.kraken_cli.paper_reset",
                                    new_callable=AsyncMock,
                                ) as pr:
                                    pr.side_effect = Exception("no cli")
                                    with patch("backend.api.routes.events.log_activity") as log_mock:
                                        out = await set_shadow_balance(ShadowBalanceRequest(total_usd=500.0))

    assert out["positions_closed"] == 1
    assert out["total_usd"] == 500.0
    purge_mock.assert_called_once()
    clear_mock.assert_called_once()
    reset_mock.assert_called_once()
    assert log_mock.call_count == 1
    kwargs = log_mock.call_args[1]
    assert "Shadow account reset" in kwargs["message"]
    assert "500.00" in kwargs["message"]
    assert kwargs["details"]["positions_closed"] == 1


@pytest.mark.asyncio
async def test_set_shadow_balance_skips_purge_when_live():
    from backend.api.routes.account import set_shadow_balance, ShadowBalanceRequest

    fake_redis = FakeRedis(["position:SOL/USD"])

    with patch("backend.api.routes.account.get_bot_mode", return_value="LIVE"):
        with patch("backend.api.routes.account.get_redis_client", return_value=fake_redis):
            with patch("backend.positions.tracker.purge_all_position_redis_keys") as purge_mock:
                with patch("backend.risk.metrics.clear_all_strategy_metrics_and_r_multiples") as clear_mock:
                    with patch("backend.risk.metrics.reset_strategy_metrics_for_ids") as reset_mock:
                        with patch(
                            "backend.execution.kraken_cli.paper_reset",
                            new_callable=AsyncMock,
                        ) as pr:
                            pr.side_effect = Exception("no cli")
                            with patch("backend.api.routes.events.log_activity"):
                                out = await set_shadow_balance(ShadowBalanceRequest(total_usd=100.0))

    assert out["positions_closed"] == 0
    assert "position:SOL/USD" in fake_redis._data
    purge_mock.assert_not_called()
    clear_mock.assert_not_called()
    reset_mock.assert_not_called()
