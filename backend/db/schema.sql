-- Initial schema for Omni-Bot Trading Platform
-- Authoritative source: docs/MSSD.md, docs/EXECUTION_PLAN_M1_M2.md

-- Strategies table
CREATE TABLE IF NOT EXISTS strategies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    config JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'inactive',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT strategies_status_check CHECK (status IN ('active', 'inactive', 'paused'))
);

-- Signals table (corresponds to TradeIntent)
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    symbol VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    intent_type VARCHAR(20) NOT NULL,
    notional_risk_pct NUMERIC(10, 4) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT signals_side_check CHECK (side IN ('buy', 'sell')),
    CONSTRAINT signals_intent_type_check CHECK (intent_type IN ('enter', 'exit', 'reduce')),
    CONSTRAINT signals_status_check CHECK (status IN ('pending', 'approved', 'rejected', 'executed'))
);

-- Orders table (corresponds to Fill)
-- TICKET-603: Added is_live and execution_mode fields
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    symbol VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,
    executed_price NUMERIC(20, 8) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    fees NUMERIC(20, 8) NOT NULL DEFAULT 0,
    slippage NUMERIC(20, 8) NOT NULL DEFAULT 0,
    exchange_order_id VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    executed_at TIMESTAMP WITH TIME ZONE,
    is_live BOOLEAN NOT NULL DEFAULT TRUE,
    execution_mode VARCHAR(20) NOT NULL DEFAULT 'live',
    error_type VARCHAR(50),
    error_message VARCHAR(500),
    CONSTRAINT orders_side_check CHECK (side IN ('buy', 'sell')),
    CONSTRAINT orders_status_check CHECK (status IN ('pending', 'executed', 'cancelled', 'failed')),
    CONSTRAINT orders_execution_mode_check CHECK (execution_mode IN ('shadow', 'live'))
);

-- Equity curve table (portfolio snapshots)
CREATE TABLE IF NOT EXISTS equity_curve (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    total_equity NUMERIC(20, 8) NOT NULL,
    realized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    exposure_pct NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Positions table (tracks open positions with strategy ownership)
CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(50) NOT NULL UNIQUE,
    side VARCHAR(10) NOT NULL,
    quantity NUMERIC(20, 8) NOT NULL,
    entry_price NUMERIC(20, 8) NOT NULL,
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL,
    unrealized_pnl NUMERIC(20, 8) NOT NULL DEFAULT 0,
    opened_by_strategy_id VARCHAR(50),  -- NULL for legacy/manual trades
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT positions_side_check CHECK (side IN ('long', 'short'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_signals_strategy_id ON signals(strategy_id);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_signal_id ON orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_executed_at ON orders(executed_at);
CREATE INDEX IF NOT EXISTS idx_equity_curve_timestamp ON equity_curve(timestamp);
CREATE INDEX IF NOT EXISTS idx_positions_opened_by_strategy_id ON positions(opened_by_strategy_id);