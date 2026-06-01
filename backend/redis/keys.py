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
POSITION_STATUS_KEY = "position:status:{symbol}"  # String: SCANNING, PENDING, LIVE, EXITING, COOLDOWN, ERROR
POSITION_COOLDOWN_KEY = "position:cooldown:{symbol}"  # String: ISO timestamp when cooldown ends
POSITION_PENDING_ORDER_KEY = "position:pending_order:{symbol}"  # String: Order ID for pending orders

# Screener keys
SCREENER_RESULTS_KEY = "screener:results"
SCREENER_LAST_SCAN_KEY = "screener:last_scan"
# Bar refresh cooldown - avoid hammering Kraken when refreshing stale backfilled bars
BAR_REFRESH_COOLDOWN_KEY = "screener:bar_refresh:{symbol}:{interval}"
BAR_REFRESH_COOLDOWN_TTL = 600  # 10 minutes
SCREENER_SIGNALS_HISTORY_KEY = "screener:signals:history"
SCREENER_STRATEGY_RESULTS_KEY = "screener:results:{strategy_id}"
SCREENER_SCAN_STATUS_KEY = "screener:scan_status"
SCREENER_SCAN_STATUS_TTL = 7200  # 2h; refreshed each scan

# Screener TTL (seconds) - should be > scan_interval * 2 to survive missed scans
# Default scan interval is 60s, so 300s (5 minutes) gives ~5x buffer
SCREENER_RESULTS_TTL = 300

# Trading state keys
BOT_MODE_KEY = "system:bot_mode"  # "SHADOW" | "LIVE" (canonical)
TRADING_ENABLED_KEY = "system:trading_enabled"  # deprecated alias; synced with bot_mode
SHADOW_LIVE_MODE_KEY = "system:shadow_live_mode"  # deprecated alias; synced with bot_mode
SHADOW_BALANCE_KEY = "system:shadow_balance"  # JSON: {"total_usd": float, "available_usd": float, "holdings": []}
SHADOW_INITIAL_EQUITY_KEY = "system:shadow_initial_equity"  # Stores initial shadow equity when shadow balance is first set (for P&L calculations)

# Per-strategy manual SIM/LIVE (Task 3) — canonical strategy slug (e.g. htf_trend)
STRATEGY_MANUAL_MODE_KEY = "strategy:manual_mode:{strategy}"
STRATEGY_MANUAL_MODE_UPDATED_KEY = "strategy:manual_mode:{strategy}:updated_at"
STRATEGY_SIM_BALANCE_KEY = "strategy:sim_balance:{strategy}"  # JSON: {total_usd, available_usd, pnl}
STRATEGY_SIM_STATS_KEY = "strategy:sim_stats:{strategy}"  # JSON: trades, wins, losses, sum_win_r, sum_loss_r

# Events log key (renamed from activity to avoid ad blocker interference)
EVENTS_LOG_KEY = "events:log"

# Ingestor keys
INGESTOR_ACTIVE_SYMBOLS_KEY = "ingestor:active_symbols"
INGESTOR_HEARTBEAT_KEY = "ingestor:heartbeat"
INGESTOR_HEARTBEAT_TTL = 120  # 2x max age window
INGESTOR_SYMBOLS_COUNT_KEY = "ingestor:symbols_count"
INGESTOR_HEARTBEAT_MAX_AGE_SECONDS = 60

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
SIGNAL_COOLDOWN_SECONDS = 1800  # 30 minutes default (Ross Cameron spec: blacklist symbol for 30 minutes after loss)

# Last evaluated bar timestamp per strategy/symbol (interval-based evaluation)
STRATEGY_LAST_EVAL_KEY = "strategy:last_eval:{strategy_id}:{symbol}"
STRATEGY_LAST_EVAL_TTL = 604800  # 7 days TTL for cleanup

# Hybrid exit: consecutive opener-silent evaluations per strategy/symbol (bar-aligned via screener store)
STRATEGY_SILENCE_COUNT_KEY = "strategy:silence_count:{strategy_id}:{symbol}"
STRATEGY_SILENCE_COUNT_TTL = STRATEGY_LAST_EVAL_TTL
# Dedupe absent-symbol silence bumps when opener scan completes without this symbol in results
STRATEGY_HYBRID_LAST_OPENER_SCAN_KEY = "strategy:hybrid_last_opener_scan:{strategy_id}:{symbol}"
STRATEGY_HYBRID_LAST_OPENER_SCAN_TTL = STRATEGY_LAST_EVAL_TTL

# Signal activity log debouncing (prevent duplicate activity log entries)
SIGNAL_LAST_LOGGED_KEY = "signal:last_logged:{strategy_id}:{symbol}:{signal_type}"
SIGNAL_LOG_COOLDOWN_SECONDS = 3600  # 1 hour cooldown after logging (or until candle close + invalidation)
NO_SHORTING_LOG_KEY = "signal:no_shorting_log:{symbol}"  # Throttle no_shorting to once per 5 min
NO_SHORTING_LOG_TTL = 300

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

# Strategy drawdown cooldown — set when cumulative R loss exceeds threshold; blocks new entries
STRATEGY_DRAWDOWN_COOLDOWN_KEY = "cooldown:drawdown:{strategy_id}"
STRATEGY_DRAWDOWN_COOLDOWN_TTL = 14400  # 4 hours
# Sticky suspend until manual re-enable or backtest ACTIVE (canonical strategy slug)
STRATEGY_DRAWDOWN_SUSPENDED_KEY = "strategy:drawdown_suspended:{strategy}"
# Rolling sum of negative R-multiples in drawdown window (canonical strategy slug)
STRATEGY_CUMULATIVE_R_LOSS_KEY = "strategy:cumulative_r_loss:{strategy}"

# Trade analytics (closed trade factor attribution)
TRADE_ANALYTICS_RECORDS_KEY = "trade_analytics:records"
TRADE_ANALYTICS_PENDING_KEY = "trade_analytics:pending:{symbol}"
TRADE_ANALYTICS_MAX_RECORDS = 10000

# Temporary exit reason storage (cleared after position close)
POSITION_EXIT_REASON_KEY = "position:exit_reason:{symbol}"
POSITION_EXIT_REASON_TTL = 300  # 5 minutes TTL

# Debounce key to prevent the monitor from hammering failed forced-exit attempts
# every 10s cycle. If set, skip the exit attempt until TTL expires.
POSITION_EXIT_ATTEMPT_KEY = "position:exit_attempt:{symbol}"
POSITION_EXIT_FAIL_COUNT_KEY = "position:exit_fail_count:{symbol}"
POSITION_EXIT_FAIL_MAX = 3
POSITION_EXIT_ATTEMPT_TTL = 60  # 60s — one retry per minute max

# TP1 tracking keys
POSITION_TP1_PRICE_KEY = "position:tp1_price:{symbol}"  # Store TP1 target price
POSITION_TP1_HIT_KEY = "position:tp1_hit:{symbol}"  # Set to "1" when TP1 is hit

# Strategy scan heartbeat (throttled activity log when strategy runs but produces no BUY/SELL)
STRATEGY_SCAN_HEARTBEAT_KEY = "strategy:scan_heartbeat:{strategy_id}"
STRATEGY_SCAN_HEARTBEAT_TTL = 300  # 5 minutes

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

# Top 10 Obvious symbols list (Ross Cameron spec: maintain "Top 10 Obvious" list in Redis cache)
# JSON array of objects with: symbol, score, rvol, market_cap, supply_ratio, spread_bps, change_24h_pct
# Only pairs with A+ score > 0.85 are included
TOP_10_OBVIOUS_KEY = "screener:top_10_obvious"  # JSON: top 10 symbols by A+ score
TOP_10_OBVIOUS_TTL = 3600  # 1 hour TTL (refreshed every 60 seconds with screener scan)

# A+ Scores for all pairs (Redis hash: symbol -> JSON score data)
# Hash field = symbol, value = JSON with: score, grade, rvol, market_cap, supply_ratio, spread_bps, change_24h_pct
# Stores scores for ALL pairs (not just top 10) for enrichment of screener results
APLUS_SCORES_KEY = "screener:aplus_scores"  # Redis hash: symbol -> JSON score data
APLUS_SCORES_TTL = 3600  # 1 hour TTL (refreshed every 60 seconds with screener scan)

# Risk capital keys (daily recalculation based on equity)
RISK_CAPITAL_KEY = "system:risk_capital"  # String: risk capital amount (equity × 2%)
RISK_CAPITAL_UPDATED_KEY = "system:risk_capital:updated_at"  # String: ISO timestamp of last update

# Post-forced-exit cooldown (prevents immediate re-entry after ANY forced exit — invalidation or max_hold)
# Prevents churn where the bot exits at 0.1% and immediately re-enters the same losing pattern.
FORCED_EXIT_COOLDOWN_KEY = "cooldown:forced_exit:{symbol}:{strategy_id}"
FORCED_EXIT_COOLDOWN_TTL = 2700  # 45 minutes (3 × 15m candles)

# Per-symbol circuit breaker: block symbol for 48h if cumulative losses > $1.50
SYMBOL_BLOCKED_KEY = "cooldown:symbol_blocked:{symbol}"
SYMBOL_BLOCKED_TTL = 172800  # 48 hours
SYMBOL_CUMULATIVE_LOSS_KEY = "stats:symbol_loss:{symbol}"

# 3-Stage Pipeline cache keys
# Stage 1 static results (supply, price, listing age) — slow-changing, 20h TTL
PIPELINE_STAGE1_KEY = "screener:pipeline:stage1:{symbol}"
PIPELINE_STAGE1_TTL = 72_000  # 20 hours

# BTC 4h change for D4 pillar — 5 min TTL
PIPELINE_BTC_4H_KEY = "screener:pipeline:btc_4h_change"
PIPELINE_BTC_4H_TTL = 300  # 5 minutes
PIPELINE_BTC_DAILY_CLOSES_KEY = "screener:pipeline:btc_daily_closes_json"
PIPELINE_BTC_DAILY_CLOSES_TTL = 3600  # 1 hour — daily bars change slowly

# Supervisor meta-strategy evaluation keys
# supervisor:status:{strategy}  — JSON verdict per canonical strategy name
SUPERVISOR_STATUS_KEY = "supervisor:status:{strategy}"
# SET of canonical strategy names that have been evaluated
SUPERVISOR_INDEX_KEY = "supervisor:strategies"
# ISO timestamp of the last completed evaluation cycle
SUPERVISOR_LAST_RUN_KEY = "supervisor:last_run"
# NX lock to prevent overlapping cycles
SUPERVISOR_LOCK_KEY = "supervisor:lock"
# Lock TTL: 2 hours hard cap (longer than any single cycle should take)
SUPERVISOR_LOCK_TTL = 7200
# Rolling live evaluation (Task 4) — separate namespace from backtest verdicts
SUPERVISOR_LIVE_STATUS_KEY = "supervisor:live:{strategy}"
SUPERVISOR_LIVE_LAST_RUN_KEY = "supervisor:live:last_run"
SUPERVISOR_LIVE_PROMOTE_STREAK_KEY = "supervisor:live:promote_streak:{strategy}"
