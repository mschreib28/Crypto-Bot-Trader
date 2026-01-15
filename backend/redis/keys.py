"""Redis key naming constants for the application."""

# Market data stream keys
MARKET_RAW_STREAM = "market:raw:{symbol}"
MARKET_OHLCV_STREAM = "market:ohlcv:{symbol}:{interval}"

# Portfolio exposure keys
PORTFOLIO_EXPOSURE_TOTAL = "portfolio:exposure:total"

# Strategy status keys
STRATEGY_STATUS = "strategy:status:{strategy_id}"

# System state keys
SYSTEM_HALT = "system:halt"

# Execution keys
EXECUTION_NONCE = "execution:nonce"
