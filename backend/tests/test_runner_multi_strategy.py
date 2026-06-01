"""Unit tests for the multi-strategy runner (MultiStrategyRunner).

TDD RED phase: These tests fail until MultiStrategyRunner is implemented in
backend/runner/service.py.

Tests cover:
- One asyncio Task is created per active strategy row
- Graceful handling when strategy init fails (logs, skips, does not crash)
- Tasks run concurrently via asyncio.gather (all complete)
- Correct consumer group per strategy: 'runner:{strategy_name}'
- Correct stream key derived from strategy's symbol+interval
- If DB query returns zero active strategies, nothing raises
- Each worker task processes bars independently
"""

import asyncio
import json
import os
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from backend.redis.keys import APLUS_SCORES_KEY, SCREENER_RESULTS_KEY


# ---------------------------------------------------------------------------
# Helpers — stub Redis, DB session, and strategy registry before imports
# ---------------------------------------------------------------------------

def _make_redis_stub():
    client = MagicMock()
    client.ping.return_value = True
    client.set.return_value = True
    client.setex.return_value = True
    client.exists.return_value = False
    client.hgetall.return_value = {}
    client.xrange.return_value = []

    _rows = [
        {
            "symbol": "BTC/USD",
            "signal_type": "NONE",
            "signal_strength": 0.0,
            "indicators": {"grade": "A+"},
            "timestamp": "",
        },
        {
            "symbol": "ETH/USD",
            "signal_type": "NONE",
            "signal_strength": 0.0,
            "indicators": {"grade": "A+"},
            "timestamp": "",
        },
    ]

    def _get_side_effect(key):
        if key == SCREENER_RESULTS_KEY:
            return json.dumps(_rows)
        return None

    client.get.side_effect = _get_side_effect

    def _hget_side_effect(key, field):
        if key == APLUS_SCORES_KEY and field in ("BTC/USD", "ETH/USD"):
            return json.dumps({"grade": "A+"})
        return None

    client.hget.side_effect = _hget_side_effect
    return client


def _make_db_strategy(name, config, status="active"):
    """Create a mock Strategy ORM object."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.name = name
    row.config = config
    row.status = status
    return row


def _vwap_db_row():
    return _make_db_strategy(
        "vwap_meanreversion",
        {
            "strategy_id": "vwap_meanreversion",
            "symbol": "BTC/USD",
            "interval": "15m",
            "htf_interval": "1h",
            "parameters": {"atr_stop_mult": 1.5},
        },
    )


def _htf_db_row():
    return _make_db_strategy(
        "htf_trend_pullback",
        {
            "strategy_id": "htf_trend_pullback",
            "symbol": "BTC/USD",
            "interval": "1h",
            "htf_interval": "4h",
            "parameters": {},
        },
    )


def _volatility_db_row():
    return _make_db_strategy(
        "volatility_breakout",
        {
            "strategy_id": "volatility_breakout",
            "symbol": "BTC/USD",
            "interval": "15m",
            "htf_interval": "1h",
            "parameters": {},
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def redis_stub():
    return _make_redis_stub()


@pytest.fixture
def patch_redis(redis_stub):
    import backend.runner.service as runner_svc

    runner_svc._screener_results_cache = None
    runner_svc._screener_results_cache_expiry = 0.0
    runner_svc._aplus_grade_cache.clear()
    with patch("backend.redis.get_redis_client", return_value=redis_stub):
        with patch("backend.runner.service.get_redis_client", return_value=redis_stub):
            with patch("backend.redis.get_connection_pool"):
                yield redis_stub


@pytest.fixture
def sample_bar_data():
    return {
        "symbol": "BTC/USD",
        "interval": "15m",
        "open": "50000.0",
        "high": "51000.0",
        "low": "49500.0",
        "close": "50500.0",
        "volume": "100.0",
        "timestamp": "2026-03-09T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Tests: MultiStrategyRunner._load_active_strategies()
# ---------------------------------------------------------------------------

class TestLoadActiveStrategies:
    """_load_active_strategies() reads from DB and returns Strategy rows."""

    def test_returns_only_active_rows(self, patch_redis):
        """Only rows with status='active' are returned."""
        active_row = _vwap_db_row()
        inactive_row = _make_db_strategy("meanrev", {}, status="inactive")

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = [active_row]

        with patch("backend.runner.service.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = Mock(return_value=False)

            from backend.runner.service import MultiStrategyRunner
            runner = MultiStrategyRunner()
            rows = runner._load_active_strategies()

        assert len(rows) == 1
        assert rows[0].name == "vwap_meanreversion"

    def test_returns_empty_list_when_no_active_strategies(self, patch_redis):
        """No active rows → empty list, no exception."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []

        with patch("backend.runner.service.get_db_session") as mock_get_session:
            mock_get_session.return_value.__enter__ = Mock(return_value=mock_session)
            mock_get_session.return_value.__exit__ = Mock(return_value=False)

            from backend.runner.service import MultiStrategyRunner
            runner = MultiStrategyRunner()
            rows = runner._load_active_strategies()

        assert rows == []

    def test_db_error_returns_empty_list(self, patch_redis):
        """If DB raises, returns empty list without crashing."""
        with patch("backend.runner.service.get_db_session") as mock_get_session:
            mock_get_session.side_effect = Exception("DB connection refused")

            from backend.runner.service import MultiStrategyRunner
            runner = MultiStrategyRunner()
            rows = runner._load_active_strategies()

        assert rows == []


# ---------------------------------------------------------------------------
# Tests: MultiStrategyRunner._build_strategy_worker()
# ---------------------------------------------------------------------------

class TestBuildStrategyWorker:
    """_build_strategy_worker() creates a StrategyWorker or returns None on failure."""

    def test_creates_worker_for_known_strategy(self, patch_redis):
        """Known strategy name → returns a StrategyWorker."""
        from backend.runner.service import MultiStrategyRunner, StrategyWorker

        with patch("backend.strategies.registry.create_strategy") as mock_create:
            mock_config = MagicMock()
            mock_config.symbol = "BTC/USD"
            mock_config.interval = "15m"
            mock_strategy = MagicMock()
            mock_create.return_value = (mock_config, mock_strategy)

            runner = MultiStrategyRunner()
            worker = runner._build_strategy_worker(_vwap_db_row())

        assert worker is not None
        assert isinstance(worker, StrategyWorker)

    def test_returns_none_for_unknown_strategy(self, patch_redis):
        """Unknown strategy name → returns None, does NOT raise."""
        from backend.runner.service import MultiStrategyRunner

        unknown_row = _make_db_strategy("alien_strategy", {"symbol": "BTC/USD", "interval": "5m"})

        with patch("backend.strategies.registry.create_strategy", return_value=None):
            runner = MultiStrategyRunner()
            worker = runner._build_strategy_worker(unknown_row)

        assert worker is None

    def test_returns_none_when_registry_raises(self, patch_redis):
        """If registry raises (bad config), returns None instead of propagating."""
        from backend.runner.service import MultiStrategyRunner

        with patch("backend.strategies.registry.create_strategy",
                   side_effect=TypeError("unexpected field 'foo'")):
            runner = MultiStrategyRunner()
            worker = runner._build_strategy_worker(_vwap_db_row())

        assert worker is None


# ---------------------------------------------------------------------------
# Tests: StrategyWorker consumer group naming
# ---------------------------------------------------------------------------

class TestStrategyWorkerConsumerGroup:
    """StrategyWorker uses 'runner:{strategy_name}' as its consumer group."""

    def test_consumer_group_name(self, patch_redis):
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"
        mock_strategy = MagicMock()

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=mock_strategy,
        )

        assert worker.consumer_group == "runner:vwap_meanreversion"

    def test_stream_key_derived_from_symbol_and_interval(self, patch_redis):
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"
        mock_strategy = MagicMock()

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=mock_strategy,
        )

        # Stream key format: market:ohlcv:{symbol}:{interval}
        assert worker.stream_key == "market:ohlcv:BTC/USD:15m"

    def test_different_strategies_have_different_consumer_groups(self, patch_redis):
        from backend.runner.service import StrategyWorker

        def make_worker(name, symbol, interval):
            cfg = MagicMock()
            cfg.symbol = symbol
            cfg.interval = interval
            return StrategyWorker(
                strategy_name=name,
                strategy_id=f"uuid-{name}",
                config=cfg,
                strategy=MagicMock(),
            )

        w1 = make_worker("vwap_meanreversion", "BTC/USD", "15m")
        w2 = make_worker("volatility_breakout", "BTC/USD", "15m")
        w3 = make_worker("htf_trend_pullback", "BTC/USD", "1h")

        assert w1.consumer_group != w2.consumer_group
        assert w2.consumer_group != w3.consumer_group
        assert w1.consumer_group == "runner:vwap_meanreversion"
        assert w2.consumer_group == "runner:volatility_breakout"
        assert w3.consumer_group == "runner:htf_trend_pullback"


# ---------------------------------------------------------------------------
# Tests: StrategyWorker._process_bar() delegates to strategy
# ---------------------------------------------------------------------------

class TestStrategyWorkerProcessBar:
    """StrategyWorker._process_bar() feeds bar to strategy and routes intents."""

    @pytest.mark.asyncio
    async def test_calls_generate_signals(self, patch_redis):
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"
        mock_strategy = MagicMock()
        mock_strategy.generate_signals.return_value = None  # No signal

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=mock_strategy,
        )

        bar = MagicMock()
        bar.symbol = "BTC/USD"
        bar.close = 50000.0

        with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
            with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                await worker._process_bar(bar)

        mock_strategy.generate_signals.assert_called_once_with(bar)

    @pytest.mark.asyncio
    async def test_routes_signal_through_risk_manager(self, patch_redis):
        """When strategy emits a signal, it is evaluated by the risk manager."""
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        mock_intent = MagicMock()
        mock_intent.strategy_id = "uuid-001"
        mock_intent.symbol = "BTC/USD"
        mock_intent.side = "buy"
        mock_intent.intent_type = "enter"
        mock_intent.notional_risk_pct = 0.02
        mock_intent.metadata = {}

        mock_strategy = MagicMock()
        mock_strategy.generate_signals.return_value = mock_intent

        mock_decision = MagicMock()
        mock_decision.approved = False
        mock_decision.rejection_reason = "test_reject"

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=mock_strategy,
        )

        bar = MagicMock()
        bar.symbol = "BTC/USD"
        bar.close = 50000.0

        with patch("backend.runner.service.evaluate_intent", return_value=mock_decision) as mock_eval:
            with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                    await worker._process_bar(bar)

        mock_eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_called_when_approved_and_shadow_on(self, patch_redis):
        """Approved intent + shadow mode → execute_trade called."""
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        mock_intent = MagicMock()
        mock_intent.strategy_id = "uuid-001"
        mock_intent.symbol = "BTC/USD"
        mock_intent.side = "buy"
        mock_intent.intent_type = "enter"
        mock_intent.notional_risk_pct = 0.02
        mock_intent.metadata = {}

        mock_strategy = MagicMock()
        mock_strategy.generate_signals.return_value = mock_intent

        mock_decision = MagicMock()
        mock_decision.approved = True
        mock_decision.intent_id = "intent-abc"
        mock_decision.evaluated_portfolio_risk = 1.0

        mock_fill = MagicMock()
        mock_execute = AsyncMock(return_value=mock_fill)

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=mock_strategy,
        )

        bar = MagicMock()
        bar.symbol = "BTC/USD"
        bar.close = 50000.0

        mock_tracker = MagicMock()
        mock_tracker.has_position.return_value = False
        runner_redis = _make_redis_stub()

        with patch.dict(os.environ, {"SUPERVISOR_GATE_ENABLED": "0"}):
            with patch("backend.runner.service.get_redis_client", return_value=runner_redis):
                with patch("backend.runner.service.evaluate_intent", return_value=mock_decision):
                    with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                            with patch("backend.runner.service.get_position_tracker", return_value=mock_tracker):
                                with patch("backend.runner.service.execute_trade", mock_execute):
                                    await worker._process_bar(bar)

        mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: MultiStrategyRunner.run() — concurrent task creation
# ---------------------------------------------------------------------------

class TestMultiStrategyRunnerRun:
    """MultiStrategyRunner.run() spawns one task per active strategy."""

    @pytest.mark.asyncio
    async def test_creates_one_task_per_active_strategy(self, patch_redis):
        """
        Given: 2 active strategies in DB
        When:  run() is called (then immediately stopped)
        Then:  2 worker tasks are created
        """
        from backend.runner.service import MultiStrategyRunner

        db_rows = [_vwap_db_row(), _htf_db_row()]

        mock_worker_1 = MagicMock()
        mock_worker_2 = MagicMock()
        mock_worker_1.strategy_name = "mock_worker_1"
        mock_worker_2.strategy_name = "mock_worker_2"

        task_count = []

        async def fake_worker_run():
            task_count.append(1)

        mock_worker_1.run = AsyncMock(side_effect=fake_worker_run)
        mock_worker_2.run = AsyncMock(side_effect=fake_worker_run)

        with patch("backend.runner.service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = db_rows
            mock_db.return_value.__enter__ = Mock(return_value=mock_session)
            mock_db.return_value.__exit__ = Mock(return_value=False)

            with patch("backend.strategies.registry.create_strategy") as mock_create:
                mock_cfg = MagicMock()
                mock_cfg.symbol = "BTC/USD"
                mock_cfg.interval = "15m"
                mock_create.return_value = (mock_cfg, MagicMock())

                runner = MultiStrategyRunner()

                # Patch _build_strategy_worker to return our mock workers
                workers_sequence = [mock_worker_1, mock_worker_2]
                runner._build_strategy_worker = Mock(side_effect=workers_sequence)

                # Run with a timeout so tests don't hang
                with patch("backend.screener.service.ScreenerService"):
                    try:
                        await asyncio.wait_for(runner.run(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass  # Expected — runner loops forever until stopped

        # Both workers' run() should have been scheduled
        mock_worker_1.run.assert_called_once()
        mock_worker_2.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_active_strategies_does_not_raise(self, patch_redis):
        """
        Given: No active strategies in DB
        When:  run() is called
        Then:  No exception is raised
        """
        from backend.runner.service import MultiStrategyRunner

        with patch("backend.runner.service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = []
            mock_db.return_value.__enter__ = Mock(return_value=mock_session)
            mock_db.return_value.__exit__ = Mock(return_value=False)

            runner = MultiStrategyRunner()

            with patch("backend.screener.service.ScreenerService"):
                try:
                    await asyncio.wait_for(runner.run(), timeout=0.3)
                except asyncio.TimeoutError:
                    pass  # Expected
                except Exception as e:
                    pytest.fail(f"run() raised unexpectedly with 0 strategies: {e}")

    @pytest.mark.asyncio
    async def test_failed_strategy_init_does_not_block_others(self, patch_redis):
        """
        Given: 3 strategies, first one fails init, other 2 succeed
        When:  run() is called
        Then:  The 2 good strategies still get tasks
        """
        from backend.runner.service import MultiStrategyRunner

        db_rows = [_vwap_db_row(), _htf_db_row(), _volatility_db_row()]

        mock_good_worker = MagicMock()
        mock_good_worker.strategy_name = "mock_good_worker"
        mock_good_worker.run = AsyncMock()

        # First row fails, next two succeed
        build_sequence = [None, mock_good_worker, mock_good_worker]

        with patch("backend.runner.service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.all.return_value = db_rows
            mock_db.return_value.__enter__ = Mock(return_value=mock_session)
            mock_db.return_value.__exit__ = Mock(return_value=False)

            runner = MultiStrategyRunner()
            runner._build_strategy_worker = Mock(side_effect=build_sequence)

            with patch("backend.screener.service.ScreenerService"):
                try:
                    await asyncio.wait_for(runner.run(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

        # Both good workers must have been scheduled
        assert mock_good_worker.run.call_count == 2


# ---------------------------------------------------------------------------
# Tests: StrategyWorker._consume_next_bar() isolation
# ---------------------------------------------------------------------------

class TestStrategyWorkerConsumeNextBar:
    """StrategyWorker._consume_next_bar() reads from its own stream key."""

    @pytest.mark.asyncio
    async def test_consumes_from_correct_stream_key(self, patch_redis):
        """consume_stream is called with the worker's stream_key."""
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "ETH/USD"
        mock_config.interval = "1h"
        mock_strategy = MagicMock()

        worker = StrategyWorker(
            strategy_name="htf_trend_pullback",
            strategy_id="uuid-003",
            config=mock_config,
            strategy=mock_strategy,
        )

        with patch("backend.runner.service.consume_stream", return_value=[]) as mock_consume:
            result = await worker._consume_next_bar()

        assert result is None  # No messages returned
        mock_consume.assert_called_once()
        call_kwargs = mock_consume.call_args[1]
        assert call_kwargs.get("stream_key") == "market:ohlcv:ETH/USD:1h"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_messages(self, patch_redis):
        from backend.runner.service import StrategyWorker

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=MagicMock(),
        )

        with patch("backend.runner.service.consume_stream", return_value=[]):
            result = await worker._consume_next_bar()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_market_data_event_on_message(self, patch_redis, sample_bar_data):
        from backend.runner.service import StrategyWorker
        from research.strategies.types import MarketDataEvent

        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        worker = StrategyWorker(
            strategy_name="vwap_meanreversion",
            strategy_id="uuid-001",
            config=mock_config,
            strategy=MagicMock(),
        )

        messages = [{"id": "1234-0", "data": sample_bar_data}]

        with patch("backend.runner.service.consume_stream", return_value=messages):
            result = await worker._consume_next_bar()

        assert result is not None
        assert isinstance(result, MarketDataEvent)
        assert result.symbol == "BTC/USD"
        assert result.close == 50500.0


# ---------------------------------------------------------------------------
# Tests: backward compat — old StrategyRunner still importable
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """StrategyRunner (single-strategy) must still exist and be importable."""

    def test_strategy_runner_still_importable(self, patch_redis):
        from backend.runner.service import StrategyRunner
        assert StrategyRunner is not None

    def test_strategy_runner_init_does_not_raise(self, patch_redis):
        from backend.runner.service import StrategyRunner

        runner = StrategyRunner(
            strategy_id="mean_reversion",
            symbol="ETH/USD",
            interval="4h",
        )
        assert runner is not None
        assert runner.strategy_id == "mean_reversion"
