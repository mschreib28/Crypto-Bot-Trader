import pytest

from backend.risk.two_percent import TwoPercentRule
from backend.risk.sizing import PositionSizer
from backend.risk.account import AccountTracker


class TestTwoPercentRule:
    def test_calculate_max_risk(self):
        rule = TwoPercentRule(risk_pct=2.0)
        assert rule.calculate_max_risk(100.0) == 2.0
        assert rule.calculate_max_risk(50.0) == 1.0
        assert rule.calculate_max_risk(500.0) == 10.0

    def test_validate_trade_approved(self):
        rule = TwoPercentRule(risk_pct=2.0)
        approved, reason = rule.validate_trade(1.5, 100.0)
        assert approved is True
        assert reason == ""

    def test_validate_trade_rejected(self):
        rule = TwoPercentRule(risk_pct=2.0)
        approved, reason = rule.validate_trade(3.0, 100.0)
        assert approved is False
        assert "exceeds_2pct_rule" in reason


class TestPositionSizer:
    def test_calculate_position_size(self):
        sizer = PositionSizer()
        result = sizer.calculate(
            account_equity=100.0,
            risk_pct=2.0,
            entry_price=3200.0,
            stop_loss_pct=5.0,
        )
        assert result.max_risk_usd == 2.0
        assert result.position_size_usd == 40.0
        assert result.quantity == 0.0125
        assert result.stop_loss_price == 3040.0

    def test_validate_minimum_pass(self):
        sizer = PositionSizer()
        valid, _ = sizer.validate_minimum(40.0)
        assert valid is True

    def test_validate_minimum_fail(self):
        sizer = PositionSizer()
        valid, reason = sizer.validate_minimum(0.5)
        assert valid is False
        assert "below_kraken_minimum" in reason

    def test_calculate_rejects_below_min_notional(self):
        """Sub-$1 position notional before rounding must return None (shadow skips micro_mode)."""
        sizer = PositionSizer()
        # equity 500 avoids micro mode; tiny risk yields position_size_usd < $1
        assert (
            sizer.calculate(
                account_equity=500.0,
                risk_pct=0.002,
                entry_price=100.0,
                stop_loss_pct=5.0,
                strategy_id=None,
                symbol="TEST/USD",
            )
            is None
        )


class TestAccountTracker:
    def test_initial_equity(self):
        tracker = AccountTracker(initial_equity=100.0)
        assert tracker.current_equity == 100.0

    def test_record_pnl(self):
        tracker = AccountTracker(initial_equity=100.0)
        tracker.record_pnl(5.0)
        assert tracker.current_equity == 105.0
        assert tracker.realized_pnl == 5.0

    def test_record_loss(self):
        tracker = AccountTracker(initial_equity=100.0)
        tracker.record_pnl(-3.0)
        assert tracker.current_equity == 97.0
