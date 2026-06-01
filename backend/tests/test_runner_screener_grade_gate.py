"""Tests for runner screener grade gate (skip generate_signals on D/F/missing)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.redis.keys import APLUS_SCORES_KEY, SCREENER_RESULTS_KEY
from research.strategies.types import MarketDataEvent


def _reset_screener_gate_caches() -> None:
    import backend.runner.service as rs

    rs._screener_results_cache = None
    rs._screener_results_cache_expiry = 0.0
    rs._aplus_grade_cache.clear()


def _btc_result_row(**indicators):
    return {
        "symbol": "BTC/USD",
        "signal_type": "NONE",
        "signal_strength": 0.0,
        "indicators": indicators,
        "timestamp": "2026-01-01T00:00:00Z",
    }


def _make_redis(results_list, hget_grade_json):
    client = MagicMock()

    def _get(key):
        if key == SCREENER_RESULTS_KEY:
            return json.dumps(results_list)
        return None

    client.get.side_effect = _get

    def _hget(key, field):
        if key == APLUS_SCORES_KEY and field == "BTC/USD":
            return hget_grade_json
        return None

    client.hget.side_effect = _hget
    return client


@pytest.fixture(autouse=True)
def reset_caches():
    _reset_screener_gate_caches()
    yield
    _reset_screener_gate_caches()


class TestScreenerAllowsStrategyEvaluation:
    """_screener_allows_strategy_evaluation: membership + grade + position bypass."""

    def test_blocks_grade_f_from_aplus(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "F"}))
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False

    def test_allows_when_position_open_despite_grade_f(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "F"}))
        tracker = MagicMock()
        tracker.has_position.return_value = True

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is True

    def test_blocks_when_no_aplus_grade_even_if_in_results(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], None)
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False

    def test_allows_grade_c_from_aplus(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "C"}))
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is True

    def test_allows_grade_a_plus_from_indicators(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        row = _btc_result_row()
        row["indicators"] = {"grade": "A+"}
        redis = _make_redis([row], None)
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is True

    def test_blocks_grade_d(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "D"}))
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False

    def test_blocks_missing_grade_after_aplus_empty(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        redis = _make_redis([_btc_result_row()], None)
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False

    def test_blocks_on_exception_fail_closed(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch(
            "backend.runner.service.get_position_tracker",
            return_value=tracker,
        ):
            with patch(
                "backend.runner.service._resolve_screener_grade",
                side_effect=RuntimeError("redis timeout"),
            ):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False

    def test_aplus_hash_takes_precedence_over_indicators(self):
        from backend.runner.service import _screener_allows_strategy_evaluation

        row = _btc_result_row()
        row["indicators"] = {"grade": "A+"}
        redis = _make_redis([row], json.dumps({"grade": "F"}))
        tracker = MagicMock()
        tracker.has_position.return_value = False

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                assert _screener_allows_strategy_evaluation("BTC/USD") is False


def _eth_row():
    return {
        "symbol": "ETH/USD",
        "signal_type": "NONE",
        "signal_strength": 0.0,
        "indicators": {},
        "timestamp": "2026-01-01T00:00:00Z",
    }


class TestStrategyWorkerProcessBarGate:
    """StrategyWorker._process_bar skips generate_signals when gate blocks."""

    @pytest.mark.asyncio
    async def test_generate_signals_not_called_when_grade_f(self):
        from backend.runner.service import StrategyWorker

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "F"}))
        tracker = MagicMock()
        tracker.has_position.return_value = False
        mock_strategy = MagicMock()
        mock_strategy.generate_signals.return_value = None
        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        bar = MarketDataEvent(
            symbol="BTC/USD",
            interval="15m",
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=1.0,
            timestamp="2026-01-01T00:00:00Z",
        )

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                worker = StrategyWorker(
                    strategy_name="macd",
                    strategy_id="uuid-macd",
                    config=mock_config,
                    strategy=mock_strategy,
                )
                await worker._process_bar(bar)

        mock_strategy.generate_signals.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_signals_called_when_position_open(self):
        from backend.runner.service import StrategyWorker

        redis = _make_redis([_btc_result_row()], json.dumps({"grade": "F"}))
        tracker = MagicMock()
        tracker.has_position.return_value = True
        mock_strategy = MagicMock()
        mock_strategy.generate_signals.return_value = None
        mock_config = MagicMock()
        mock_config.symbol = "BTC/USD"
        mock_config.interval = "15m"

        bar = MarketDataEvent(
            symbol="BTC/USD",
            interval="15m",
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=1.0,
            timestamp="2026-01-01T00:00:00Z",
        )

        with patch("backend.runner.service.get_redis_client", return_value=redis):
            with patch("backend.runner.service.get_position_tracker", return_value=tracker):
                worker = StrategyWorker(
                    strategy_name="macd",
                    strategy_id="uuid-macd",
                    config=mock_config,
                    strategy=mock_strategy,
                )
                await worker._process_bar(bar)

        mock_strategy.generate_signals.assert_called_once()
