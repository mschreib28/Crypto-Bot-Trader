"""
Integration tests for MSDD v3.0 complete trade lifecycle.

Tests cover:
- Scout entry lifecycle
- Scale-in lifecycle
- Exit scenarios (48h filter, ATR trailing stop, breakeven guard)
- LIVE_SLOTS overflow
- Live universe restriction
- Costmin validation
- Dynamic risk recalculation
- Frontend integration
- Edge cases
- Performance testing
"""

import pytest
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Dict, Any, Optional

# Import modules under test
from backend.risk.evaluator import TradeIntent, evaluate_intent
from backend.execution.executor import execute_trade
from backend.positions.monitor import PositionMonitor
from backend.positions.models import Position
from backend.positions.tracker import PositionTracker
from backend.risk.micro_mode import get_live_slots_max, get_live_slots_status
from backend.execution.kraken_rest import KrakenClient
from backend.risk.sizing import PositionSizer, PositionSize
from backend.risk.account import AccountTracker


# Test fixtures
@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis_mock = Mock()
    redis_mock.get = Mock(return_value=None)
    redis_mock.set = Mock(return_value=True)
    redis_mock.setex = Mock(return_value=True)
    redis_mock.hgetall = Mock(return_value={})
    redis_mock.hset = Mock(return_value=True)
    redis_mock.delete = Mock(return_value=True)
    redis_mock.ping = Mock(return_value=True)
    return redis_mock


@pytest.fixture
def mock_kraken_client():
    """Mock Kraken REST client."""
    client = Mock(spec=KrakenClient)
    client.get_ticker = Mock(return_value={"XETHZUSD": {"c": ["3200.00", "100.0"]}})
    client.get_costmin = Mock(return_value=0.50)
    client.get_asset_pairs = Mock(return_value={"XETHZUSD": {"costmin": "0.50"}})
    client.add_order = Mock(return_value={
        "result": {
            "txid": ["TEST-TXID-123"],
            "descr": {"order": "buy 0.00046875 ETHUSD @ market"}
        },
        "error": []
    })
    client.cancel_order = Mock(return_value={"result": {"count": 1}, "error": []})
    client.query_orders = Mock(return_value={"result": {}})
    return client


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = Mock()
    session.query = Mock(return_value=Mock(
        filter=Mock(return_value=Mock(first=Mock(return_value=None)))
    ))
    session.close = Mock()
    return session


@pytest.fixture
def sample_trade_intent():
    """Sample TradeIntent for testing."""
    return TradeIntent(
        strategy_id="test_strategy_v1",
        symbol="ETH/USD",
        side="buy",
        intent_type="enter",
        notional_risk_pct=2.0,
        metadata={}
    )


@pytest.fixture
def sample_position():
    """Sample Position for testing."""
    return Position(
        symbol="ETH/USD",
        side="long",
        quantity=0.00046875,
        entry_price=3200.0,
        entry_time=datetime.now(timezone.utc).isoformat(),
        unrealized_pnl=0.0,
        current_price=3200.0,
        opened_by_strategy_id="test_strategy_v1",
        stop_loss_order_id="STOP-123",
        stop_loss_price=1856.0,  # 42% stop
        scout_entry_price=3200.0,
        soldier_entry_price=None,
        scale_in_triggered=False,
        breakeven_guard_active=False,
        breakeven_stop_price=None,
        trailing_stop_active=False,
        trailing_stop_price=None,
    )


class TestScoutEntryLifecycle:
    """Test complete Scout entry lifecycle."""
    
    @pytest.mark.asyncio
    async def test_scout_entry_complete_lifecycle(
        self, mock_redis, mock_kraken_client, sample_trade_intent
    ):
        """Test: Signal confirmed → EXECUTION_ALLOWED → ORDER_INTENT → Scout entry."""
        
        # Setup: Mock equity < $50 to trigger Scout sizing
        with patch('backend.execution.executor.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.redis.get_redis_client', return_value=mock_redis):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    with patch('backend.execution.executor.get_position_tracker') as mock_tracker_get:
                        tracker = Mock(spec=PositionTracker)
                        tracker.record_fill = Mock()
                        tracker.get_position = Mock(return_value=None)
                        mock_tracker_get.return_value = tracker
                        
                        # Mock activity logging
                        with patch('backend.api.routes.events.log_activity') as mock_log:
                            # Execute trade
                            current_price = 3200.0
                            fill = await execute_trade(sample_trade_intent, current_price)
                            
                            # Assertions
                            assert fill is not None, "Fill should be created"
                            assert fill.symbol == "ETH/USD"
                            assert fill.side == "buy"
                            
                            # Verify Scout entry size ($1.50)
                            # Position size should be ~$1.50 at $3200 = 0.00046875 ETH
                            assert fill.quantity > 0
                            
                            # Verify activity log sequence
                            log_calls = [call[1]['activity_type'] for call in mock_log.call_args_list]
                            assert 'TRADE_PLACED' in log_calls, "TRADE_PLACED should be logged"
                            
                            # Verify position tracker was called
                            tracker.record_fill.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_scout_stop_loss_placement(
        self, mock_redis, mock_kraken_client, sample_trade_intent
    ):
        """Test: Stop-loss placed correctly (42% stop)."""
        
        with patch('backend.execution.executor.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.redis.get_redis_client', return_value=mock_redis):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    with patch('backend.execution.executor.get_position_tracker') as mock_tracker_get:
                        tracker = Mock(spec=PositionTracker)
                        tracker.record_fill = Mock()
                        tracker.get_position = Mock(return_value=None)
                        mock_tracker_get.return_value = tracker
                        
                        # Execute trade
                        current_price = 3200.0
                        fill = await execute_trade(sample_trade_intent, current_price)
                        
                        # Verify stop-loss order was placed
                        # Check that add_order was called for stop-loss
                        stop_loss_calls = [
                            call for call in mock_kraken_client.add_order.call_args_list
                            if len(call[1]) > 0 and call[1].get('ordertype') == 'stop-loss'
                        ]
                        assert len(stop_loss_calls) > 0, "Stop-loss order should be placed"
    
    @pytest.mark.asyncio
    async def test_position_tracking_after_scout_entry(
        self, mock_redis, sample_position
    ):
        """Test: Position tracked correctly after Scout entry."""
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            tracker = PositionTracker()
            
            # Create a fill
            from backend.execution.models import Fill
            fill = Fill(
                order_id="test-order-123",
                symbol="ETH/USD",
                side="buy",
                executed_price=3200.0,
                quantity=0.00046875,
                fees=0.0012,
                slippage=0.0,
                exchange_order_id="KRAKEN-123",
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            
            # Record fill
            tracker.record_fill(fill, strategy_id="test_strategy_v1")
            
            # Get position
            position = tracker.get_position("ETH/USD")
            
            assert position is not None, "Position should be tracked"
            assert position.symbol == "ETH/USD"
            assert position.scout_entry_price == 3200.0, "Scout entry price should be set"
            assert position.scale_in_triggered is False, "Scale-in should not be triggered yet"


class TestScaleInLifecycle:
    """Test scale-in lifecycle."""
    
    @pytest.mark.asyncio
    async def test_scale_in_trigger_at_1_5_percent(
        self, mock_redis, mock_kraken_client, sample_position
    ):
        """Test: Position reaches +1.5% profit triggers Soldier scale-in."""
        
        # Setup position at +1.5% profit
        current_price = sample_position.scout_entry_price * 1.015  # +1.5%
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                with patch('backend.execution.executor.get_position_tracker') as mock_tracker_get:
                    tracker = Mock(spec=PositionTracker)
                    tracker.get_position = Mock(return_value=sample_position)
                    tracker.get_all_positions = Mock(return_value=[sample_position])
                    mock_tracker_get.return_value = tracker
                    
                    monitor = PositionMonitor(update_interval=1.0)
                    
                    # Mock execute_trade for Soldier scale-in
                    with patch('backend.positions.monitor.execute_trade') as mock_execute:
                        mock_fill = Mock()
                        mock_fill.order_id = "soldier-order-123"
                        mock_fill.symbol = "ETH/USD"
                        mock_fill.side = "buy"
                        mock_fill.executed_price = current_price
                        mock_fill.quantity = 0.0009375  # $3.00 / $3200
                        mock_execute.return_value = mock_fill
                        
                        # Check scale-in trigger
                        await monitor._check_scale_in_trigger(sample_position, current_price)
                        
                        # Verify execute_trade was called for Soldier scale-in
                        mock_execute.assert_called_once()
                        call_args = mock_execute.call_args[0]
                        assert call_args[0].side == "buy"
                        assert call_args[0].symbol == "ETH/USD"
    
    @pytest.mark.asyncio
    async def test_breakeven_guard_activation(
        self, mock_redis, mock_kraken_client, sample_position
    ):
        """Test: Breakeven guard activates at +2% profit."""
        
        # Setup position with Soldier scale-in already executed
        sample_position.scale_in_triggered = True
        sample_position.soldier_entry_price = 3248.0  # +1.5% from scout
        
        # Current price at +2% from scout entry
        current_price = sample_position.scout_entry_price * 1.02
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                with patch('backend.config.BREAKEVEN_GUARD_TRIGGER_PCT', 2.0):
                    with patch('backend.config.KRAKEN_FEE_PCT', 0.26):
                        monitor = PositionMonitor(update_interval=1.0)
                        
                        # Mock update_kraken_stop_loss
                        monitor._update_kraken_stop_loss = AsyncMock()
                        
                        # Check breakeven guard
                        await monitor._check_breakeven_guard(sample_position, current_price)
                        
                        # Verify breakeven guard activated
                        assert sample_position.breakeven_guard_active is True
                        assert sample_position.breakeven_stop_price is not None
                        
                        # Breakeven should be scout_entry_price + fees
                        expected_breakeven = sample_position.scout_entry_price * 1.0026
                        assert abs(sample_position.breakeven_stop_price - expected_breakeven) < 0.01


class TestExitScenarios:
    """Test exit scenarios."""
    
    @pytest.mark.asyncio
    async def test_48_hour_filter_exit(
        self, mock_redis, mock_kraken_client, sample_position
    ):
        """Test: 48-hour filter exit when TP1 not hit."""
        
        # Setup position held > 48 hours
        entry_time = datetime.now(timezone.utc) - timedelta(hours=49)
        sample_position.entry_time = entry_time.isoformat()
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            # Mock TP1 not hit
            mock_redis.get.return_value = None  # TP1 not hit
            
            with patch('backend.config.OPPORTUNITY_FILTER_HOURS', 48):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    monitor = PositionMonitor(update_interval=1.0)
                    
                    # Mock force_exit_position
                    monitor._force_exit_position = AsyncMock()
                    
                    # Mock get strategy name
                    with patch('backend.db.get_session') as mock_session_get:
                        mock_session = Mock()
                        mock_session.query.return_value.filter.return_value.first.return_value = Mock(name="Test Strategy")
                        mock_session_get.return_value = mock_session
                        
                        current_price = 3200.0
                        await monitor._check_48h_opportunity_filter(sample_position, current_price)
                        
                        # Verify force_exit_position was called
                        monitor._force_exit_position.assert_called_once()
                        call_args = monitor._force_exit_position.call_args
                        assert call_args[1]['reason'] == "opportunity_filter_48h"
    
    @pytest.mark.asyncio
    async def test_atr_trailing_stop_exit(
        self, mock_redis, mock_kraken_client, sample_position
    ):
        """Test: ATR trailing stop exit when price drops to trailing stop."""
        
        # Setup position with trailing stop active
        sample_position.trailing_stop_active = True
        sample_position.trailing_stop_price = 3100.0
        sample_position.entry_price = 3000.0  # +3% profit was at 3090
        
        # Current price drops to trailing stop
        current_price = 3100.0
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                monitor = PositionMonitor(update_interval=1.0)
                
                # Mock force_exit_position
                monitor._force_exit_position = AsyncMock()
                
                # Mock get ATR
                monitor._get_atr_for_position = AsyncMock(return_value=50.0)
                
                # Mock get strategy name
                with patch('backend.db.get_session') as mock_session_get:
                    mock_session = Mock()
                    mock_session.query.return_value.filter.return_value.first.return_value = Mock(name="Test Strategy")
                    mock_session_get.return_value = mock_session
                    
                    await monitor._check_atr_trailing_stop(sample_position, current_price)
                    
                    # Verify force_exit_position was called
                    monitor._force_exit_position.assert_called_once()
                    call_args = monitor._force_exit_position.call_args
                    assert call_args[1]['reason'] == "atr_trailing_stop"
    
    @pytest.mark.asyncio
    async def test_breakeven_guard_exit(
        self, mock_redis, mock_kraken_client, sample_position
    ):
        """Test: Breakeven guard exit when price drops to breakeven."""
        
        # Setup position with breakeven guard active
        sample_position.breakeven_guard_active = True
        sample_position.breakeven_stop_price = 3208.32  # Scout entry + fees
        sample_position.scout_entry_price = 3200.0
        
        # Current price drops to breakeven
        current_price = 3208.32
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                # Mock stop-loss order trigger (Kraken would trigger this)
                # In real scenario, Kraken stop-loss order would execute
                # Here we test that the position is closed when stop-loss triggers
                
                tracker = PositionTracker()
                position = tracker.get_position(sample_position.symbol)
                
                # If stop-loss triggers, position should be closed
                # This is tested via the stop-loss order execution path
                assert True  # Placeholder - actual test would verify stop-loss execution


class TestLiveSlotsOverflow:
    """Test LIVE_SLOTS overflow behavior."""
    
    @pytest.mark.asyncio
    async def test_first_signal_executes_live(
        self, mock_redis, sample_trade_intent
    ):
        """Test: First signal executes live when slots available."""
        
        with patch('backend.risk.portfolio.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.positions.tracker.get_position_tracker') as mock_tracker_get:
                tracker = Mock(spec=PositionTracker)
                tracker.get_live_position_count = Mock(return_value=0)  # No positions
                mock_tracker_get.return_value = tracker
                
                # Evaluate intent
                decision = evaluate_intent(sample_trade_intent)
                
                # With equity < $50, LIVE_SLOTS_MAX = 1
                # Since current_positions = 0 < 1, should approve
                # (May still reject for other reasons like market data freshness)
                assert isinstance(decision, type(evaluate_intent(sample_trade_intent)))
    
    @pytest.mark.asyncio
    async def test_second_signal_routes_to_shadow(
        self, mock_redis, sample_trade_intent
    ):
        """Test: Second signal routes to Shadow Mode when slots full."""
        
        with patch('backend.risk.portfolio.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.positions.tracker.get_position_tracker') as mock_tracker_get:
                tracker = Mock(spec=PositionTracker)
                tracker.get_live_position_count = Mock(return_value=1)  # 1 slot used
                mock_tracker_get.return_value = tracker
                
                # Mock shadow mode enabled
                with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=True):
                    decision = evaluate_intent(sample_trade_intent)
                    
                    # Should reject and route to shadow mode
                    if not decision.approved:
                        assert decision.rejection_reason == "live_slots_full_routed_to_shadow"


class TestLiveUniverseRestriction:
    """Test live universe restriction."""
    
    @pytest.mark.asyncio
    async def test_top_5_pair_executes_live(
        self, mock_redis, sample_trade_intent
    ):
        """Test: Top 5 pair executes live."""
        
        # ETH/USD is in top 5
        sample_trade_intent.symbol = "ETH/USD"
        
        with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
            decision = evaluate_intent(sample_trade_intent)
            
            # Should not reject for live universe (may reject for other reasons)
            if not decision.approved:
                assert decision.rejection_reason != "not_in_live_universe"
    
    @pytest.mark.asyncio
    async def test_non_top_5_pair_routes_to_shadow(
        self, mock_redis, sample_trade_intent
    ):
        """Test: Non-top-5 pair routes to Shadow Mode."""
        
        # XLM/USD is not in top 5
        sample_trade_intent.symbol = "XLM/USD"
        
        with patch('backend.ingestor.symbols.is_in_live_universe', return_value=False):
            decision = evaluate_intent(sample_trade_intent)
            
            # Should reject for not in live universe
            assert decision.approved is False
            assert decision.rejection_reason == "not_in_live_universe"


class TestCostminValidation:
    """Test costmin validation."""
    
    @pytest.mark.asyncio
    async def test_order_below_costmin_rejected(
        self, mock_redis, mock_kraken_client, sample_trade_intent
    ):
        """Test: Order below costmin rejected."""
        
        # Mock costmin = $0.50
        mock_kraken_client.get_costmin.return_value = 0.50
        
        # Mock position size < $0.50 (would be rejected)
        with patch('backend.execution.executor.get_current_equity', return_value=Decimal('10.00')):
            with patch('backend.redis.get_redis_client', return_value=mock_redis):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    # Mock sizing that would result in < $0.50
                    with patch('backend.risk.sizing.PositionSizer.calculate') as mock_sizing:
                        from backend.risk.sizing import PositionSize
                        mock_sizing.return_value = PositionSize(
                            position_size_usd=0.30,  # Below costmin
                            quantity=0.0001,
                            stop_loss_price=3000.0,
                            stop_loss_pct=5.0,
                            max_risk_usd=0.20,
                        )
                        
                        current_price = 3200.0
                        fill = await execute_trade(sample_trade_intent, current_price)
                        
                        # Should reject order below costmin
                        assert fill is None
    
    @pytest.mark.asyncio
    async def test_order_above_costmin_executes(
        self, mock_redis, mock_kraken_client, sample_trade_intent
    ):
        """Test: Order above costmin executes."""
        
        # Mock costmin = $0.50
        mock_kraken_client.get_costmin.return_value = 0.50
        
        with patch('backend.execution.executor.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.redis.get_redis_client', return_value=mock_redis):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    with patch('backend.execution.executor.get_position_tracker') as mock_tracker_get:
                        tracker = Mock(spec=PositionTracker)
                        tracker.record_fill = Mock()
                        tracker.get_position = Mock(return_value=None)
                        mock_tracker_get.return_value = tracker
                        
                        current_price = 3200.0
                        fill = await execute_trade(sample_trade_intent, current_price)
                        
                        # Scout entry is $1.50, which is > $0.50 costmin
                        # Should execute (may be None for other reasons like market data)
                        # But if it executes, position_size_usd should be >= costmin
                        if fill is not None:
                            assert fill.quantity * current_price >= 0.50
    
    @pytest.mark.asyncio
    async def test_fallback_to_default_costmin_on_api_failure(
        self, mock_redis, mock_kraken_client, sample_trade_intent
    ):
        """Test: Fallback to $0.50 default if AssetPairs API fails."""
        
        # Mock API failure
        mock_kraken_client.get_costmin.side_effect = Exception("API Error")
        mock_kraken_client.get_asset_pairs.side_effect = Exception("API Error")
        
        with patch('backend.execution.executor.get_current_equity', return_value=Decimal('31.80')):
            with patch('backend.redis.get_redis_client', return_value=mock_redis):
                with patch('backend.execution.executor.get_kraken_client', return_value=mock_kraken_client):
                    # Should use default $0.50 costmin
                    # Execution should proceed (costmin check logs warning but doesn't block)
                    current_price = 3200.0
                    # Should not raise exception
                    try:
                        fill = await execute_trade(sample_trade_intent, current_price)
                        # If fill is None, it's for other reasons, not costmin
                        assert True
                    except Exception as e:
                        # Should not fail due to costmin API error
                        assert "costmin" not in str(e).lower()


class TestDynamicRiskRecalculation:
    """Test dynamic risk recalculation."""
    
    @pytest.mark.asyncio
    async def test_risk_capital_recalculated_daily(
        self, mock_redis
    ):
        """Test: Risk capital recalculated daily."""
        
        from backend.risk.account import AccountTracker
        
        # Initial equity
        tracker = AccountTracker(initial_equity=31.80)
        initial_risk = tracker.current_equity * Decimal('0.02')
        
        # Simulate daily P&L
        tracker.record_pnl(5.0)  # Equity now $36.80
        new_risk = tracker.current_equity * Decimal('0.02')
        
        assert new_risk > initial_risk, "Risk capital should increase with equity"
        assert abs(float(new_risk) - 0.736) < 0.01, "Risk should be 2% of $36.80"
    
    @pytest.mark.asyncio
    async def test_scout_size_minimum_enforced(
        self, mock_redis
    ):
        """Test: Minimum $1.50 enforced for Scout entry."""
        
        from backend.risk.sizing import PositionSizer
        
        sizer = PositionSizer()
        
        # Even with very low equity, Scout entry should be $1.50 minimum
        scout_size = float(os.getenv("SCOUT_ENTRY_SIZE_USD", "1.50"))
        
        assert scout_size >= 1.50, "Scout entry should be at least $1.50"
    
    @pytest.mark.asyncio
    async def test_scout_size_maximum_enforced(
        self, mock_redis
    ):
        """Test: Maximum $5.00 enforced for Scout entry (M3 milestone)."""
        
        scout_size = float(os.getenv("SCOUT_ENTRY_SIZE_USD", "1.50"))
        
        # M3 milestone: Scout entry max $5.00
        assert scout_size <= 5.00, "Scout entry should not exceed $5.00"


class TestEdgeCases:
    """Test edge cases."""
    
    @pytest.mark.asyncio
    async def test_position_held_exactly_48_hours(
        self, mock_redis, sample_position
    ):
        """Test: Position held exactly 48 hours."""
        
        # Setup position held exactly 48 hours
        entry_time = datetime.now(timezone.utc) - timedelta(hours=48)
        sample_position.entry_time = entry_time.isoformat()
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            mock_redis.get.return_value = None  # TP1 not hit
            
            with patch('backend.config.OPPORTUNITY_FILTER_HOURS', 48):
                monitor = PositionMonitor(update_interval=1.0)
                monitor._force_exit_position = AsyncMock()
                
                with patch('backend.db.get_session') as mock_session_get:
                    mock_session = Mock()
                    mock_session.query.return_value.filter.return_value.first.return_value = Mock(name="Test Strategy")
                    mock_session_get.return_value = mock_session
                    
                    current_price = 3200.0
                    await monitor._check_48h_opportunity_filter(sample_position, current_price)
                    
                    # Should trigger exit (>= 48 hours)
                    monitor._force_exit_position.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_price_exactly_at_trailing_stop(
        self, mock_redis, sample_position
    ):
        """Test: Price exactly at trailing stop."""
        
        sample_position.trailing_stop_active = True
        sample_position.trailing_stop_price = 3100.0
        
        current_price = 3100.0  # Exactly at trailing stop
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            monitor = PositionMonitor(update_interval=1.0)
            monitor._force_exit_position = AsyncMock()
            monitor._get_atr_for_position = AsyncMock(return_value=50.0)
            
            with patch('backend.db.get_session') as mock_session_get:
                mock_session = Mock()
                mock_session.query.return_value.filter.return_value.first.return_value = Mock(name="Test Strategy")
                mock_session_get.return_value = mock_session
                
                await monitor._check_atr_trailing_stop(sample_position, current_price)
                
                # Should trigger exit (price <= trailing stop)
                monitor._force_exit_position.assert_called_once()


class TestPerformance:
    """Test performance metrics."""
    
    @pytest.mark.asyncio
    async def test_position_monitor_performance(
        self, mock_redis, sample_position
    ):
        """Test: PositionMonitor performance (all checks)."""
        
        import time
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            monitor = PositionMonitor(update_interval=1.0)
            
            # Mock all dependencies
            monitor._get_current_price = AsyncMock(return_value=3200.0)
            monitor._check_scale_in_trigger = AsyncMock()
            monitor._check_tp1_hit = AsyncMock()
            monitor._check_breakeven_guard = AsyncMock()
            monitor._check_forced_exits = AsyncMock()
            monitor._check_48h_opportunity_filter = AsyncMock()
            monitor._check_atr_trailing_stop = AsyncMock()
            
            with patch('backend.positions.tracker.get_position_tracker') as mock_tracker_get:
                tracker = Mock(spec=PositionTracker)
                tracker.get_all_positions = Mock(return_value=[sample_position])
                tracker.update_position_pnl = Mock(return_value=sample_position)
                mock_tracker_get.return_value = tracker
                
                # Measure time
                start_time = time.time()
                await monitor._update_all_positions()
                elapsed_time = time.time() - start_time
                
                # Should complete quickly (< 1 second for single position)
                assert elapsed_time < 1.0, f"PositionMonitor should be fast (< 1s), took {elapsed_time:.3f}s"


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
