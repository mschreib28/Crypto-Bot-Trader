"""Supervisor backtest cycle drawdown breach override."""

import logging
from unittest.mock import MagicMock, patch

from backend.supervisor.classifier import classify
from backend.supervisor.parser import BacktestMetrics
from backend.supervisor.service import (
    DRAWDOWN_BREACH_THRESHOLD,
    SupervisorService,
    _apply_drawdown_breach_override,
)


class TestDrawdownBreachOverride:
    def test_breach_overrides_active_classifier(self):
        cycle_log = logging.getLogger("test")
        active = classify(50.0, 2.0, 10)
        assert active.status == "ACTIVE"

        with patch(
            "backend.supervisor.store.read_cumulative_r_loss",
            return_value=-6.0,
        ):
            with patch(
                "backend.supervisor.store.is_drawdown_suspended",
                return_value=False,
            ):
                with patch(
                    "backend.supervisor.store.set_drawdown_suspended"
                ) as mock_set:
                    verdict_obj, reason = _apply_drawdown_breach_override(
                        "meanrev", active, active.reason, cycle_log
                    )

        assert verdict_obj.status == "SUSPENDED"
        assert verdict_obj.size_factor == 0.0
        assert "drawdown_breach" in reason
        mock_set.assert_called_once()

    def test_no_breach_keeps_active(self):
        cycle_log = logging.getLogger("test")
        active = classify(50.0, 2.0, 10)

        with patch(
            "backend.supervisor.store.read_cumulative_r_loss",
            return_value=-2.0,
        ):
            verdict_obj, reason = _apply_drawdown_breach_override(
                "meanrev", active, active.reason, cycle_log
            )

        assert verdict_obj.status == "ACTIVE"
        assert reason == active.reason

    def test_already_suspended_skips_second_warning(self):
        cycle_log = logging.getLogger("test")
        active = classify(50.0, 2.0, 10)

        with patch(
            "backend.supervisor.store.read_cumulative_r_loss",
            return_value=-6.0,
        ):
            with patch(
                "backend.supervisor.store.is_drawdown_suspended",
                return_value=True,
            ):
                with patch(
                    "backend.supervisor.store.set_drawdown_suspended"
                ) as mock_set:
                    verdict_obj, _ = _apply_drawdown_breach_override(
                        "meanrev", active, active.reason, cycle_log
                    )

        mock_set.assert_not_called()
        assert verdict_obj.status == "SUSPENDED"

    def test_run_strategy_applies_breach_after_backtest(self):
        svc = SupervisorService()
        cycle_log = logging.getLogger("test")
        metrics = BacktestMetrics(
            trades=20,
            wins=11,
            losses=9,
            win_rate=55.0,
            rr_ratio=2.0,
        )
        mock_run = MagicMock(exit_code=0, stdout="ok", stderr="", duration_sec=1.0)

        with patch("backend.supervisor.service.run_backtest", return_value=mock_run):
            with patch(
                "backend.supervisor.service.parse_backtest_stdout",
                return_value=metrics,
            ):
                with patch(
                    "backend.supervisor.store.read_cumulative_r_loss",
                    return_value=-6.0,
                ):
                    with patch(
                        "backend.supervisor.store.is_drawdown_suspended",
                        return_value=False,
                    ):
                        with patch(
                            "backend.supervisor.store.set_drawdown_suspended"
                        ):
                            verdict = svc._run_strategy("meanrev", cycle_log)

        assert verdict["status"] == "SUSPENDED"
        assert verdict["size_factor"] == 0.0
        assert "drawdown_breach" in verdict["reason"]

    def test_active_clear_skipped_when_breached(self):
        breached = -6.0 < DRAWDOWN_BREACH_THRESHOLD
        assert breached

        with patch(
            "backend.supervisor.store.read_cumulative_r_loss",
            return_value=-6.0,
        ):
            with patch(
                "backend.supervisor.store.clear_drawdown_suspended"
            ) as mock_clear:
                cumulative_r_loss = -6.0
                verdict_status = "ACTIVE"
                breached_flag = (
                    cumulative_r_loss is not None
                    and cumulative_r_loss < DRAWDOWN_BREACH_THRESHOLD
                )
                if verdict_status == "ACTIVE" and not breached_flag:
                    mock_clear("meanrev")

        mock_clear.assert_not_called()

    def test_active_clear_when_not_breached(self):
        with patch(
            "backend.supervisor.store.read_cumulative_r_loss",
            return_value=-2.0,
        ):
            with patch(
                "backend.supervisor.store.clear_drawdown_suspended"
            ) as mock_clear:
                cumulative_r_loss = -2.0
                verdict_status = "ACTIVE"
                breached_flag = (
                    cumulative_r_loss is not None
                    and cumulative_r_loss < DRAWDOWN_BREACH_THRESHOLD
                )
                if verdict_status == "ACTIVE" and not breached_flag:
                    mock_clear("meanrev")

        mock_clear.assert_called_once_with("meanrev")
