"""Unit tests for budget/risk limits."""

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from backend.risk.limits import (
    MAX_BUDGET,
    MAX_TRADE_SIZE,
    MIN_TRADE_SIZE,
    DAILY_LOSS_LIMIT,
    calculate_intent_value,
    check_budget_limit,
    check_daily_loss_limit,
)
from backend.risk.evaluator import TradeIntent


class TestCalculateIntentValue:
    """Tests for calculate_intent_value function."""
    
    def test_calculate_intent_value_basic(self):
        """Test basic intent value calculation."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=10.0,  # 10% of equity
            metadata={}
        )
        
        current_equity = Decimal("100.0")
        value = calculate_intent_value(intent, current_equity)
        
        assert value == 10.0  # 10% of $100 = $10
    
    def test_calculate_intent_value_larger_equity(self):
        """Test intent value with larger equity."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=25.0,  # 25% of equity
            metadata={}
        )
        
        current_equity = Decimal("1000.0")
        value = calculate_intent_value(intent, current_equity)
        
        assert value == 250.0  # 25% of $1000 = $250
    
    def test_calculate_intent_value_zero_equity(self):
        """Test intent value with zero equity returns 0."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=10.0,
            metadata={}
        )
        
        value = calculate_intent_value(intent, Decimal("0"))
        assert value == 0.0


class TestCheckBudgetLimit:
    """Tests for check_budget_limit function."""
    
    def test_trade_below_minimum_rejected(self):
        """Trade below MIN_TRADE_SIZE should be rejected."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=0.5,  # 0.5% of $100 = $0.50
            metadata={}
        )
        
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=0.0, 
            current_equity=Decimal("100.0")
        )
        
        assert approved is False
        assert reason == "below_minimum_trade_size"
    
    def test_trade_exceeds_max_size_rejected(self):
        """Trade exceeding MAX_TRADE_SIZE should be rejected."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=30.0,  # 30% of $100 = $30 > $25
            metadata={}
        )
        
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=0.0, 
            current_equity=Decimal("100.0")
        )
        
        assert approved is False
        assert reason == "exceeds_max_trade_size"
    
    def test_trade_exceeds_budget_rejected(self):
        """Trade that would exceed MAX_BUDGET should be rejected."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=20.0,  # 20% of $100 = $20
            metadata={}
        )
        
        # Current exposure is $85, adding $20 would exceed $100 budget
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=85.0, 
            current_equity=Decimal("100.0")
        )
        
        assert approved is False
        assert reason == "exceeds_budget_limit"
    
    def test_valid_trade_approved(self):
        """Valid trade within all limits should be approved."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=10.0,  # 10% of $100 = $10
            metadata={}
        )
        
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=50.0,  # $50 + $10 = $60 < $100
            current_equity=Decimal("100.0")
        )
        
        assert approved is True
        assert reason == ""
    
    def test_trade_at_exact_max_size_approved(self):
        """Trade at exactly MAX_TRADE_SIZE should be approved."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=25.0,  # 25% of $100 = $25 (exactly MAX_TRADE_SIZE)
            metadata={}
        )
        
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=0.0,
            current_equity=Decimal("100.0")
        )
        
        assert approved is True
        assert reason == ""
    
    def test_trade_at_exact_min_size_approved(self):
        """Trade at exactly MIN_TRADE_SIZE should be approved."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=1.0,  # 1% of $100 = $1 (exactly MIN_TRADE_SIZE)
            metadata={}
        )
        
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=0.0,
            current_equity=Decimal("100.0")
        )
        
        assert approved is True
        assert reason == ""
    
    def test_trade_at_exact_budget_limit_approved(self):
        """Trade that exactly reaches MAX_BUDGET should be approved."""
        intent = TradeIntent(
            strategy_id="test-strategy",
            symbol="ETH/USD",
            side="buy",
            intent_type="enter",
            notional_risk_pct=25.0,  # 25% of $100 = $25
            metadata={}
        )
        
        # $75 + $25 = $100 exactly
        approved, reason = check_budget_limit(
            intent, 
            current_exposure=75.0,
            current_equity=Decimal("100.0")
        )
        
        assert approved is True
        assert reason == ""


class TestCheckDailyLossLimit:
    """Tests for check_daily_loss_limit function."""
    
    def test_no_loss_no_halt(self):
        """Positive PnL should not trigger halt."""
        should_halt = check_daily_loss_limit(daily_pnl=5.0)
        assert should_halt is False
    
    def test_small_loss_no_halt(self):
        """Loss below limit should not trigger halt."""
        should_halt = check_daily_loss_limit(daily_pnl=-5.0)
        assert should_halt is False
    
    def test_loss_at_limit_triggers_halt(self):
        """Loss exactly at limit should trigger halt."""
        should_halt = check_daily_loss_limit(daily_pnl=-10.0)  # DAILY_LOSS_LIMIT default
        assert should_halt is True
    
    def test_loss_exceeds_limit_triggers_halt(self):
        """Loss exceeding limit should trigger halt."""
        should_halt = check_daily_loss_limit(daily_pnl=-15.0)
        assert should_halt is True
    
    def test_zero_pnl_no_halt(self):
        """Zero PnL should not trigger halt."""
        should_halt = check_daily_loss_limit(daily_pnl=0.0)
        assert should_halt is False


class TestDefaultLimits:
    """Tests for default limit values."""
    
    def test_default_max_budget(self):
        """MAX_BUDGET should default to 100.0."""
        assert MAX_BUDGET == 100.0
    
    def test_default_max_trade_size(self):
        """MAX_TRADE_SIZE should default to 25.0."""
        assert MAX_TRADE_SIZE == 25.0
    
    def test_default_min_trade_size(self):
        """MIN_TRADE_SIZE should default to 1.0."""
        assert MIN_TRADE_SIZE == 1.0
    
    def test_default_daily_loss_limit(self):
        """DAILY_LOSS_LIMIT should default to 10.0."""
        assert DAILY_LOSS_LIMIT == 10.0
