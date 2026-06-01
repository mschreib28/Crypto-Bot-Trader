"""Unit tests for screener/service.py _process_auto_execution() execution gates.

RED phase: These tests verify:
1. BUY signals with sufficient confidence reach execute_trade() in shadow mode
2. The grade gate is fail-closed in shadow mode (F/D/missing blocked; A+/A/B/C allowed)
3. current_price is always available before reaching execute_trade()
4. Below-confidence signals are properly blocked

TDD cycle:
1. Write test (RED) - verify failure (or existing partial behavior)
2. Implement fix (GREEN) - verify pass
3. Refactor - verify stays green
"""

from contextlib import AsyncExitStack, ExitStack
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from backend.screener.models import SignalResult
from backend.screener.pipeline import d2_momentum_passes, strategy_requires_d2_momentum


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_stub():
    """Return a mock Redis client."""
    client = MagicMock()
    client.ping.return_value = True
    client.get.return_value = None
    client.set.return_value = True
    client.setex.return_value = True
    client.exists.return_value = False
    client.lpush.return_value = 1
    client.ltrim.return_value = True
    return client


def _make_screener(redis_client=None):
    """Create a ScreenerService with mocked Redis."""
    if redis_client is None:
        redis_client = _make_redis_stub()
    with patch("backend.screener.service.get_redis_client", return_value=redis_client):
        with patch("backend.screener.service.log_activity"):
            from backend.screener.service import ScreenerService
            svc = ScreenerService(scan_interval_seconds=60.0, bars_to_fetch=250, interval="5m")
    return svc, redis_client


def _session_mock(strategy_name=None):
    """Return a mock DB session."""
    session = MagicMock()
    if strategy_name:
        strat = MagicMock()
        strat.name = strategy_name
        session.query.return_value.filter.return_value.first.return_value = strat
    else:
        session.query.return_value.filter.return_value.first.return_value = None
    session.close = Mock()
    return session


def _buy_signal(
    confidence=95.0,
    symbol="ETH/USD",
    strategy_id="strat-001",
    current_price=2030.0,
    extra_indicators=None,
):
    """Construct a high-confidence BUY SignalResult with current_price populated."""
    indicators = {
        "current_price": current_price,
        "bar_timestamp": "2026-03-09T00:00:00Z",
        "timeframe": "5m",
    }
    if extra_indicators:
        indicators.update(extra_indicators)
    return SignalResult(
        symbol=symbol,
        signal_type="BUY",
        confidence=confidence,
        strategy_id=strategy_id,
        indicators=indicators,
        timestamp="2026-03-09T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Base helper that wraps all common patches for _process_auto_execution tests
# ---------------------------------------------------------------------------

def _run_process(
    svc,
    redis_client,
    signal,
    *,
    shadow_mode=True,
    trading_enabled_flag=False,
    mock_execute=None,
    mock_decision=None,
    mock_position_tracker=None,
    grade=None,
    confidence_buy=70.0,
    confidence_sell=70.0,
    min_allowed_grade="A+",
    strategy_name=None,
):
    """
    Build an async function that calls _process_auto_execution with the standard
    set of mocks. Returns (coroutine, mock_execute).

    Callers should ``await`` the returned coroutine inside a test.
    """
    if mock_execute is None:
        mock_execute = AsyncMock(return_value=MagicMock())
    if mock_decision is None:
        mock_decision = MagicMock(approved=True, intent_id="d-x", rejection_reason=None)
    if mock_position_tracker is None:
        mock_position_tracker = MagicMock()
        mock_position_tracker.has_position.return_value = False
    if grade is None:
        grade = {"score": 0.95, "grade": "A+"}

    bot_mode = "LIVE" if (trading_enabled_flag and not shadow_mode) else "SHADOW"

    async def _run():
        with patch("backend.screener.service.get_redis_client", return_value=redis_client):
            with patch("backend.screener.service.log_activity"):
                with patch("backend.screener.service.get_bot_mode", return_value=bot_mode):
                    with patch("backend.screener.service.evaluate_intent", return_value=mock_decision):
                        with patch("backend.screener.service.execute_trade", mock_execute):
                            with patch("backend.screener.service.get_position_tracker", return_value=mock_position_tracker):
                                with patch(
                                    "backend.screener.service.get_session",
                                    return_value=_session_mock(strategy_name),
                                ):
                                    with patch("backend.screener.service.ScreenerService._get_aplus_score", return_value=grade):
                                        await svc._process_auto_execution(
                                            signal,
                                            trading_enabled=trading_enabled_flag or shadow_mode,
                                            confidence_buy=confidence_buy,
                                            confidence_sell=confidence_sell,
                                            min_allowed_grade=min_allowed_grade,
                                        )

    return _run, mock_execute


# ---------------------------------------------------------------------------
# Test: confidence gate
# ---------------------------------------------------------------------------

class TestConfidenceGate:
    """
    _process_auto_execution() must block signals below the confidence threshold
    and pass signals at or above it.
    """

    @pytest.mark.asyncio
    async def test_signal_above_threshold_reaches_execute_trade(self):
        """
        Given: BUY signal with confidence 95%, threshold 70%, shadow mode ON
        When:  _process_auto_execution() runs
        Then:  execute_trade() is called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)

        run, mock_execute = _run_process(
            svc, redis_client, signal, shadow_mode=True, confidence_buy=70.0
        )
        await run()

        mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_below_threshold_blocked(self):
        """
        Given: BUY signal with confidence 50%, threshold 70%
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=50.0)
        mock_execute = AsyncMock(return_value=None)

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            confidence_buy=70.0,
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_signal_at_threshold_passes(self):
        """
        Given: BUY signal with confidence exactly at threshold (70%)
        When:  _process_auto_execution() runs
        Then:  execute_trade() IS called (threshold is inclusive)
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=70.0)

        run, mock_execute = _run_process(
            svc, redis_client, signal, shadow_mode=True, confidence_buy=70.0
        )
        await run()

        mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# Test: grade gate (fail closed in shadow and live)
# ---------------------------------------------------------------------------

class TestGradeFilterShadowMode:
    """Grade gate blocks F/D/missing in SHADOW; min_allowed_grade still applies for score."""

    @pytest.mark.asyncio
    async def test_shadow_mode_blocks_f_grade_buy(self):
        """BUY with grade F must not execute in shadow mode."""
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        run, mock_execute = _run_process(
            svc,
            redis_client,
            signal,
            shadow_mode=True,
            grade={"score": 0.95, "grade": "F"},
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_shadow_mode_blocks_c_below_min_allowed_grade(self):
        """Grade C allowed by letter gate but blocked when score below min_allowed_grade."""
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        run, mock_execute = _run_process(
            svc,
            redis_client,
            signal,
            shadow_mode=True,
            grade={"score": 0.40, "grade": "C"},
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_mode_enforces_grade_filter(self):
        """
        Given: BUY signal with confidence 95%, live trading ON, symbol grade is 'C'
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called (below min_allowed_grade A+)
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=False,
            trading_enabled_flag=True,
            grade={"score": 0.40, "grade": "C"},
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_shadow_mode_aplus_grade_still_executes(self):
        """
        Given: BUY signal, shadow mode ON, symbol grade IS 'A+' (passes grade filter)
        When:  _process_auto_execution() runs
        Then:  execute_trade() IS called (no regression in A+ path)
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            grade={"score": 0.95, "grade": "A+"},
        )
        await run()

        mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# Test: D2 momentum gate (momentum strategies only)
# ---------------------------------------------------------------------------


class TestD2MomentumGate:
    """Non mean-reversion strategies require pillars.d2_momentum.pass at BUY."""

    _grade_b_d2_fail = {
        "score": 0.70,
        "grade": "B",
        "pillars": {"d2_momentum": {"pass": False}},
    }
    _grade_b_d2_pass = {
        "score": 0.70,
        "grade": "B",
        "pillars": {"d2_momentum": {"pass": True}},
    }

    @staticmethod
    def _d2_gate_blocks(strategy_name: str, aplus_data: dict) -> bool:
        """Mirror screener/service.py D2 gate before execute_trade."""
        return strategy_requires_d2_momentum(strategy_name) and not d2_momentum_passes(
            aplus_data
        )

    def test_volatility_breakout_blocked_without_d2(self):
        assert self._d2_gate_blocks("volatility_breakout", self._grade_b_d2_fail)

    def test_volatility_breakout_allowed_with_d2_pass(self):
        assert not self._d2_gate_blocks("volatility_breakout", self._grade_b_d2_pass)

    def test_vwap_meanrev_exempt_without_d2(self):
        assert not self._d2_gate_blocks("vwap_meanrev", self._grade_b_d2_fail)


# ---------------------------------------------------------------------------
# Test: current_price availability
# ---------------------------------------------------------------------------

class TestCurrentPriceGate:
    """
    Fix 3: current_price must be populated from signal indicators before
    reaching execute_trade(). Missing current_price must abort execution.
    """

    @pytest.mark.asyncio
    async def test_signal_with_current_price_reaches_execute_trade(self):
        """
        Given: BUY signal with current_price in indicators, shadow mode ON
        When:  _process_auto_execution() runs
        Then:  execute_trade() is called with that price
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0, current_price=2030.0)

        run, mock_execute = _run_process(svc, redis_client, signal, shadow_mode=True)
        await run()

        mock_execute.assert_called_once()
        passed_price = mock_execute.call_args[0][1]
        assert passed_price == 2030.0

    @pytest.mark.asyncio
    async def test_signal_without_current_price_aborts_execution(self):
        """
        Given: BUY signal with NO current_price in indicators
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called (logged as no_current_price error)
        """
        svc, redis_client = _make_screener()
        signal = SignalResult(
            symbol="ETH/USD",
            signal_type="BUY",
            confidence=95.0,
            strategy_id="strat-001",
            indicators={
                # current_price intentionally missing
                "bar_timestamp": "2026-03-09T00:00:00Z",
            },
            timestamp="2026-03-09T00:00:00Z",
        )
        mock_execute = AsyncMock(return_value=None)

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_signal_with_price_key_alternative_reaches_execute_trade(self):
        """
        Given: BUY signal with 'price' (not 'current_price') in indicators
        When:  _process_auto_execution() runs
        Then:  execute_trade() IS called using the 'price' value
        """
        svc, redis_client = _make_screener()
        signal = SignalResult(
            symbol="ETH/USD",
            signal_type="BUY",
            confidence=95.0,
            strategy_id="strat-001",
            indicators={
                "price": 2030.0,  # alternative key, no current_price
                "bar_timestamp": "2026-03-09T00:00:00Z",
            },
            timestamp="2026-03-09T00:00:00Z",
        )

        run, mock_execute = _run_process(svc, redis_client, signal, shadow_mode=True)
        await run()

        mock_execute.assert_called_once()
        passed_price = mock_execute.call_args[0][1]
        assert passed_price == 2030.0


# ---------------------------------------------------------------------------
# Test: trading_disabled gate
# ---------------------------------------------------------------------------

class TestTradingDisabledGate:
    """When trading_enabled is False (and shadow off), execution is blocked."""

    @pytest.mark.asyncio
    async def test_trading_disabled_blocks_execution(self):
        """
        Given: BUY signal with high confidence, trading_enabled=False, shadow off
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        # Override the trading_enabled arg to False, shadow_mode also False
        async def _run_disabled():
            with patch("backend.screener.service.get_redis_client", return_value=redis_client):
                with patch("backend.screener.service.log_activity"):
                    with patch("backend.screener.service.get_bot_mode", return_value="SHADOW"):
                        with patch("backend.screener.service.execute_trade", mock_execute):
                            with patch("backend.screener.service.get_session", return_value=_session_mock()):
                                await svc._process_auto_execution(
                                    signal,
                                    trading_enabled=False,  # Explicitly off
                                    confidence_buy=70.0,
                                    confidence_sell=70.0,
                                    min_allowed_grade="A+",
                                )

        await _run_disabled()
        mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test: position_exists gate (no double-buying)
# ---------------------------------------------------------------------------

class TestPositionExistsGate:
    """BUY signals for symbols with existing positions must be skipped."""

    @pytest.mark.asyncio
    async def test_buy_skipped_when_position_exists(self):
        """
        Given: BUY signal and position already exists for symbol
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        mock_tracker = MagicMock()
        mock_tracker.has_position.return_value = True  # Position exists!

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            mock_position_tracker=mock_tracker,
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test: risk evaluator rejection gate
# ---------------------------------------------------------------------------

class TestRiskEvaluatorGate:
    """execute_trade() must not be called when risk evaluator rejects the intent."""

    @pytest.mark.asyncio
    async def test_risk_rejection_blocks_execution(self):
        """
        Given: BUY signal with high confidence, risk evaluator rejects
        When:  _process_auto_execution() runs
        Then:  execute_trade() is NOT called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)
        mock_execute = AsyncMock(return_value=None)

        rejected = MagicMock(
            approved=False,
            intent_id="d-008",
            rejection_reason="daily_loss_limit_exceeded",
        )

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            mock_decision=rejected,
            mock_execute=mock_execute,
        )
        await run()

        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_approval_allows_execution(self):
        """
        Given: BUY signal with high confidence, risk evaluator approves
        When:  _process_auto_execution() runs
        Then:  execute_trade() IS called
        """
        svc, redis_client = _make_screener()
        signal = _buy_signal(confidence=95.0)

        approved = MagicMock(approved=True, intent_id="d-009", rejection_reason=None)

        run, mock_execute = _run_process(
            svc, redis_client, signal,
            shadow_mode=True,
            mock_decision=approved,
        )
        await run()

        mock_execute.assert_called_once()
