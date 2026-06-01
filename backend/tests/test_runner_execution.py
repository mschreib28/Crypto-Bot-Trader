"""Unit tests for runner/service.py _process_bar() execution wiring.

RED phase: These tests verify that _process_bar() calls execute_trade() when
shadow mode or live trading is enabled. They SHOULD FAIL until Fix 1 is applied.

TDD cycle:
1. Write test (RED) - verify failure
2. Implement fix (GREEN) - verify pass
3. Refactor - verify stays green
"""

import asyncio
import json
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes.trading import BotModeRequest, set_bot_mode_endpoint
from backend.redis.keys import APLUS_SCORES_KEY, SCREENER_RESULTS_KEY


# ---------------------------------------------------------------------------
# Helpers: build a minimal stub for backend.redis before importing anything
# that touches it at module level.
# ---------------------------------------------------------------------------

def _make_redis_stub():
    """Return a mock Redis client that satisfies get/set/ping calls."""
    client = MagicMock()
    client.ping.return_value = True
    client.set.return_value = True
    client.setex.return_value = True
    client.exists.return_value = False
    # Must be a real empty dict — MagicMock default is truthy and breaks PositionTracker.
    client.hgetall.return_value = {}

    _eth_row = {
        "symbol": "ETH/USD",
        "signal_type": "NONE",
        "signal_strength": 0.0,
        "indicators": {"grade": "A+"},
        "timestamp": "",
    }

    def _get_side_effect(key):
        if key == SCREENER_RESULTS_KEY:
            return json.dumps([_eth_row])
        return None

    client.get.side_effect = _get_side_effect

    def _hget_side_effect(key, field):
        if key == APLUS_SCORES_KEY and field == "ETH/USD":
            return json.dumps({"grade": "A+"})
        return None

    client.hget.side_effect = _hget_side_effect
    return client


def _patch_redis():
    """Patch backend.redis.get_redis_client at the module level."""
    redis_stub = _make_redis_stub()
    return patch("backend.redis.get_redis_client", return_value=redis_stub)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis_client():
    """Provide a reusable mock Redis client."""
    return _make_redis_stub()


@pytest.fixture
def sample_bar():
    """Return a minimal MarketDataEvent-like object."""
    bar = MagicMock()
    bar.symbol = "ETH/USD"
    bar.interval = "4h"
    bar.open = 2000.0
    bar.high = 2050.0
    bar.low = 1980.0
    bar.close = 2030.0
    bar.volume = 5000.0
    bar.timestamp = "2026-03-09T00:00:00Z"
    return bar


@pytest.fixture
def sample_intent():
    """Return a minimal backend TradeIntent-like mock."""
    intent = MagicMock()
    intent.strategy_id = "mean_reversion"
    intent.symbol = "ETH/USD"
    intent.side = "buy"
    intent.intent_type = "enter"
    intent.notional_risk_pct = 0.02
    intent.metadata = {"source": "runner"}
    return intent


@pytest.fixture
def approved_decision(sample_intent):
    """Return a mock RiskDecision that is approved."""
    decision = MagicMock()
    decision.approved = True
    decision.intent_id = "test-intent-001"
    decision.evaluated_portfolio_risk = 1.5
    decision.rejection_reason = None
    return decision


@pytest.fixture
def rejected_decision():
    """Return a mock RiskDecision that is rejected."""
    decision = MagicMock()
    decision.approved = False
    decision.intent_id = "test-intent-002"
    decision.rejection_reason = "portfolio_limit_exceeded"
    return decision


@pytest.fixture
def mock_fill():
    """Return a mock Fill object."""
    fill = MagicMock()
    fill.symbol = "ETH/USD"
    fill.side = "buy"
    fill.quantity = 0.001
    fill.executed_price = 2030.0
    fill.order_id = "shadow_ETH_buy_1234"
    return fill


# ---------------------------------------------------------------------------
# Helper: build StrategyRunner with all external dependencies mocked
# ---------------------------------------------------------------------------

@contextmanager
def _build_runner(redis_client):
    """Construct a StrategyRunner with mocked Redis for the whole `with` block."""
    import backend.runner.service as runner_svc

    runner_svc._screener_results_cache = None
    runner_svc._screener_results_cache_expiry = 0.0
    runner_svc._aplus_grade_cache.clear()
    mock_tracker = MagicMock()
    mock_tracker.has_position.return_value = False
    # Patch where runner / tracker bind get_redis_client (import-time).
    with patch("backend.runner.service.get_redis_client", return_value=redis_client):
        with patch("backend.positions.tracker.get_redis_client", return_value=redis_client):
            with patch("backend.runner.service.get_position_tracker", return_value=mock_tracker):
                with patch("backend.runner.config.get_stream_key", return_value="test:stream"):
                    from backend.runner.service import StrategyRunner
                    runner = StrategyRunner(
                        strategy_id="mean_reversion",
                        symbol="ETH/USD",
                        interval="4h",
                    )
                    yield runner


# ---------------------------------------------------------------------------
# Test: _process_bar calls execute_trade when shadow mode is ON
# ---------------------------------------------------------------------------

class TestProcessBarCallsExecuteTrade:
    """
    Verify that _process_bar() wires through to execute_trade() when either
    shadow_live_mode or trading_enabled is True.

    These tests FAIL before Fix 1 (the TODO is replaced with real wiring).
    """

    @pytest.mark.asyncio
    async def test_calls_execute_trade_when_shadow_mode_on(
        self, mock_redis_client, sample_bar, sample_intent, approved_decision, mock_fill
    ):
        """
        Given: strategy produces a signal, risk approves it, shadow mode is ON
        When:  _process_bar() runs
        Then:  execute_trade() is called exactly once with the backend intent
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            # Wire up a strategy that generates one signal
            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=mock_fill)

            with patch("backend.runner.service.evaluate_intent", return_value=approved_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            await runner._process_bar(sample_bar)

            # execute_trade must have been called once (paper path)
            mock_execute.assert_called_once()
            call_args = mock_execute.call_args
            # First positional arg is the TradeIntent
            passed_intent = call_args[0][0]
            assert passed_intent.symbol == "ETH/USD"
            assert passed_intent.side == "buy"
            # Second positional arg is the current price
            passed_price = call_args[0][1]
            assert passed_price == sample_bar.close
            assert call_args.kwargs.get("live") is False

    @pytest.mark.asyncio
    async def test_calls_execute_trade_when_trading_enabled(
        self, mock_redis_client, sample_bar, sample_intent, approved_decision, mock_fill
    ):
        """
        Given: strategy produces a signal, risk approves it, live trading is ON
        When:  _process_bar() runs
        Then:  execute_trade() is called exactly once
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=mock_fill)

            with patch("backend.runner.service.evaluate_intent", return_value=approved_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="LIVE"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="LIVE"):
                        with patch(
                            "backend.runner.service.get_effective_mode",
                            return_value=("LIVE", 1.0),
                        ):
                            with patch("backend.runner.service.execute_trade", mock_execute):
                                await runner._process_bar(sample_bar)

            mock_execute.assert_called_once()
            assert mock_execute.call_args.kwargs.get("live") is True

    @pytest.mark.asyncio
    async def test_calls_execute_trade_paper_when_bot_mode_shadow(
        self, mock_redis_client, sample_bar, approved_decision
    ):
        """
        Given: strategy produces a signal, risk approves it, canonical bot mode SHADOW
        When:  _process_bar() runs
        Then:  execute_trade() is called once with live=False (paper)
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=None)

            with patch("backend.runner.service.evaluate_intent", return_value=approved_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            await runner._process_bar(sample_bar)

            mock_execute.assert_called_once()
            assert mock_execute.call_args.kwargs.get("live") is False

    @pytest.mark.asyncio
    async def test_does_not_call_execute_trade_when_no_signal(
        self, mock_redis_client, sample_bar
    ):
        """
        Given: strategy generates no signal (returns None)
        When:  _process_bar() runs
        Then:  execute_trade() is NOT called
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = None  # No signal
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=None)

            with patch("backend.risk.evaluator.evaluate_intent") as mock_evaluate:
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            await runner._process_bar(sample_bar)

            mock_evaluate.assert_not_called()
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_call_execute_trade_when_risk_rejected(
        self, mock_redis_client, sample_bar, rejected_decision
    ):
        """
        Given: strategy produces a signal, risk REJECTS it
        When:  _process_bar() runs
        Then:  execute_trade() is NOT called
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=None)

            with patch("backend.runner.service.evaluate_intent", return_value=rejected_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            await runner._process_bar(sample_bar)

            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_execute_trade_exception_gracefully(
        self, mock_redis_client, sample_bar, approved_decision
    ):
        """
        Given: execute_trade() raises an exception
        When:  _process_bar() runs
        Then:  the exception is caught and _process_bar() completes without raising
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(side_effect=RuntimeError("Kraken connection failed"))

            # Should not raise
            with patch("backend.runner.service.evaluate_intent", return_value=approved_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            await runner._process_bar(sample_bar)  # Must not raise

            mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_is_logged_on_success(
        self, mock_redis_client, sample_bar, approved_decision, mock_fill
    ):
        """
        Given: execute_trade() returns a Fill
        When:  _process_bar() runs
        Then:  the fill details (symbol, side, qty, price) are present in log output
               (no exception, runner continues)
        """
        with _build_runner(mock_redis_client) as runner:
            runner._last_price = sample_bar.close

            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = MagicMock(
                strategy_id="mean_reversion",
                symbol="ETH/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=0.02,
                metadata={},
            )
            runner._strategy = mock_strategy

            mock_execute = AsyncMock(return_value=mock_fill)

            with patch("backend.runner.service.evaluate_intent", return_value=approved_decision):
                with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                    with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.runner.service.execute_trade", mock_execute):
                            # Capture log output to verify fill details are logged
                            with patch("backend.runner.service.logger") as mock_logger:
                                await runner._process_bar(sample_bar)

            # The info call should contain fill details
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            fill_logged = any("ETH/USD" in c or "fill" in c.lower() for c in info_calls)
            assert fill_logged, f"Expected fill details in log. Got: {info_calls}"

    @pytest.mark.asyncio
    async def test_last_price_updated_from_bar(self, mock_redis_client, sample_bar):
        """
        Given: a bar with close=2030
        When:  _process_bar() runs (even with no signal)
        Then:  runner._last_price is updated to bar.close
        """
        with _build_runner(mock_redis_client) as runner:
            mock_strategy = MagicMock()
            mock_strategy.generate_signals.return_value = None  # No signal
            runner._strategy = mock_strategy

            with patch("backend.runner.service.get_bot_mode", return_value="SHADOW"):
                with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                    await runner._process_bar(sample_bar)

            assert runner._last_price == sample_bar.close


@pytest.mark.asyncio
async def test_post_bot_mode_live_without_confirm_returns_400():
    """Invariant #8: LIVE requires confirm token."""
    with pytest.raises(HTTPException) as exc_info:
        await set_bot_mode_endpoint(BotModeRequest(mode="LIVE", confirm=None))
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        await set_bot_mode_endpoint(BotModeRequest(mode="LIVE", confirm="wrong"))
    assert exc_info.value.status_code == 400
