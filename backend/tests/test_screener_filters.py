"""Unit tests for screener global filters.

Tests verify that filters are applied correctly and skip messages appear in logs.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock

from backend.screener.models import SignalResult


class TestShadowModeExecutionGate:
    """Test that shadow mode allows auto-execution when trading_enabled is False."""

    @pytest.fixture
    def screener_service(self):
        with patch('backend.redis.get_redis_client'):
            from backend.screener.service import ScreenerService
            return ScreenerService(scan_interval_seconds=60.0, bars_to_fetch=250, interval="5m")

    @pytest.fixture
    def mock_strategy(self):
        strategy = Mock()
        strategy.strategy_id = "test-strategy-shadow"
        return strategy

    @pytest.fixture
    def symbols_bars(self):
        return {
            "BTC/USD": [
                {
                    "timestamp": "2025-02-23T12:00:00Z",
                    "open": 50000,
                    "high": 50100,
                    "low": 49900,
                    "close": 50050,
                    "volume": 1000,
                }
            ]
        }

    @pytest.fixture
    def actionable_buy_signal(self, mock_strategy):
        return SignalResult(
            symbol="BTC/USD",
            signal_type="BUY",
            confidence=85.0,
            strategy_id=mock_strategy.strategy_id,
            indicators={"current_price": 50050.0, "bar_timestamp": "2025-02-23T12:00:00Z"},
            timestamp="2025-02-23T12:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_shadow_mode_processes_signals_when_trading_disabled(
        self, screener_service, mock_strategy, symbols_bars, actionable_buy_signal
    ):
        """When shadow mode is ON and trading is OFF, _process_auto_execution should be called."""
        with patch('backend.redis.get_redis_client') as mock_redis_cls:
            mock_redis = Mock()
            mock_redis.get = Mock(return_value=None)
            mock_redis.set = Mock(return_value=True)
            mock_redis.setex = Mock(return_value=True)
            mock_redis.lpush = Mock(return_value=True)
            mock_redis.ltrim = Mock(return_value=True)
            mock_redis.exists = Mock(return_value=False)
            mock_redis_cls.return_value = mock_redis

            with patch('backend.screener.service.get_trading_enabled', return_value=False):
                with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=True):
                    with patch(
                        'backend.screener.service.scan_with_strategy',
                        new_callable=AsyncMock,
                        return_value=[actionable_buy_signal],
                    ):
                        with patch.object(
                            screener_service,
                            '_process_auto_execution',
                            new_callable=AsyncMock,
                        ) as mock_process:
                            with patch.object(screener_service, '_should_evaluate', return_value=True):
                                with patch.object(screener_service, '_record_evaluation'):
                                    await screener_service._run_strategy_scan(
                                        mock_strategy,
                                        symbols_bars,
                                        interval="5m",
                                        confidence_buy=70.0,
                                        confidence_sell=70.0,
                                    )

                            # Shadow mode + trading off: execution allowed, so _process_auto_execution is called
                            assert mock_process.call_count == 1
                            call_args = mock_process.call_args
                            assert call_args[0][1] is True  # trading_enabled passed as True (shadow allows execution)
                            assert call_args[0][0].symbol == "BTC/USD"
                            assert call_args[0][0].signal_type == "BUY"

    @pytest.mark.asyncio
    async def test_both_off_skips_auto_execution(
        self, screener_service, mock_strategy, symbols_bars, actionable_buy_signal
    ):
        """When both trading and shadow are OFF, _process_auto_execution should not be called."""
        with patch('backend.redis.get_redis_client') as mock_redis_cls:
            mock_redis = Mock()
            mock_redis.get = Mock(return_value=None)
            mock_redis.set = Mock(return_value=True)
            mock_redis.setex = Mock(return_value=True)
            mock_redis.lpush = Mock(return_value=True)
            mock_redis.ltrim = Mock(return_value=True)
            mock_redis.exists = Mock(return_value=False)
            mock_redis_cls.return_value = mock_redis

            with patch('backend.screener.service.get_trading_enabled', return_value=False):
                with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                    with patch(
                        'backend.screener.service.scan_with_strategy',
                        new_callable=AsyncMock,
                        return_value=[actionable_buy_signal],
                    ):
                        with patch.object(
                            screener_service,
                            '_process_auto_execution',
                            new_callable=AsyncMock,
                        ) as mock_process:
                            with patch.object(screener_service, '_should_evaluate', return_value=True):
                                with patch.object(screener_service, '_record_evaluation'):
                                    await screener_service._run_strategy_scan(
                                        mock_strategy,
                                        symbols_bars,
                                        interval="5m",
                                        confidence_buy=70.0,
                                        confidence_sell=70.0,
                                    )

                            # Both off: _process_auto_execution is still called but with trading_enabled=False
                            # (it logs and returns early inside _process_auto_execution)
                            assert mock_process.call_count == 1
                            call_args = mock_process.call_args
                            assert call_args[0][1] is False  # trading_enabled passed as False


class TestScreenerGlobalFilters:
    """Test global filters: whitelist, liquidity, and spread."""
    
    @pytest.fixture
    def screener_service(self):
        """Create a ScreenerService instance for testing."""
        # Import here to avoid import-time redis dependency issues
        with patch('backend.redis.get_redis_client'):
            from backend.screener.service import ScreenerService
            return ScreenerService(scan_interval_seconds=60.0, bars_to_fetch=250, interval="5m")
    
    @pytest.fixture
    def mock_redis(self):
        """Mock Redis client."""
        redis_mock = Mock()
        redis_mock.get = Mock(return_value=None)
        redis_mock.set = Mock(return_value=True)
        redis_mock.setex = Mock(return_value=True)
        redis_mock.lpush = Mock(return_value=True)
        redis_mock.ltrim = Mock(return_value=True)
        redis_mock.exists = Mock(return_value=False)
        redis_mock.xrevrange = Mock(return_value=[])
        return redis_mock
    
    @pytest.mark.asyncio
    async def test_whitelist_filter_skips_non_whitelisted_symbols(
        self, screener_service, mock_redis
    ):
        """Test that whitelist filter skips non-whitelisted symbols in shadow mode."""
        symbols = ["BTC/USD", "ETH/USD", "XPL/USD", "ZRO/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=True):
                with patch('backend.ingestor.config.get_enforce_whitelist_in_shadow', return_value=True):
                    with patch('backend.ingestor.symbols.is_in_live_universe') as mock_is_whitelisted:
                        # BTC/USD and ETH/USD are whitelisted, XPL/USD and ZRO/USD are not
                        mock_is_whitelisted.side_effect = lambda s: s in ["BTC/USD", "ETH/USD"]
                        
                        with patch('backend.ingestor.symbols.get_symbol_volume', return_value=50000000.0):
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Verify filtering results
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "ETH/USD" in filtered
                                    assert "XPL/USD" not in filtered
                                    assert "ZRO/USD" not in filtered
                                    
                                    # Verify skip reasons
                                    assert len(skip_reasons) == 2
                                    assert "XPL/USD" in skip_reasons
                                    assert "ZRO/USD" in skip_reasons
                                    assert "not in whitelist" in skip_reasons["XPL/USD"]
                                    assert "not in whitelist" in skip_reasons["ZRO/USD"]
                                    
                                    # Filter skips are no longer logged to activity (backend logger only)
                                    assert mock_log_activity.call_count == 0
    
    @pytest.mark.asyncio
    async def test_whitelist_filter_not_applied_when_not_in_shadow_mode(
        self, screener_service, mock_redis
    ):
        """Test that whitelist filter is not applied when not in shadow mode."""
        symbols = ["BTC/USD", "XPL/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_enforce_whitelist_in_shadow', return_value=True):
                    with patch('backend.ingestor.symbols.is_in_live_universe') as mock_is_whitelisted:
                        # Even if XPL/USD is not whitelisted, it should pass when not in shadow mode
                        mock_is_whitelisted.return_value = False
                        
                        with patch('backend.ingestor.symbols.get_symbol_volume', return_value=50000000.0):
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Both symbols should pass (whitelist filter not applied)
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "XPL/USD" in filtered
                                    assert len(skip_reasons) == 0
                                    
                                    # No log_activity calls for whitelist filter
                                    whitelist_logs = [
                                        call for call in mock_log_activity.call_args_list
                                        if call[1]['details'].get('filter') == 'whitelist'
                                    ]
                                    assert len(whitelist_logs) == 0
    
    @pytest.mark.asyncio
    async def test_liquidity_filter_skips_low_volume_symbols(
        self, screener_service, mock_redis
    ):
        """Test that liquidity filter skips symbols with volume below threshold."""
        symbols = ["BTC/USD", "ETH/USD", "LOWVOL/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_min_24h_volume_usd', return_value=10000000.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume') as mock_get_volume:
                            # BTC/USD and ETH/USD have high volume, LOWVOL/USD has low volume
                            def volume_side_effect(symbol):
                                if symbol == "LOWVOL/USD":
                                    return 5000000.0  # Below $10M threshold
                                return 50000000.0  # Above threshold
                            
                            mock_get_volume.side_effect = volume_side_effect
                            
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Verify filtering results
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "ETH/USD" in filtered
                                    assert "LOWVOL/USD" not in filtered
                                    
                                    # Verify skip reasons
                                    assert len(skip_reasons) == 1
                                    assert "LOWVOL/USD" in skip_reasons
                                    assert "volume" in skip_reasons["LOWVOL/USD"].lower()
                                    
                                    # Filter skips are no longer logged to activity (backend logger only)
                                    assert mock_log_activity.call_count == 0
    
    @pytest.mark.asyncio
    async def test_liquidity_filter_graceful_degradation_missing_data(
        self, screener_service, mock_redis
    ):
        """Test that symbols without volume data are evaluated (graceful degradation)."""
        symbols = ["BTC/USD", "NODATA/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_min_24h_volume_usd', return_value=10000000.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume') as mock_get_volume:
                            # BTC/USD has volume data, NODATA/USD has None
                            def volume_side_effect(symbol):
                                if symbol == "NODATA/USD":
                                    return None  # Missing data
                                return 50000000.0
                            
                            mock_get_volume.side_effect = volume_side_effect
                            
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Both symbols should pass (missing data = graceful degradation)
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "NODATA/USD" in filtered
                                    assert len(skip_reasons) == 0
                                    
                                    # No log_activity calls for liquidity filter
                                    liquidity_logs = [
                                        call for call in mock_log_activity.call_args_list
                                        if call[1]['details'].get('filter') == 'liquidity'
                                    ]
                                    assert len(liquidity_logs) == 0
    
    @pytest.mark.asyncio
    async def test_spread_filter_skips_wide_spread_symbols(
        self, screener_service, mock_redis
    ):
        """Test that spread filter skips symbols with spread above threshold."""
        symbols = ["BTC/USD", "ETH/USD", "WIDESPREAD/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_max_spread_bps', return_value=15.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume', return_value=50000000.0):
                            with patch('backend.ingestor.symbols.get_symbol_spread') as mock_get_spread:
                                # BTC/USD and ETH/USD have tight spread, WIDESPREAD/USD has wide spread
                                def spread_side_effect(symbol):
                                    if symbol == "WIDESPREAD/USD":
                                        return 25.0  # Above 15 bps threshold
                                    return 10.0  # Below threshold
                                
                                mock_get_spread.side_effect = spread_side_effect
                                
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Verify filtering results
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "ETH/USD" in filtered
                                    assert "WIDESPREAD/USD" not in filtered
                                    
                                    # Verify skip reasons
                                    assert len(skip_reasons) == 1
                                    assert "WIDESPREAD/USD" in skip_reasons
                                    assert "spread" in skip_reasons["WIDESPREAD/USD"].lower()
                                    
                                    # Filter skips are no longer logged to activity (backend logger only)
                                    assert mock_log_activity.call_count == 0
    
    @pytest.mark.asyncio
    async def test_spread_filter_graceful_degradation_missing_data(
        self, screener_service, mock_redis
    ):
        """Test that symbols without spread data are evaluated (graceful degradation)."""
        symbols = ["BTC/USD", "NODATA/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_max_spread_bps', return_value=15.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume', return_value=50000000.0):
                            with patch('backend.ingestor.symbols.get_symbol_spread') as mock_get_spread:
                                # BTC/USD has spread data, NODATA/USD has None
                                def spread_side_effect(symbol):
                                    if symbol == "NODATA/USD":
                                        return None  # Missing data
                                    return 10.0
                                
                                mock_get_spread.side_effect = spread_side_effect
                                
                                with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # Both symbols should pass (missing data = graceful degradation)
                                    assert len(filtered) == 2
                                    assert "BTC/USD" in filtered
                                    assert "NODATA/USD" in filtered
                                    assert len(skip_reasons) == 0
                                    
                                    # No log_activity calls for spread filter
                                    spread_logs = [
                                        call for call in mock_log_activity.call_args_list
                                        if call[1]['details'].get('filter') == 'spread'
                                    ]
                                    assert len(spread_logs) == 0
    
    @pytest.mark.asyncio
    async def test_all_filters_applied_together(
        self, screener_service, mock_redis
    ):
        """Test that all filters are applied together in correct order (fail-fast)."""
        symbols = [
            "BTC/USD",      # Passes all filters
            "ETH/USD",      # Passes all filters
            "NONWHITE/USD", # Fails whitelist (should be skipped first)
            "LOWVOL/USD",   # Fails liquidity (should be skipped if whitelist passes)
            "WIDESPREAD/USD", # Fails spread (should be skipped if previous filters pass)
        ]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=True):
                with patch('backend.ingestor.config.get_enforce_whitelist_in_shadow', return_value=True):
                    with patch('backend.ingestor.config.get_min_24h_volume_usd', return_value=10000000.0):
                        with patch('backend.ingestor.config.get_max_spread_bps', return_value=15.0):
                            with patch('backend.ingestor.symbols.is_in_live_universe') as mock_is_whitelisted:
                                # Only BTC/USD and ETH/USD are whitelisted
                                mock_is_whitelisted.side_effect = lambda s: s in ["BTC/USD", "ETH/USD"]
                                
                                with patch('backend.ingestor.symbols.get_symbol_volume') as mock_get_volume:
                                    def volume_side_effect(symbol):
                                        if symbol == "LOWVOL/USD":
                                            return 5000000.0  # Below threshold
                                        return 50000000.0  # Above threshold
                                    
                                    mock_get_volume.side_effect = volume_side_effect
                                    
                                    with patch('backend.ingestor.symbols.get_symbol_spread') as mock_get_spread:
                                        def spread_side_effect(symbol):
                                            if symbol == "WIDESPREAD/USD":
                                                return 25.0  # Above threshold
                                            return 10.0  # Below threshold
                                        
                                        mock_get_spread.side_effect = spread_side_effect
                                        
                                        with patch('backend.api.routes.events.log_activity') as mock_log_activity:
                                            filtered, skip_reasons = await screener_service._apply_global_filters(
                                                symbols, strategy_id
                                            )
                                            
                                            # Only BTC/USD and ETH/USD should pass
                                            assert len(filtered) == 2
                                            assert "BTC/USD" in filtered
                                            assert "ETH/USD" in filtered
                                            
                                            # All other symbols should be skipped
                                            assert len(skip_reasons) == 3
                                            assert "NONWHITE/USD" in skip_reasons
                                            assert "LOWVOL/USD" in skip_reasons
                                            assert "WIDESPREAD/USD" in skip_reasons
                                            
                                            # Verify fail-fast: NONWHITE/USD should fail whitelist first
                                            assert "not in whitelist" in skip_reasons["NONWHITE/USD"]
                                            
                                            # LOWVOL/USD should fail liquidity (whitelist check skipped in non-shadow or passes)
                                            # Actually, since we're in shadow mode, LOWVOL/USD would need to pass whitelist first
                                            # But NONWHITE fails whitelist, so it's skipped before liquidity check
                                            
                                            # Filter skips are no longer logged to activity (backend logger only)
                                            assert mock_log_activity.call_count == 0
    
    @pytest.mark.asyncio
    async def test_filter_respects_env_var_overrides(
        self, screener_service, mock_redis
    ):
        """Test that filters respect environment variable overrides."""
        symbols = ["BTC/USD", "CUSTOMVOL/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                # Test custom volume threshold
                with patch('backend.ingestor.config.get_min_24h_volume_usd', return_value=50000000.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume') as mock_get_volume:
                            # BTC/USD has $60M (above custom $50M threshold)
                            # CUSTOMVOL/USD has $30M (below custom $50M threshold)
                            def volume_side_effect(symbol):
                                if symbol == "CUSTOMVOL/USD":
                                    return 30000000.0
                                return 60000000.0
                            
                            mock_get_volume.side_effect = volume_side_effect
                            
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity'):
                                    filtered, skip_reasons = await screener_service._apply_global_filters(
                                        symbols, strategy_id
                                    )
                                    
                                    # CUSTOMVOL/USD should be filtered out with custom threshold
                                    assert len(filtered) == 1
                                    assert "BTC/USD" in filtered
                                    assert "CUSTOMVOL/USD" not in filtered
                                    assert len(skip_reasons) == 1
                                    assert "CUSTOMVOL/USD" in skip_reasons
    
    @pytest.mark.asyncio
    async def test_filter_logs_summary_message(
        self, screener_service, mock_redis
    ):
        """Test that filter summary is logged correctly."""
        import logging
        
        symbols = ["BTC/USD", "SKIP1/USD", "SKIP2/USD"]
        strategy_id = "test-strategy-123"
        
        with patch('backend.redis.get_redis_client', return_value=mock_redis):
            with patch('backend.api.routes.trading.get_shadow_live_mode', return_value=False):
                with patch('backend.ingestor.config.get_min_24h_volume_usd', return_value=10000000.0):
                    with patch('backend.ingestor.symbols.is_in_live_universe', return_value=True):
                        with patch('backend.ingestor.symbols.get_symbol_volume') as mock_get_volume:
                            def volume_side_effect(symbol):
                                if symbol in ["SKIP1/USD", "SKIP2/USD"]:
                                    return 5000000.0  # Below threshold
                                return 50000000.0
                            
                            mock_get_volume.side_effect = volume_side_effect
                            
                            with patch('backend.ingestor.symbols.get_symbol_spread', return_value=10.0):
                                with patch('backend.api.routes.events.log_activity'):
                                    # Capture logger.info calls
                                    with patch('backend.screener.service.logger') as mock_logger:
                                        filtered, skip_reasons = await screener_service._apply_global_filters(
                                            symbols, strategy_id
                                        )
                                        
                                        # Verify summary log was called
                                        info_calls = [str(call) for call in mock_logger.info.call_args_list]
                                        
                                        # Should have summary log
                                        summary_logs = [
                                            call for call in info_calls
                                            if f"Strategy {strategy_id}" in str(call) and "Filtered" in str(call)
                                        ]
                                        assert len(summary_logs) > 0
                                        
                                        # Should have breakdown log (since symbols were skipped)
                                        breakdown_logs = [
                                            call for call in info_calls
                                            if "Skip breakdown" in str(call)
                                        ]
                                        assert len(breakdown_logs) > 0
