-- Migration 004: Persistent activity log table
-- Stores all log_activity() events permanently for historical analysis.

CREATE TABLE IF NOT EXISTS activity_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp   TIMESTAMPTZ NOT NULL,
    type        VARCHAR(100) NOT NULL,
    message     TEXT NOT NULL,
    details     JSONB,
    symbol      VARCHAR(50),   -- denormalized from details for fast filtering
    strategy    VARCHAR(255),  -- denormalized from details for fast filtering
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp       ON activity_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_type            ON activity_log (type);
CREATE INDEX IF NOT EXISTS idx_activity_log_symbol          ON activity_log (symbol);
CREATE INDEX IF NOT EXISTS idx_activity_log_type_timestamp  ON activity_log (type, timestamp DESC);
