"""Unit tests for D2 momentum BUY gate helpers."""

from backend.screener.pipeline import (
    d2_momentum_passes,
    strategy_requires_d2_momentum,
)


class TestStrategyRequiresD2Momentum:
    def test_momentum_strategies_require_d2(self):
        assert strategy_requires_d2_momentum("volatility_breakout") is True
        assert strategy_requires_d2_momentum("bull_flag_1m") is True
        assert strategy_requires_d2_momentum("htf_trend_pullback") is True

    def test_mean_reversion_strategies_exempt(self):
        assert strategy_requires_d2_momentum("vwap_meanrev") is False
        assert strategy_requires_d2_momentum("vwap_meanreversion") is False
        assert strategy_requires_d2_momentum("meanrev") is False
        assert strategy_requires_d2_momentum("mean_reversion") is False


class TestD2MomentumPasses:
    def test_pass_when_pillar_pass_true(self):
        data = {"pillars": {"d2_momentum": {"pass": True}}}
        assert d2_momentum_passes(data) is True

    def test_fail_when_pillar_pass_false(self):
        data = {"pillars": {"d2_momentum": {"pass": False}}}
        assert d2_momentum_passes(data) is False

    def test_fail_closed_missing_data(self):
        assert d2_momentum_passes(None) is False
        assert d2_momentum_passes({}) is False
        assert d2_momentum_passes({"pillars": {}}) is False
