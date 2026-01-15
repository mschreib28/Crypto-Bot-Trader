"""Unit tests for the risk evaluator."""

import pytest
from datetime import datetime, timezone

from backend.risk.evaluator import evaluate_intent, TradeIntent
from backend.risk.models import RiskDecision


def test_trade_intent_validation():
    """Test TradeIntent field validation."""
    # Valid TradeIntent
    intent = TradeIntent(
        strategy_id="test-strategy-123",
        symbol="BTC/USD",
        side="buy",
        intent_type="enter",
        notional_risk_pct=5.0,
        metadata={}
    )
    assert intent.strategy_id == "test-strategy-123"
    assert intent.notional_risk_pct == 5.0
    
    # Invalid side
    with pytest.raises(ValueError, match="side must be 'buy' or 'sell'"):
        TradeIntent(
            strategy_id="test",
            symbol="BTC/USD",
            side="invalid",
            intent_type="enter",
            notional_risk_pct=5.0,
            metadata={}
        )
    
    # Invalid intent_type
    with pytest.raises(ValueError, match="intent_type must be 'enter', 'exit', or 'reduce'"):
        TradeIntent(
            strategy_id="test",
            symbol="BTC/USD",
            side="buy",
            intent_type="invalid",
            notional_risk_pct=5.0,
            metadata={}
        )
    
    # Invalid notional_risk_pct
    with pytest.raises(ValueError, match="notional_risk_pct must be positive"):
        TradeIntent(
            strategy_id="test",
            symbol="BTC/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=-1.0,
            metadata={}
        )


def test_risk_decision_validation():
    """Test RiskDecision field validation."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Valid approved decision
    decision = RiskDecision(
        intent_id="test-intent-123",
        approved=True,
        rejection_reason=None,
        evaluated_portfolio_risk=10.5,
        timestamp=timestamp
    )
    assert decision.approved is True
    assert decision.rejection_reason is None
    
    # Valid rejected decision
    decision = RiskDecision(
        intent_id="test-intent-456",
        approved=False,
        rejection_reason="exceeds_portfolio_limit",
        evaluated_portfolio_risk=55.0,
        timestamp=timestamp
    )
    assert decision.approved is False
    assert decision.rejection_reason == "exceeds_portfolio_limit"
    
    # Invalid: approved=True but rejection_reason is not None
    with pytest.raises(ValueError, match="rejection_reason must be None when approved is True"):
        RiskDecision(
            intent_id="test",
            approved=True,
            rejection_reason="some_reason",
            evaluated_portfolio_risk=10.0,
            timestamp=timestamp
        )
    
    # Invalid: approved=False but rejection_reason is None
    with pytest.raises(ValueError, match="rejection_reason must be provided when approved is False"):
        RiskDecision(
            intent_id="test",
            approved=False,
            rejection_reason=None,
            evaluated_portfolio_risk=10.0,
            timestamp=timestamp
        )


def test_evaluate_intent_returns_risk_decision():
    """Test that evaluate_intent returns a RiskDecision object."""
    intent = TradeIntent(
        strategy_id="test-strategy-123",
        symbol="BTC/USD",
        side="buy",
        intent_type="enter",
        notional_risk_pct=5.0,
        metadata={}
    )
    
    # This will likely reject due to missing data (fail-closed behavior)
    # but should still return a valid RiskDecision
    decision = evaluate_intent(intent)
    
    assert isinstance(decision, RiskDecision)
    assert decision.intent_id is not None
    assert isinstance(decision.approved, bool)
    assert decision.evaluated_portfolio_risk >= 0.0
    assert decision.timestamp is not None
    
    # If rejected, should have a rejection_reason
    if not decision.approved:
        assert decision.rejection_reason is not None
    else:
        assert decision.rejection_reason is None
