"""Unit tests for backend.supervisor.live_evaluator."""

from unittest.mock import MagicMock, patch

import pytest

from backend.supervisor.classifier import StrategyVerdict
from backend.supervisor.live_evaluator import (
    _aggregate_window,
    _apply_promotion_gate,
    classify_live,
    evaluate_live_stats,
)


def test_aggregate_window_rr_avg_win_over_avg_loss_mag():
    # wins at +1, +2 -> avg 1.5; losses at -1, -2 -> avg loss mag (1+2)/2 = 1.5 -> R:R 1.0
    t, w, l, wr, rr = _aggregate_window([1.0, 2.0, -1.0, -2.0])
    assert t == 4 and w == 2 and l == 2
    assert wr == 50.0
    assert abs(rr - 1.0) < 1e-6


def test_aggregate_window_all_wins_inf_rr_stored_elsewhere():
    t, w, l, wr, rr = _aggregate_window([1.0, 0.5])
    assert rr == float("inf")


def test_classify_live_active():
    v = classify_live(55.0, 2.0, 10)
    assert v.status == "ACTIVE"


def test_classify_live_reduced():
    v = classify_live(40.0, 1.0, 10)
    assert v.status == "REDUCED"


def test_classify_live_suspended_below_reduced():
    v = classify_live(30.0, 1.0, 10)
    assert v.status == "SUSPENDED"


def test_evaluate_live_stats_insufficient_returns_none():
    session = MagicMock()
    with patch(
        "backend.supervisor.live_evaluator.strategy_uuids_for_canonical",
        return_value=["uuid-1"],
    ):
        with patch(
            "backend.supervisor.live_evaluator._load_r_multiples_in_window",
            return_value=[1.0, -1.0, 0.5],
        ):
            assert evaluate_live_stats("htf_trend", session) is None


def test_evaluate_live_stats_sufficient_returns_dict_with_verdict_obj():
    session = MagicMock()
    r_list = [1.0, -1.0, 1.0, 0.5, -0.5]
    with patch(
        "backend.supervisor.live_evaluator.strategy_uuids_for_canonical",
        return_value=["uuid-1"],
    ):
        with patch(
            "backend.supervisor.live_evaluator._load_r_multiples_in_window",
            return_value=r_list,
        ):
            out = evaluate_live_stats("htf_trend", session)
            assert out is not None
            assert "verdict_obj" in out
            assert isinstance(out["verdict_obj"], StrategyVerdict)
            assert out["trades"] == 5


def test_promotion_gate_immediate_suspend():
    draft = {"strategy": "htf_trend", "status": "SUSPENDED", "trades": 5}
    raw = StrategyVerdict(status="SUSPENDED", size_factor=0.0, reason="x")
    with patch("backend.supervisor.live_evaluator.read_live_verdict", return_value=None):
        with patch("backend.supervisor.live_evaluator.get_redis_client") as m_redis:
            r = MagicMock()
            m_redis.return_value = r
            out = _apply_promotion_gate("htf_trend", draft, raw)
            assert out["status"] == "SUSPENDED"
            r.delete.assert_called()


def test_promotion_gate_two_cycles_from_suspended():
    draft = {"strategy": "htf_trend", "status": "REDUCED", "trades": 5}
    raw = StrategyVerdict(status="REDUCED", size_factor=0.5, reason="ok")
    prev = {"status": "SUSPENDED", "trades": 5}

    r1 = MagicMock()
    r1.get.return_value = None

    with patch("backend.supervisor.live_evaluator.read_live_verdict", return_value=prev):
        with patch("backend.supervisor.live_evaluator.get_redis_client", return_value=r1):
            out1 = _apply_promotion_gate("htf_trend", dict(draft), raw)
            assert out1["status"] == "SUSPENDED"
            assert "promotion_pending" in out1["reason"]

    r2 = MagicMock()
    r2.get.return_value = b"1"

    with patch("backend.supervisor.live_evaluator.read_live_verdict", return_value=prev):
        with patch("backend.supervisor.live_evaluator.get_redis_client", return_value=r2):
            out2 = _apply_promotion_gate("htf_trend", dict(draft), raw)
            assert out2["status"] == "REDUCED"


def test_get_effective_mode_live_overrides_backtest():
    from backend.supervisor.store import get_effective_mode

    live = {"trades": 5, "status": "SUSPENDED"}
    back = {"status": "ACTIVE", "trades": 100}

    with patch("backend.api.routes.trading.get_bot_mode", return_value="LIVE"):
        with patch("backend.supervisor.store.get_strategy_manual_mode", return_value="LIVE"):
            with patch("backend.supervisor.store.read_live_verdict", return_value=live):
                with patch("backend.supervisor.store.read_verdict", return_value=back):
                    mode, fac = get_effective_mode("htf_trend")
                    assert mode == "SIM" and fac == 1.0

    live2 = {"trades": 5, "status": "REDUCED"}
    with patch("backend.api.routes.trading.get_bot_mode", return_value="LIVE"):
        with patch("backend.supervisor.store.get_strategy_manual_mode", return_value="LIVE"):
            with patch("backend.supervisor.store.read_live_verdict", return_value=live2):
                with patch("backend.supervisor.store.read_verdict", return_value=back):
                    mode, fac = get_effective_mode("htf_trend")
                    assert mode == "LIVE" and fac == 0.5
