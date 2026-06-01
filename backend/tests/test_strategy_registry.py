"""Unit tests for backend/strategies/registry.py — strategy factory.

TDD RED phase: All tests here FAIL until registry.py is implemented.

Tests cover:
- create_strategy() returns (config, strategy) for registered names
- Unknown strategy name returns None
- Config is correctly populated from flat DB config dict
- Nested 'parameters' dict is flattened into config fields
- strategy_id override from DB row is applied
- symbol override from DB row is applied
- interval override from DB row is applied
- Exception safety: ValueError on bad field does not propagate (caller handles)
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers — keep Redis out of the import chain
# ---------------------------------------------------------------------------

def _make_redis_stub():
    client = MagicMock()
    client.ping.return_value = True
    client.get.return_value = None
    client.set.return_value = True
    return client


# ---------------------------------------------------------------------------
# Import under test (deferred so we can patch Redis before import)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_redis():
    """Prevent any real Redis connection during registry tests."""
    redis_stub = _make_redis_stub()
    with patch("backend.redis.get_redis_client", return_value=redis_stub):
        with patch("backend.redis.get_connection_pool"):
            yield


# ---------------------------------------------------------------------------
# Minimal DB-row-like config dicts (mirrors what strategies.sql produces)
# ---------------------------------------------------------------------------

VWAP_CONFIG = {
    "strategy_id": "vwap_meanreversion",
    "name": "VWAP Mean Reversion",
    "symbol": "BTC/USD",
    "interval": "15m",
    "htf_interval": "1h",
    "max_risk_pct": 1.0,
    "volume_threshold": 1.5,
    "parameters": {
        "dev_threshold_ATR": 0.5,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "atr_stop_mult": 1.5,
        "swing_lookback_bars": 5,
        "tp1_R": 1.2,
        "tp2_R": 2.5,
        "max_bars_in_trade": 12,
        "volume_filter_mode": "conservative",
        "regime_slope_threshold": 0.001,
    },
    "filters": {"confidence_buy": 70, "confidence_sell": 70},
}

VOLATILITY_CONFIG = {
    "strategy_id": "volatility_breakout",
    "name": "Volatility Breakout",
    "symbol": "BTC/USD",
    "interval": "15m",
    "htf_interval": "1h",
    "max_risk_pct": 1.0,
    "volume_threshold": 1.5,
    "parameters": {
        "squeeze_percentile": 10.0,
        "squeeze_lookback_N": 100,
        "vol_compress_mult": 0.9,
        "vol_breakout_mult": 1.5,
        "retest_window_bars": 6,
        "retest_fail_bps": 50.0,
        "atr_stop_mult": 1.8,
        "atr_target1_mult": 2.0,
        "atr_target2_mult": 3.5,
        "use_measured_move": False,
    },
    "filters": {"confidence_buy": 65, "confidence_sell": 65},
}

HTF_CONFIG = {
    "strategy_id": "htf_trend_pullback",
    "name": "HTF Trend Pullback",
    "symbol": "BTC/USD",
    "interval": "1h",
    "htf_interval": "4h",
    "max_risk_pct": 1.0,
    "volume_threshold": 1.5,
    "parameters": {
        "htf_ema_slow": 200,
        "htf_ema_fast": 50,
        "htf_slope_threshold": 0.001,
        "pullback_max_ATR": 1.5,
        "atr_stop_mult": 1.5,
        "tp1_R": 1.5,
        "tp2_R": 3.0,
        "max_hours_in_trade": 24,
        "extension_ATR_mult": 3.0,
    },
    "filters": {"confidence_buy": 60, "confidence_sell": 60},
}

MEANREV_CONFIG = {
    "strategy_id": "mean_reversion",
    "symbol": "ETH/USD",
    "interval": "4h",
}

MOMENTUM_CONFIG = {
    "strategy_id": "trend_following",
    "symbol": "BTC/USD",
    "interval": "4h",
}

PULLBACK_VWAP_CONFIG = {
    "strategy_id": "pullback_vwap",
    "name": "Pullback to VWAP",
    "symbol": "BTC/USD",
    "interval": "15m",
    "max_risk_pct": 1.0,
    "volume_threshold": 1.5,
    "max_hold_candles": 8,
    "parameters": {
        "long_only": True,
        "initial_move_min_pct": 8.0,
        "initial_move_lookback_bars": 96,
        "initial_move_rvol_min": 2.0,
        "pullback_threshold_pct": 0.5,
        "volume_absorption_check": True,
        "absorption_vs_sma_max": 1.5,
        "tp1_R": 1.0,
        "tp2_R": 2.0,
        "tp1_partial_pct": 0.6,
        "max_bars_in_trade": 8,
        "atr_stop_mult": 1.0,
        "rsi_period": 14,
        "atr_period": 14,
        "volume_sma_period": 20,
        "anchored_vwap_lookback": 20,
        "notional_risk_pct": 2.0,
    },
    "filters": {"confidence_buy": 65, "confidence_sell": 65},
}

BULL_FLAG_5M_CONFIG = {
    "strategy_id": "bull_flag_5m",
    "name": "Bull Flag 5m",
    "symbol": "BTC/USD",
    "interval": "5m",
    "max_hold_candles": 18,
    "parameters": {
        "pole_min_pct": 5.0,
        "rsi_overbought": 75.0,
    },
    "filters": {"confidence_buy": 75, "confidence_sell": 75},
}

SWING_BULL_FLAG_CONFIG = {
    "strategy_id": "swing_bull_flag",
    "name": "Swing Bull Flag (W2)",
    "symbol": "ETH/USD",
    "interval": "4h",
    "max_hold_candles": 30,
    "parameters": {
        "flag_min_candles": 2,
        "flag_max_candles": 5,
        "allow_mild_pullback": False,
        "require_daily_ema200": True,
        "require_btc_d4_gate": True,
        "daily_ema_period": 200,
    },
    "filters": {"confidence_buy": 75, "confidence_sell": 75},
}

MACD_CONFIG = {
    "strategy_id": "macd_crossover",
    "symbol": "BTC/USD",
    "interval": "1h",
    "parameters": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
    },
}


# ---------------------------------------------------------------------------
# Tests: create_strategy() happy-path for all 6 strategy types
# ---------------------------------------------------------------------------

class TestCreateStrategyVWAP:
    """create_strategy('vwap_meanrev', ...) returns correct types."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
        from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy

        result = create_strategy("vwap_meanrev", "db-uuid-001", VWAP_CONFIG)

        assert result is not None, "Expected (config, strategy) tuple, got None"
        config, strategy = result
        assert isinstance(config, VWAPMeanReversionConfig)
        assert isinstance(strategy, VWAPMeanReversionStrategy)

    def test_strategy_id_set_from_db_uuid(self):
        from backend.strategies.registry import create_strategy

        config, strategy = create_strategy("vwap_meanrev", "db-uuid-001", VWAP_CONFIG)
        assert config.strategy_id == "db-uuid-001"
        assert strategy.strategy_id == "db-uuid-001"

    def test_symbol_set_from_db_config(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("vwap_meanrev", "db-uuid-001", VWAP_CONFIG)
        assert config.symbol == "BTC/USD"

    def test_interval_set_from_db_config(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("vwap_meanrev", "db-uuid-001", VWAP_CONFIG)
        assert config.interval == "15m"

    def test_parameters_dict_flattened_into_config(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("vwap_meanrev", "db-uuid-001", VWAP_CONFIG)
        # Parameters from the nested 'parameters' key should be set on config
        assert config.atr_stop_mult == 1.5
        assert config.rsi_oversold == 30.0

    def test_also_accepts_vwap_meanreversion_name(self):
        """The DB seed uses 'vwap_meanreversion' — registry must match it."""
        from backend.strategies.registry import create_strategy
        from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy

        result = create_strategy("vwap_meanreversion", "db-uuid-001", VWAP_CONFIG)
        assert result is not None
        _, strategy = result
        assert isinstance(strategy, VWAPMeanReversionStrategy)

    def test_vwap_meanrev_1h_name_uses_same_factory(self):
        """Second VWAP instance: DB name vwap_meanrev_1h with 1h/4h config."""
        from backend.strategies.registry import create_strategy
        from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy

        cfg_1h = {
            **VWAP_CONFIG,
            "interval": "1h",
            "htf_interval": "4h",
            "strategy_id": "vwap_meanrev_1h",
        }
        result = create_strategy("vwap_meanrev_1h", "db-uuid-1h", cfg_1h)
        assert result is not None
        config, strategy = result
        assert isinstance(strategy, VWAPMeanReversionStrategy)
        assert config.strategy_id == "db-uuid-1h"
        assert config.interval == "1h"
        assert config.htf_interval == "4h"


class TestCreateStrategyVolatilityBreakout:
    """create_strategy('volatility_breakout', ...) returns correct types."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig
        from research.strategies.volatility_breakout.strategy import VolatilityBreakoutStrategy

        result = create_strategy("volatility_breakout", "db-uuid-002", VOLATILITY_CONFIG)

        assert result is not None
        config, strategy = result
        assert isinstance(config, VolatilityBreakoutConfig)
        assert isinstance(strategy, VolatilityBreakoutStrategy)

    def test_strategy_id_overridden(self):
        from backend.strategies.registry import create_strategy

        config, strategy = create_strategy("volatility_breakout", "db-uuid-002", VOLATILITY_CONFIG)
        assert config.strategy_id == "db-uuid-002"
        assert strategy.strategy_id == "db-uuid-002"

    def test_parameters_flattened(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("volatility_breakout", "db-uuid-002", VOLATILITY_CONFIG)
        assert config.squeeze_percentile == 10.0
        assert config.atr_stop_mult == 1.8


class TestCreateStrategyHTFTrend:
    """create_strategy('htf_trend', ...) returns correct types."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.htf_trend.config import HTFTrendConfig
        from research.strategies.htf_trend.strategy import HTFTrendStrategy

        result = create_strategy("htf_trend", "db-uuid-003", HTF_CONFIG)

        assert result is not None
        config, strategy = result
        assert isinstance(config, HTFTrendConfig)
        assert isinstance(strategy, HTFTrendStrategy)

    def test_also_accepts_htf_trend_pullback_name(self):
        """The DB seed uses 'htf_trend_pullback' — registry must match it."""
        from backend.strategies.registry import create_strategy
        from research.strategies.htf_trend.strategy import HTFTrendStrategy

        result = create_strategy("htf_trend_pullback", "db-uuid-003", HTF_CONFIG)
        assert result is not None
        _, strategy = result
        assert isinstance(strategy, HTFTrendStrategy)

    def test_parameters_flattened(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("htf_trend", "db-uuid-003", HTF_CONFIG)
        assert config.htf_ema_slow == 200
        assert config.atr_stop_mult == 1.5


class TestCreateStrategyPullbackVWAPRetired:
    """Pullback to VWAP retired May 2026 — factory does not load it."""

    def test_pullback_vwap_name_returns_none(self):
        from backend.strategies.registry import create_strategy

        assert create_strategy("pullback_vwap", "db-uuid-pb1", PULLBACK_VWAP_CONFIG) is None

    def test_pullback_to_vwap_alias_returns_none(self):
        from backend.strategies.registry import create_strategy

        assert create_strategy("pullback_to_vwap", "db-uuid-pb1", PULLBACK_VWAP_CONFIG) is None


class TestCreateStrategyBullFlag:
    """create_strategy('bull_flag_5m', ...) returns BullFlagStrategy."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.bull_flag.config import BullFlagConfig
        from research.strategies.bull_flag.strategy import BullFlagStrategy

        result = create_strategy("bull_flag_5m", "db-uuid-bf5", BULL_FLAG_5M_CONFIG)
        assert result is not None
        config, strategy = result
        assert isinstance(config, BullFlagConfig)
        assert isinstance(strategy, BullFlagStrategy)
        assert config.strategy_id == "db-uuid-bf5"
        assert config.interval == "5m"
        assert config.max_hold_candles == 18


class TestCreateStrategySwingBullFlag:
    """create_strategy('swing_bull_flag', ...) returns BullFlagStrategy (W2 preset)."""

    def test_returns_tuple_with_4h_and_swing_flags(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.bull_flag.config import BullFlagConfig
        from research.strategies.bull_flag.strategy import BullFlagStrategy

        result = create_strategy("swing_bull_flag", "db-uuid-w2", SWING_BULL_FLAG_CONFIG)
        assert result is not None
        config, strategy = result
        assert isinstance(config, BullFlagConfig)
        assert isinstance(strategy, BullFlagStrategy)
        assert config.strategy_id == "db-uuid-w2"
        assert config.interval == "4h"
        assert config.max_hold_candles == 30
        assert config.allow_mild_pullback is False
        assert config.require_daily_ema200 is True
        assert config.require_btc_d4_gate is True
        assert config.daily_ema_period == 200
        assert config.flag_min_candles == 2
        assert config.flag_max_candles == 5


class TestCreateStrategyMeanRev:
    """create_strategy('meanrev', ...) returns correct types."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.meanrev.config import MeanReversionConfig
        from research.strategies.meanrev.strategy import MeanReversionStrategy

        result = create_strategy("meanrev", "db-uuid-004", MEANREV_CONFIG)

        assert result is not None
        config, strategy = result
        assert isinstance(config, MeanReversionConfig)
        assert isinstance(strategy, MeanReversionStrategy)

    def test_also_accepts_mean_reversion_name(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.meanrev.strategy import MeanReversionStrategy

        result = create_strategy("mean_reversion", "db-uuid-004", MEANREV_CONFIG)
        assert result is not None
        _, strategy = result
        assert isinstance(strategy, MeanReversionStrategy)

    def test_symbol_set_from_config(self):
        from backend.strategies.registry import create_strategy

        config, _ = create_strategy("meanrev", "db-uuid-004", MEANREV_CONFIG)
        assert config.symbol == "ETH/USD"


class TestCreateStrategyMomentum:
    """create_strategy('momentum', ...) returns correct types."""

    def test_returns_tuple_of_config_and_strategy(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.momentum.config import MomentumConfig
        from research.strategies.momentum.strategy import MomentumStrategy

        result = create_strategy("momentum", "db-uuid-005", MOMENTUM_CONFIG)

        assert result is not None
        config, strategy = result
        assert isinstance(config, MomentumConfig)
        assert isinstance(strategy, MomentumStrategy)

    def test_also_accepts_trend_following_name(self):
        from backend.strategies.registry import create_strategy
        from research.strategies.momentum.strategy import MomentumStrategy

        result = create_strategy("trend_following", "db-uuid-005", MOMENTUM_CONFIG)
        assert result is not None
        _, strategy = result
        assert isinstance(strategy, MomentumStrategy)


class TestCreateStrategyMACDRetired:
    """Standalone MACD retired May 2026 — factory does not load it."""

    def test_macd_name_returns_none(self):
        from backend.strategies.registry import create_strategy

        assert create_strategy("macd", "db-uuid-006", MACD_CONFIG) is None

    def test_macd_crossover_name_returns_none(self):
        from backend.strategies.registry import create_strategy

        assert create_strategy("macd_crossover", "db-uuid-006", MACD_CONFIG) is None


# ---------------------------------------------------------------------------
# Tests: unknown strategy name → returns None (does not raise)
# ---------------------------------------------------------------------------

class TestCreateStrategyUnknownName:
    """Unrecognised strategy names must return None gracefully."""

    def test_unknown_name_returns_none(self):
        from backend.strategies.registry import create_strategy

        result = create_strategy("nonexistent_strategy", "db-uuid-999", {})
        assert result is None

    def test_empty_name_returns_none(self):
        from backend.strategies.registry import create_strategy

        result = create_strategy("", "db-uuid-999", {})
        assert result is None

    def test_none_config_does_not_raise(self):
        """Passing None as config_data should be handled gracefully."""
        from backend.strategies.registry import create_strategy

        # Should not raise, should return None or a default-config result
        try:
            result = create_strategy("meanrev", "db-uuid-004", None)
            # Either None or a valid tuple is acceptable
            if result is not None:
                config, strategy = result
                assert config is not None
        except Exception as e:
            pytest.fail(f"create_strategy raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# Tests: config flattening logic
# ---------------------------------------------------------------------------

class TestConfigFlattening:
    """Verify that nested 'parameters' dict is merged correctly."""

    def test_top_level_fields_not_overwritten_by_excluded_keys(self):
        """'filters', 'max_risk_pct', 'volume_threshold' must NOT leak into config."""
        from backend.strategies.registry import create_strategy
        from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig

        config, _ = create_strategy("vwap_meanrev", "uuid-x", VWAP_CONFIG)

        # VWAPMeanReversionConfig has no 'filters' or 'max_risk_pct' field —
        # if flattening were wrong it would raise TypeError on construction.
        assert isinstance(config, VWAPMeanReversionConfig)

    def test_parameters_dict_values_override_defaults(self):
        """Values in 'parameters' sub-dict are applied to the config dataclass."""
        from backend.strategies.registry import create_strategy

        modified_config = {
            **VWAP_CONFIG,
            "parameters": {
                **VWAP_CONFIG["parameters"],
                "rsi_oversold": 25.0,  # Non-default value
            },
        }
        config, _ = create_strategy("vwap_meanrev", "uuid-x", modified_config)
        assert config.rsi_oversold == 25.0

    def test_empty_parameters_dict_uses_config_defaults(self):
        """When 'parameters' is empty or absent, config dataclass defaults apply."""
        from backend.strategies.registry import create_strategy

        minimal_config = {
            "symbol": "ETH/USD",
            "interval": "15m",
        }
        result = create_strategy("vwap_meanrev", "uuid-x", minimal_config)
        assert result is not None
        config, _ = result
        # Should have a default rsi_oversold, not raise
        assert config.rsi_oversold is not None


# ---------------------------------------------------------------------------
# Tests: get_required_interval() helper
# ---------------------------------------------------------------------------

class TestGetRequiredInterval:
    """get_required_interval() extracts the interval from a DB config dict."""

    def test_returns_interval_from_config(self):
        from backend.strategies.registry import get_required_interval

        assert get_required_interval({"interval": "15m"}) == "15m"
        assert get_required_interval({"interval": "1h"}) == "1h"
        assert get_required_interval({"interval": "4h"}) == "4h"

    def test_pullback_vwap_seed_shape(self):
        from backend.strategies.registry import get_required_interval

        assert get_required_interval(PULLBACK_VWAP_CONFIG) == "15m"

    def test_returns_default_when_missing(self):
        from backend.strategies.registry import get_required_interval

        result = get_required_interval({})
        assert result == "5m"  # default

    def test_returns_default_for_none_config(self):
        from backend.strategies.registry import get_required_interval

        result = get_required_interval(None)
        assert result == "5m"
