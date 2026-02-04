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

# Position keys
POSITION_KEY = "position:{symbol}"

# Screener keys
SCREENER_RESULTS_KEY = "screener:results"
SCREENER_LAST_SCAN_KEY = "screener:last_scan"
SCREENER_SIGNALS_HISTORY_KEY = "screener:signals:history"
SCREENER_STRATEGY_RESULTS_KEY = "screener:results:{strategy_id}"

# Screener TTL (seconds) - should be > scan_interval * 2 to survive missed scans
# Default scan interval is 60s, so 300s (5 minutes) gives ~5x buffer
SCREENER_RESULTS_TTL = 300

# Trading state keys
TRADING_ENABLED_KEY = "system:trading_enabled"
SHADOW_LIVE_MODE_KEY = "system:shadow_live_mode"
SHADOW_BALANCE_KEY = "system:shadow_balance"  # JSON: {"total_usd": float, "available_usd": float, "holdings": []}

# Events log key (renamed from activity to avoid ad blocker interference)
EVENTS_LOG_KEY = "events:log"

# Ingestor keys
INGESTOR_ACTIVE_SYMBOLS_KEY = "ingestor:active_symbols"

# Metrics keys
METRICS_OPEN_TRADES_KEY = "metrics:open_trades"  # Hash: trade_id -> trade data
METRICS_STRATEGY_STATS_KEY = "metrics:strategy:{strategy_id}"  # Hash: wins, losses, total_pnl

# Symbol volume data (24h volume from ticker)
SYMBOL_VOLUME_KEY = "market:volume"  # Hash: symbol -> JSON {volume_24h, updated_at}

# Signal execution cooldown keys
# Per-candle cooldown: includes bar_timestamp so it expires when new candle opens
SIGNAL_EXECUTED_KEY = "signal:executed:{strategy_id}:{symbol}:{bar_timestamp}"
# Legacy key format (for backward compatibility during transition)
SIGNAL_EXECUTED_KEY_LEGACY = "signal:executed:{strategy_id}:{symbol}"
SIGNAL_COOLDOWN_SECONDS = 14400  # 4 hours default (fallback for legacy keys)

# Last evaluated bar timestamp per strategy/symbol (interval-based evaluation)
STRATEGY_LAST_EVAL_KEY = "strategy:last_eval:{strategy_id}:{symbol}"
STRATEGY_LAST_EVAL_TTL = 604800  # 7 days TTL for cleanup

# Signal activity log debouncing (prevent duplicate activity log entries)
SIGNAL_LAST_LOGGED_KEY = "signal:last_logged:{strategy_id}:{symbol}:{signal_type}"
SIGNAL_LOG_COOLDOWN_SECONDS = 3600  # 1 hour cooldown after logging (or until candle close + invalidation)

# Execution allowed debouncing (ensure only ONE EXECUTION_ALLOWED per candle)
EXECUTION_ALLOWED_LOGGED_KEY = "execution:allowed_logged:{strategy_id}:{symbol}:{bar_timestamp}"
EXECUTION_ALLOWED_TTL = 86400  # 24 hours TTL (should cover multiple candle periods)

# R-multiples tracking per strategy (rolling window of last N trades)
STRATEGY_R_MULTIPLES_KEY = "strategy:r_multiples:{strategy_id}"
STRATEGY_R_MULTIPLES_MAX = 20  # Keep last 20 R-multiples

# Strategy drawdown tracking
STRATEGY_PEAK_EQUITY_KEY = "strategy:peak_equity:{strategy_id}"
STRATEGY_CURRENT_EQUITY_KEY = "strategy:current_equity:{strategy_id}"
STRATEGY_DRAWDOWN_KEY = "strategy:drawdown:{strategy_id}"
STRATEGY_DRAWDOWN_HISTORY_KEY = "strategy:drawdown_history:{strategy_id}"
STRATEGY_DRAWDOWN_HISTORY_MAX = 100  # Keep last 100 drawdown snapshots
STRATEGY_DISABLE_REASON_KEY = "strategy:disable_reason:{strategy_id}"

# Temporary exit reason storage (cleared after position close)
POSITION_EXIT_REASON_KEY = "position:exit_reason:{symbol}"
POSITION_EXIT_REASON_TTL = 300  # 5 minutes TTL

# TP1 tracking keys
POSITION_TP1_PRICE_KEY = "position:tp1_price:{symbol}"  # Store TP1 target price
POSITION_TP1_HIT_KEY = "position:tp1_hit:{symbol}"  # Set to "1" when TP1 is hit

# Strategy phase state (for multi-phase strategies like Volatility Breakout)
STRATEGY_PHASE_STATE_KEY = "strategy:phase_state:{strategy_id}:{symbol}"
STRATEGY_PHASE_STATE_TTL = 86400  # 24 hours TTL (should cover multiple candle periods)

# Failed symbols tracking (symbols that consistently fail to provide data)
FAILED_SYMBOLS_KEY = "ingestor:failed_symbols"  # Set: symbols that should be replaced
FAILED_SYMBOLS_TTL = 86400  # 24 hours TTL - failed symbols are retried after this period

# Asset pairs cache (Kraken costmin data)
ASSET_PAIRS_CACHE_KEY = "market:asset_pairs:{pair}"  # Hash: costmin, updated_at
ASSET_PAIRS_CACHE_TTL = 3600  # 1 hour TTL

# Universe refresh tracking (for startup staleness checks)
UNIVERSE_LAST_REFRESH_KEY = "ingestor:universe:last_refresh"  # String: ISO timestamp
RVOL_LAST_REFRESH_KEY = "ingestor:rvol:last_refresh"  # String: ISO timestamp

# Live slots tracking (optional, for caching)
LIVE_SLOTS_COUNT_KEY = "system:live_slots:count"  # String: current live position count (cached)

# Live universe restriction (set of allowed symbols for live trading)
LIVE_UNIVERSE_KEY = "system:live_universe"  # Set: allowed symbols for live execution

# Risk capital keys (daily recalculation based on equity)
RISK_CAPITAL_KEY = "system:risk_capital"  # String: risk capital amount (equity × 2%)
RISK_CAPITAL_UPDATED_KEY = "system:risk_capital:updated_at"  # String: ISO timestamp of last update
