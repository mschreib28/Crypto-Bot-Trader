-- Migration 002: Replace old strategies with new production-grade analyzed strategies
-- Date: 2026-01-30
-- Description: Replaces mean_reversion, macd_crossover, and trend_following
--              with vwap_meanreversion, volatility_breakout, and htf_trend_pullback

BEGIN;

-- Deactivate old strategies
UPDATE strategies 
SET status = 'inactive', 
    updated_at = NOW()
WHERE name IN ('mean_reversion', 'macd_crossover', 'trend_following')
AND status = 'active';

-- Insert new strategies (using ON CONFLICT DO UPDATE to allow re-running)
INSERT INTO strategies (name, config, status)
VALUES (
    'vwap_meanreversion',
    jsonb_build_object(
        'strategy_id', 'vwap_meanreversion',
        'name', 'VWAP Mean Reversion',
        'symbol', 'BTC/USD',
        'interval', '15m',
        'htf_interval', '1h',
        'max_risk_pct', 1.0,
        'volume_threshold', 1.5,
        'parameters', jsonb_build_object(
            'dev_threshold_ATR', 0.5,
            'rsi_oversold', 30.0,
            'rsi_overbought', 70.0,
            'atr_stop_mult', 1.5,
            'swing_lookback_bars', 5,
            'tp1_R', 1.2,
            'tp2_R', 2.5,
            'max_bars_in_trade', 12,
            'volume_filter_mode', 'conservative',
            'regime_slope_threshold', 0.001
        ),
        'filters', jsonb_build_object(
            'confidence_buy', 70,
            'confidence_sell', 70
        )
    ),
    'active'
)
ON CONFLICT (name) DO UPDATE SET
    config = EXCLUDED.config,
    status = EXCLUDED.status,
    updated_at = NOW();

INSERT INTO strategies (name, config, status)
VALUES (
    'volatility_breakout',
    jsonb_build_object(
        'strategy_id', 'volatility_breakout',
        'name', 'Volatility Breakout',
        'symbol', 'BTC/USD',
        'interval', '15m',
        'htf_interval', '1h',
        'max_risk_pct', 1.0,
        'volume_threshold', 1.5,
        'parameters', jsonb_build_object(
            'squeeze_percentile', 10.0,
            'squeeze_lookback_N', 200,
            'vol_compress_mult', 0.9,
            'vol_breakout_mult', 1.5,
            'retest_window_bars', 6,
            'retest_fail_bps', 50.0,
            'atr_stop_mult', 1.8,
            'atr_target1_mult', 2.0,
            'atr_target2_mult', 3.5,
            'use_measured_move', false
        ),
        'filters', jsonb_build_object(
            'confidence_buy', 65,
            'confidence_sell', 65
        )
    ),
    'active'
)
ON CONFLICT (name) DO UPDATE SET
    config = EXCLUDED.config,
    status = EXCLUDED.status,
    updated_at = NOW();

INSERT INTO strategies (name, config, status)
VALUES (
    'htf_trend_pullback',
    jsonb_build_object(
        'strategy_id', 'htf_trend_pullback',
        'name', 'HTF Trend Pullback',
        'symbol', 'BTC/USD',
        'interval', '1h',
        'htf_interval', '4h',
        'max_risk_pct', 1.0,
        'volume_threshold', 1.5,
        'parameters', jsonb_build_object(
            'htf_ema_slow', 200,
            'htf_ema_fast', 50,
            'htf_slope_threshold', 0.001,
            'pullback_max_ATR', 1.5,
            'atr_stop_mult', 1.5,
            'tp1_R', 1.5,
            'tp2_R', 3.0,
            'max_hours_in_trade', 24,
            'extension_ATR_mult', 3.0
        ),
        'filters', jsonb_build_object(
            'confidence_buy', 60,
            'confidence_sell', 60
        )
    ),
    'active'
)
ON CONFLICT (name) DO UPDATE SET
    config = EXCLUDED.config,
    status = EXCLUDED.status,
    updated_at = NOW();

COMMIT;
