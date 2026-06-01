-- Migration 003: Set max_hold_candles explicitly in strategy configs
-- Fixes: monitor.py fallback default (6 candles) was being used at 5m interval
-- instead of the intended 15m interval, causing trades to exit after only 30 min.
-- After this migration, vwap_meanreversion holds for 6 × 15m = 90 min max.

UPDATE strategies
SET config = config || jsonb_build_object(
    'max_hold_candles', 6,
    'interval', '15m'
),
updated_at = NOW()
WHERE name = 'vwap_meanreversion';

UPDATE strategies
SET config = config || jsonb_build_object(
    'max_hold_candles', 4,
    'interval', '15m'
),
updated_at = NOW()
WHERE name = 'volatility_breakout';

UPDATE strategies
SET config = config || jsonb_build_object(
    'max_hold_candles', 3,
    'interval', '1h'
),
updated_at = NOW()
WHERE name = 'htf_trend_pullback';
