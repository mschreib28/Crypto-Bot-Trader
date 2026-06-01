-- Migration 005: Add VWAP Mean Reversion 1h instance (second worker, same strategy class).
-- Date: 2026-05-11
-- max_bars_in_trade=12 at 1h ~= 12h cap (mirrors spirit of 15m instance time-box).

BEGIN;

INSERT INTO strategies (name, config, status)
VALUES (
    'vwap_meanrev_1h',
    jsonb_build_object(
        'strategy_id', 'vwap_meanrev_1h',
        'name', 'VWAP Mean Reversion (1h)',
        'symbol', 'BTC/USD',
        'interval', '1h',
        'htf_interval', '4h',
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

COMMIT;
