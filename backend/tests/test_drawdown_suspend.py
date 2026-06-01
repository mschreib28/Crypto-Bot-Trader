"""Tests for sticky drawdown suspend."""

from unittest.mock import MagicMock, patch

from backend.risk.metrics import StrategyMetrics


class TestDrawdownSuspend:
    def test_breach_suspends_once(self):
        with patch("backend.risk.metrics.get_redis_client", return_value=MagicMock()):
            metrics = StrategyMetrics()
        metrics.get_r_multiples = MagicMock(
            return_value={
                "r_multiples": [
                    {"r_multiple": -2.0},
                    {"r_multiple": -2.0},
                    {"r_multiple": -1.5},
                    {"r_multiple": -1.0},
                    {"r_multiple": -0.5},
                ]
            }
        )

        strategy = MagicMock()
        strategy.status = "active"
        strategy.name = "meanrev"
        strategy.config = {"max_drawdown_r": -5.0, "drawdown_window_trades": 20}

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = strategy

        with patch("backend.db.get_session", return_value=session):
            with patch("backend.api.routes.events.log_activity"):
                with patch(
                    "backend.supervisor.store.is_drawdown_suspended",
                    side_effect=[False, True],
                ):
                    with patch(
                        "backend.supervisor.store.set_drawdown_suspended"
                    ) as mock_set:
                        with patch(
                            "backend.supervisor.store.write_verdict"
                        ) as mock_write:
                            with patch(
                                "backend.supervisor.store.write_cumulative_r_loss"
                            ) as mock_cum:
                                metrics.check_strategy_drawdown("meanrev")
                                metrics.check_strategy_drawdown("meanrev")

        mock_cum.assert_called()
        assert mock_cum.call_args[0][1] == -7.0

        mock_set.assert_called_once()
        mock_write.assert_called_once()
        assert mock_write.call_args[0][1]["status"] == "SUSPENDED"

    def test_already_suspended_skips_warning(self):
        with patch("backend.risk.metrics.get_redis_client", return_value=MagicMock()):
            metrics = StrategyMetrics()
        metrics.get_r_multiples = MagicMock(
            return_value={
                "r_multiples": [{"r_multiple": -2.0}] * 5,
            }
        )

        strategy = MagicMock()
        strategy.status = "active"
        strategy.name = "meanrev"
        strategy.config = {}

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = strategy

        with patch("backend.db.get_session", return_value=session):
            with patch("backend.api.routes.events.log_activity") as mock_log:
                with patch(
                    "backend.supervisor.store.is_drawdown_suspended",
                    return_value=True,
                ):
                    with patch(
                        "backend.supervisor.store.set_drawdown_suspended"
                    ) as mock_set:
                        metrics.check_strategy_drawdown("meanrev")

        mock_set.assert_not_called()
        mock_log.assert_not_called()
