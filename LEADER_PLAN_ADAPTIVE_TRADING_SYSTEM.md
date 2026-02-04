# Leader Plan: Adaptive Self-Improving Trading System

## Problem Statement

**Client Goal:** The system must dynamically update itself based on performance to consistently make money, even if it's pennies or a couple dollars. The system should be self-improving and adaptive.

**Current State:**
- **Performance:** 0% win rate, -$8.70 total P&L, losing 20% per week
- **Static Configuration:** Strategy parameters (confidence thresholds, filters) are fixed
- **No Adaptation:** System doesn't adjust based on performance
- **No Auto-Disable:** Underperforming strategies continue trading
- **Fixed Risk:** Position sizing doesn't adapt to win rate

**Critical Requirements:**
1. **Real-Time Performance Monitoring:** Track win rate, P&L per strategy continuously
2. **Adaptive Parameter Adjustment:** Automatically adjust confidence thresholds based on performance
3. **Strategy Auto-Disable:** Pause strategies that are consistently losing
4. **Dynamic Risk Management:** Reduce position size for underperforming strategies
5. **Performance-Based Prioritization:** Focus on strategies that are profitable
6. **Consistent Profitability:** Aim for steady small gains rather than high-risk trades

## 1. Scope

### In Scope
- **Performance Monitor Service:** Continuous monitoring of strategy performance (win rate, P&L, trade count)
- **Adaptive Confidence Thresholds:** Automatically adjust confidence thresholds based on recent win rate
- **Strategy Auto-Disable:** Automatically pause strategies with poor performance (e.g., <40% win rate after 10+ trades)
- **Dynamic Position Sizing:** Adjust position size based on strategy performance (reduce size for losing strategies)
- **Performance-Based Strategy Selection:** Prioritize better-performing strategies in signal generation
- **Adaptive Filters:** Tighten filters (higher confidence thresholds) when win rate drops
- **Profitability Target:** System adjusts to maintain consistent small profits

### Out of Scope
- Changing core strategy algorithms (MACD, RSI calculations remain unchanged)
- Frontend changes (backend-only adaptation)
- Manual strategy parameter tuning (system is fully automated)
- Historical backtesting (focus on live performance adaptation)

## 2. File Ownership

### Performance Monitoring (backend/performance/)
- `backend/performance/monitor.py` - **NEW**: Continuous performance monitoring service
- `backend/performance/adaptation.py` - **NEW**: Adaptive parameter adjustment logic
- `backend/performance/models.py` - **NEW**: Performance metrics and adaptation rules

### Strategy Management (backend/strategies/)
- `backend/strategies/manager.py` - **NEW**: Strategy lifecycle management (enable/disable based on performance)
- `backend/strategies/adaptation.py` - **NEW**: Strategy parameter adaptation

### Risk Management (backend/risk/)
- `backend/risk/adaptive_sizing.py` - **NEW**: Dynamic position sizing based on performance
- `backend/risk/sizing.py` - Modify to accept performance-based adjustments

### Screening (backend/screener/)
- `backend/screener/service.py` - Integrate adaptive confidence thresholds
- `backend/screener/engine.py` - Use performance-based strategy prioritization

### Database (backend/db/)
- `backend/db/models.py` - Add performance tracking fields to Strategy model (optional)
- `backend/db/migrations/002_add_performance_tracking.sql` - **NEW**: Migration for performance metrics

### Configuration (.env)
- `ADAPTIVE_ENABLED=true` - Enable/disable adaptive system
- `MIN_WIN_RATE_THRESHOLD=0.40` - Minimum win rate to keep strategy active (40%)
- `MIN_TRADES_FOR_EVALUATION=10` - Minimum trades before evaluating strategy
- `ADAPTIVE_CONFIDENCE_STEP=5.0` - Step size for confidence threshold adjustments (5%)
- `TARGET_WIN_RATE=0.55` - Target win rate for adaptation (55%)
- `PROFITABILITY_TARGET_USD=1.0` - Target daily profit in USD (can be small)

## 3. Contracts Impacted

### API Contracts
- **No changes** - Adaptation is internal, API responses remain same

### Database Schema
- **Optional:** Add `performance_metrics` JSONB field to `strategies` table:
  ```sql
  ALTER TABLE strategies ADD COLUMN performance_metrics JSONB DEFAULT '{}';
  ```
  Stores: `win_rate`, `total_trades`, `total_pnl`, `last_evaluated_at`, `adaptive_confidence_buy`, `adaptive_confidence_sell`

### Environment Variables
- `ADAPTIVE_ENABLED` (default: true) - Master switch for adaptive system
- `MIN_WIN_RATE_THRESHOLD` (default: 0.40) - Auto-disable threshold
- `MIN_TRADES_FOR_EVALUATION` (default: 10) - Minimum trades before evaluation
- `ADAPTIVE_CONFIDENCE_STEP` (default: 5.0) - Confidence adjustment step size
- `TARGET_WIN_RATE` (default: 0.55) - Target win rate
- `PROFITABILITY_TARGET_USD` (default: 1.0) - Daily profit target

### Strategy Config Schema
- **No changes** - Adaptation modifies `filters.confidence_buy` and `filters.confidence_sell` dynamically in database

## 4. Acceptance Criteria

### Performance Monitoring (TICKET-501)
- [ ] Service tracks win rate, P&L, trade count per strategy in real-time
- [ ] Metrics updated after each trade execution
- [ ] Performance data stored in Redis with TTL (last 30 days)
- [ ] Service runs continuously and updates metrics every 5 minutes
- [ ] Metrics accessible via API endpoint `/api/v1/strategies/{id}/performance`

### Adaptive Confidence Thresholds (TICKET-502)
- [ ] System adjusts confidence thresholds based on recent win rate
- [ ] If win rate < target: increase confidence threshold (tighter filters)
- [ ] If win rate > target: decrease confidence threshold (more opportunities)
- [ ] Adjustments logged: "Strategy X: win_rate=45%, adjusting confidence_buy 90% -> 95%"
- [ ] Thresholds clamped to valid range (50-100%)
- [ ] Changes persist to database `strategies.config.filters`

### Strategy Auto-Disable (TICKET-503)
- [ ] System automatically pauses strategies with win rate < threshold after minimum trades
- [ ] Evaluation: After N trades (default: 10), if win_rate < 40%, set status='paused'
- [ ] Log: "Strategy X auto-paused: win_rate=35% < 40% threshold after 12 trades"
- [ ] Paused strategies don't generate signals
- [ ] Manual re-enable required (or auto-enable after performance improves)

### Dynamic Position Sizing (TICKET-504)
- [ ] Position size reduced for underperforming strategies
- [ ] Formula: `adjusted_size = base_size * performance_multiplier`
- [ ] Performance multiplier: `min(1.0, win_rate / target_win_rate)`
- [ ] Example: win_rate=40%, target=55% → multiplier=0.73 → 27% smaller positions
- [ ] Well-performing strategies can increase size (capped at 1.5x base)

### Performance-Based Prioritization (TICKET-505)
- [ ] Strategies sorted by performance (win rate, then P&L) before scanning
- [ ] Better-performing strategies evaluated first
- [ ] Underperforming strategies evaluated last or skipped if too many signals
- [ ] Log: "Prioritizing strategy X (win_rate=60%) over Y (win_rate=35%)"

### Profitability Consistency (TICKET-506)
- [ ] System tracks daily P&L target
- [ ] If daily profit >= target: reduce trading frequency (cooldown)
- [ ] If daily loss: tighten filters further, reduce position sizes
- [ ] Goal: Consistent small profits rather than high-risk trades
- [ ] Log: "Daily P&L: $1.20 (target: $1.00), entering cooldown mode"

## 5. Dependencies

### Must Complete First
- **TICKET-401** (Performance Analysis) - **COMPLETED** - Baseline metrics established
- **TICKET-201** (P&L Calculation) - **COMPLETED** - P&L tracking working

### Can Run Parallel
- **TICKET-501** (Performance Monitoring) - Can start immediately
- **TICKET-502** (Adaptive Thresholds) - Can start immediately
- **TICKET-503** (Auto-Disable) - Depends on TICKET-501
- **TICKET-504** (Dynamic Sizing) - Depends on TICKET-501

### Must Complete Before Production
- **TICKET-505** (Prioritization) - Should be tested after adaptation
- **TICKET-506** (Profitability Consistency) - Final optimization

## Agent Launch Instructions

---

### Ticket 1: Real-Time Performance Monitoring Service
**Agent:** `backend-execute`  
**Ticket:** `TICKET-501: Implement real-time performance monitoring service`  
**Branch:** `backend/performance-monitoring`

**Prompt:**
```
Implement a real-time performance monitoring service that tracks strategy performance continuously.

Requirements:
1. Create `backend/performance/monitor.py`:
   - Service class `PerformanceMonitor` that runs continuously
   - Tracks metrics per strategy: win_rate, total_trades, total_pnl, recent_pnl (last 24h)
   - Updates metrics after each trade execution (listen to position updates)
   - Stores metrics in Redis with key pattern: `performance:strategy:{strategy_id}`
   - TTL: 30 days (keep last 30 days of data)
   - Update interval: Every 5 minutes (recalculate from positions)

2. Metrics to track:
   - `win_rate`: (winning_trades / total_trades) * 100
   - `total_trades`: Count of all trades
   - `winning_trades`: Count of profitable trades
   - `losing_trades`: Count of losing trades
   - `total_pnl`: Sum of all P&L
   - `recent_pnl_24h`: P&L from trades in last 24 hours
   - `average_win`: Average profit per winning trade
   - `average_loss`: Average loss per losing trade
   - `last_updated`: Timestamp of last update

3. Integration points:
   - Hook into `backend/positions/tracker.py`: When position P&L updates, update performance metrics
   - Hook into `backend/execution/executor.py`: When trade executes, record trade outcome
   - Use `backend/positions/monitor.py` as reference for service pattern

4. Create `backend/performance/models.py`:
   - `StrategyPerformance` dataclass with all metrics above
   - `to_dict()` and `from_dict()` methods for Redis storage

5. API endpoint (optional, in `backend/api/routes/strategies.py`):
   - `GET /api/v1/strategies/{strategy_id}/performance`
   - Returns current performance metrics for strategy

6. Startup integration:
   - Add to `backend/api/main.py` startup events
   - Start `PerformanceMonitor` service on API startup
   - Stop gracefully on shutdown

Files to create:
- `backend/performance/__init__.py`
- `backend/performance/monitor.py` - Main monitoring service
- `backend/performance/models.py` - Performance data models

Files to modify:
- `backend/api/main.py` - Add PerformanceMonitor startup/shutdown
- `backend/positions/tracker.py` - Call performance monitor on P&L updates
- `backend/execution/executor.py` - Record trade outcomes

Do not modify:
- Strategy evaluation logic
- Signal generation
- Core trading execution

Acceptance criteria:
- Service runs continuously and updates metrics every 5 minutes
- Metrics updated immediately after trade execution
- Performance data accessible via Redis and API
- Service starts/stops gracefully with API
```

---

### Ticket 2: Adaptive Confidence Threshold Adjustment
**Agent:** `backend-execute`  
**Ticket:** `TICKET-502: Implement adaptive confidence threshold adjustment`  
**Branch:** `backend/adaptive-confidence`

**Prompt:**
```
Implement adaptive confidence threshold adjustment that automatically modifies strategy confidence thresholds based on performance.

Requirements:
1. Create `backend/performance/adaptation.py`:
   - Class `AdaptiveThresholdManager` that adjusts confidence thresholds
   - Method `adjust_confidence_thresholds(strategy_id: str) -> tuple[float, float]`
   - Returns: (new_confidence_buy, new_confidence_sell)

2. Adaptation logic:
   - Read current performance metrics from Redis (`performance:strategy:{strategy_id}`)
   - Read current thresholds from database (`strategies.config.filters.confidence_buy/sell`)
   - Calculate adjustment:
     * If win_rate < TARGET_WIN_RATE (default 55%): Increase threshold by ADAPTIVE_CONFIDENCE_STEP (default 5%)
     * If win_rate > TARGET_WIN_RATE: Decrease threshold by ADAPTIVE_CONFIDENCE_STEP
     * If win_rate == TARGET_WIN_RATE ± 2%: No change (dead zone to prevent oscillation)
   - Clamp thresholds to valid range: [50, 100]
   - Minimum trades required: Only adjust if total_trades >= MIN_TRADES_FOR_EVALUATION (default 10)

3. Update database:
   - Modify `strategies.config.filters.confidence_buy` and `confidence_sell` in database
   - Use `backend/db/models.py` Strategy model
   - Log changes: "Strategy {name}: Adjusted confidence_buy {old}% -> {new}% (win_rate={win_rate}%)"

4. Integration:
   - Call `adjust_confidence_thresholds()` from `PerformanceMonitor` after metrics update
   - Only adjust if `ADAPTIVE_ENABLED=true` (environment variable)
   - Adjustments happen every evaluation cycle (every 5 minutes if performance changed)

5. Environment variables (.env):
   - `ADAPTIVE_ENABLED=true` - Master switch
   - `TARGET_WIN_RATE=0.55` - Target win rate (55%)
   - `ADAPTIVE_CONFIDENCE_STEP=5.0` - Step size for adjustments (5%)
   - `MIN_TRADES_FOR_EVALUATION=10` - Minimum trades before adjusting

6. Logging:
   - Log all threshold adjustments with reasoning
   - Log when adjustment skipped (insufficient trades, dead zone, etc.)

Files to create:
- `backend/performance/adaptation.py` - Adaptive threshold logic

Files to modify:
- `backend/performance/monitor.py` - Call adaptation after metrics update
- `backend/screener/service.py` - Use updated thresholds from database (already reads from config)
- `.env` - Add adaptive configuration variables

Do not modify:
- Strategy evaluation algorithms
- Signal generation logic
- Core confidence scoring

Acceptance criteria:
- Thresholds adjust automatically based on win rate
- Adjustments logged clearly with reasoning
- Thresholds persist to database
- System respects minimum trade requirements
- Dead zone prevents oscillation
```

---

### Ticket 3: Strategy Auto-Disable Based on Performance
**Agent:** `backend-execute`  
**Ticket:** `TICKET-503: Implement automatic strategy disable for underperforming strategies`  
**Branch:** `backend/strategy-auto-disable`

**Prompt:**
```
Implement automatic strategy disabling for strategies that consistently underperform.

Requirements:
1. Create `backend/strategies/manager.py`:
   - Class `StrategyLifecycleManager` for managing strategy enable/disable
   - Method `evaluate_and_disable_poor_performers() -> List[str]`
   - Returns list of strategy IDs that were auto-disabled

2. Auto-disable logic:
   - Evaluate all active strategies
   - For each strategy:
     * Read performance metrics from Redis
     * Check: `total_trades >= MIN_TRADES_FOR_EVALUATION` (default 10)
     * Check: `win_rate < MIN_WIN_RATE_THRESHOLD` (default 40%)
     * If both conditions met: Set strategy `status='paused'` in database
   - Log: "Strategy {name} ({id}) auto-paused: win_rate={win_rate}% < {threshold}% after {trades} trades"

3. Re-enable logic (optional, future):
   - Periodically check paused strategies
   - If performance improves (manual review or auto-criteria), re-enable
   - For now: Manual re-enable only (via API or database)

4. Integration:
   - Call `evaluate_and_disable_poor_performers()` from `PerformanceMonitor` periodically
   - Evaluation frequency: Every 10 minutes (less frequent than metrics update)
   - Only evaluate if `ADAPTIVE_ENABLED=true`

5. Screener integration:
   - Modify `backend/screener/service.py` `_load_enabled_strategies()`:
     * Already filters by `status='active'`, so paused strategies automatically excluded
   - No changes needed - existing filter works

6. Environment variables (.env):
   - `MIN_WIN_RATE_THRESHOLD=0.40` - Auto-disable threshold (40%)
   - `MIN_TRADES_FOR_EVALUATION=10` - Minimum trades before evaluation

7. Logging and alerts:
   - Log all auto-disable actions
   - Log when evaluation skipped (insufficient trades, already paused, etc.)
   - Consider adding activity log entry for visibility

Files to create:
- `backend/strategies/__init__.py`
- `backend/strategies/manager.py` - Strategy lifecycle management

Files to modify:
- `backend/performance/monitor.py` - Call strategy evaluation
- `backend/db/models.py` - Strategy model (no changes, uses existing status field)
- `.env` - Add MIN_WIN_RATE_THRESHOLD

Do not modify:
- Strategy evaluation logic
- Signal generation
- Core trading execution

Acceptance criteria:
- Strategies with win_rate < 40% after 10+ trades are automatically paused
- Paused strategies don't generate signals
- All auto-disable actions logged
- System respects minimum trade requirements
- Manual re-enable possible via database/API
```

---

### Ticket 4: Dynamic Position Sizing Based on Performance
**Agent:** `backend-execute`  
**Ticket:** `TICKET-504: Implement dynamic position sizing based on strategy performance`  
**Branch:** `backend/dynamic-position-sizing`

**Prompt:**
```
Implement dynamic position sizing that adjusts trade size based on strategy performance.

Requirements:
1. Create `backend/risk/adaptive_sizing.py`:
   - Class `AdaptivePositionSizer` that wraps existing `PositionSizer`
   - Method `calculate_adaptive_size(strategy_id: str, base_size: float) -> float`
   - Returns adjusted position size based on performance

2. Performance-based sizing logic:
   - Read performance metrics from Redis (`performance:strategy:{strategy_id}`)
   - Calculate performance multiplier:
     * `multiplier = min(1.0, win_rate / TARGET_WIN_RATE)`
     * Example: win_rate=40%, target=55% → multiplier=0.73
     * Example: win_rate=60%, target=55% → multiplier=1.0 (capped)
   - For well-performing strategies (win_rate > target):
     * Allow increase up to 1.5x: `multiplier = min(1.5, 1.0 + (win_rate - target) / target)`
   - Apply multiplier: `adjusted_size = base_size * multiplier`
   - Minimum size: Never go below 10% of base size (safety limit)

3. Integration with existing sizing:
   - Modify `backend/risk/sizing.py` `PositionSizer.calculate_size()`:
     * Add optional `strategy_id` parameter
     * If `ADAPTIVE_SIZING_ENABLED=true` and `strategy_id` provided:
       - Call `AdaptivePositionSizer.calculate_adaptive_size()`
       - Use adjusted size instead of base size
   - Maintain backward compatibility: If strategy_id not provided, use base sizing

4. Integration with execution:
   - Modify `backend/execution/executor.py` `execute_trade()`:
     * Pass `strategy_id` from `TradeIntent.strategy_id` to `PositionSizer`
     * Position sizing will automatically use adaptive sizing if enabled

5. Environment variables (.env):
   - `ADAPTIVE_SIZING_ENABLED=true` - Enable dynamic position sizing
   - `TARGET_WIN_RATE=0.55` - Target win rate for sizing (55%)
   - `MAX_SIZE_MULTIPLIER=1.5` - Maximum size increase for well-performing strategies

6. Logging:
   - Log size adjustments: "Strategy {id}: Adjusted position size ${base:.2f} -> ${adjusted:.2f} (multiplier={mult:.2f}x, win_rate={wr}%)"
   - Log when adaptive sizing skipped (insufficient trades, disabled, etc.)

Files to create:
- `backend/risk/adaptive_sizing.py` - Adaptive sizing logic

Files to modify:
- `backend/risk/sizing.py` - Integrate adaptive sizing
- `backend/execution/executor.py` - Pass strategy_id to PositionSizer
- `.env` - Add adaptive sizing configuration

Do not modify:
- Core risk management (2% rule still applies to base size)
- Position tracking
- Trade execution flow

Acceptance criteria:
- Position sizes adjust based on strategy win rate
- Underperforming strategies get smaller positions
- Well-performing strategies can increase size (capped at 1.5x)
- Minimum size safety limit enforced
- All adjustments logged
- Backward compatible (works without strategy_id)
```

---

### Ticket 5: Performance-Based Strategy Prioritization
**Agent:** `backend-execute`  
**Ticket:** `TICKET-505: Implement performance-based strategy prioritization in screener`  
**Branch:** `backend/strategy-prioritization`

**Prompt:**
```
Implement performance-based strategy prioritization so better-performing strategies are evaluated first.

Requirements:
1. Modify `backend/screener/service.py`:
   - Update `_load_enabled_strategies()` or create `_load_and_prioritize_strategies()`:
     * Load all active strategies
     * For each strategy, read performance metrics from Redis
     * Sort strategies by performance score:
       - Primary: win_rate (descending)
       - Secondary: total_pnl (descending)
       - Tertiary: total_trades (ascending - prefer strategies with more data)
     * Return sorted list

2. Performance score calculation:
   - Create helper function `_calculate_performance_score(strategy: Strategy) -> float`:
     * Read metrics from Redis: `performance:strategy:{strategy_id}`
     * If no metrics: score = 0.0 (new strategies evaluated last)
     * Score = (win_rate * 0.7) + (normalized_pnl * 0.3)
     * Normalized P&L: `min(1.0, total_pnl / 100.0)` (cap at $100 for normalization)

3. Integration:
   - Use prioritized strategy list in `_run_strategy_scans()`:
     * Strategies evaluated in priority order
     * Better-performing strategies get first chance at signals
     * Log: "Evaluating strategies in priority order: [Strategy X (60%), Strategy Y (45%), ...]"

4. Optional: Limit evaluation:
   - If too many strategies, only evaluate top N (e.g., top 5 by performance)
   - This focuses resources on best performers
   - Configurable via `MAX_STRATEGIES_TO_EVALUATE` (default: all)

5. Environment variables (.env):
   - `PERFORMANCE_PRIORITIZATION_ENABLED=true` - Enable prioritization
   - `MAX_STRATEGIES_TO_EVALUATE=0` - 0 = evaluate all, N = evaluate top N

6. Logging:
   - Log strategy evaluation order
   - Log when strategies skipped due to limit

Files to modify:
- `backend/screener/service.py` - Add prioritization logic
- `.env` - Add prioritization configuration

Do not modify:
- Strategy evaluation logic
- Signal generation
- Core screener functionality

Acceptance criteria:
- Strategies evaluated in performance order (best first)
- Performance score calculated correctly
- Evaluation order logged
- System works with or without performance data
- Optional limit on number of strategies evaluated
```

---

### Ticket 6: Profitability Consistency and Daily Targets
**Agent:** `backend-execute`  
**Ticket:** `TICKET-506: Implement daily profitability targets and consistency logic`  
**Branch:** `backend/profitability-consistency`

**Prompt:**
```
Implement daily profitability targets and consistency logic to maintain steady small profits.

Requirements:
1. Create `backend/performance/profitability.py`:
   - Class `ProfitabilityManager` that tracks daily P&L and adjusts system behavior
   - Tracks: `daily_pnl`, `daily_target`, `consecutive_profitable_days`, `consecutive_losing_days`

2. Daily P&L tracking:
   - Calculate daily P&L from all positions (realized + unrealized)
   - Reset at start of each day (UTC midnight)
   - Store in Redis: `performance:daily_pnl:{date}` (format: YYYY-MM-DD)

3. Profitability logic:
   - If `daily_pnl >= PROFITABILITY_TARGET_USD` (default $1.00):
     * Enter "cooldown mode": Reduce trading frequency
     * Increase confidence thresholds by 10% (tighter filters)
     * Log: "Daily target reached ($1.20), entering cooldown mode"
   - If `daily_pnl < 0` (losing day):
     * Tighten filters further: Increase confidence thresholds by 15%
     * Reduce position sizes globally by 20%
     * Log: "Daily loss detected (-$2.50), tightening filters"
   - If `daily_pnl` between 0 and target:
     * Normal operation
     * Gradually relax filters if consistently profitable

4. Consistency tracking:
   - Track consecutive profitable/losing days
   - If 3+ consecutive profitable days:
     * System is working well, maintain current settings
   - If 3+ consecutive losing days:
     * Emergency mode: Pause all trading, require manual review
     * Log alert: "3 consecutive losing days detected, pausing all trading"

5. Integration:
   - Call `ProfitabilityManager.update_daily_metrics()` from `PerformanceMonitor`
   - Update frequency: Every 5 minutes
   - Apply profitability adjustments to all strategies globally

6. Environment variables (.env):
   - `PROFITABILITY_TARGET_USD=1.0` - Daily profit target in USD
   - `PROFITABILITY_COOLDOWN_ENABLED=true` - Enable cooldown when target reached
   - `MAX_CONSECUTIVE_LOSSES=3` - Maximum consecutive losing days before emergency pause

7. Logging and alerts:
   - Log daily P&L summary
   - Log when cooldown mode activated
   - Log when filters tightened due to losses
   - Alert when emergency pause triggered

Files to create:
- `backend/performance/profitability.py` - Profitability management

Files to modify:
- `backend/performance/monitor.py` - Integrate profitability tracking
- `backend/screener/service.py` - Apply global profitability adjustments
- `.env` - Add profitability configuration

Do not modify:
- Core trading execution
- Strategy evaluation
- Position tracking

Acceptance criteria:
- Daily P&L tracked and reset daily
- Cooldown mode activates when target reached
- Filters tighten when losing
- Emergency pause triggers after consecutive losses
- All actions logged clearly
- System maintains consistent small profits
```

---

## Execution Order

1. **TICKET-501** (Performance Monitoring) - **CRITICAL PATH** - Must establish real-time metrics first
2. **TICKET-502** (Adaptive Thresholds) - **HIGH PRIORITY** - Can start after TICKET-501
3. **TICKET-503** (Auto-Disable) - **HIGH PRIORITY** - Depends on TICKET-501
4. **TICKET-504** (Dynamic Sizing) - **MEDIUM PRIORITY** - Depends on TICKET-501
5. **TICKET-505** (Prioritization) - **MEDIUM PRIORITY** - Can run after TICKET-501
6. **TICKET-506** (Profitability Consistency) - **MEDIUM PRIORITY** - Final optimization

## Expected Outcomes

After implementing all tickets:
- **Self-Improving System:** Strategies automatically adjust based on performance
- **Consistent Profitability:** System maintains steady small profits ($1-5/day)
- **Auto-Disable:** Underperforming strategies paused automatically
- **Adaptive Filters:** Confidence thresholds adjust to maintain target win rate
- **Dynamic Sizing:** Position sizes adapt to performance
- **Performance Focus:** Better-performing strategies prioritized

## Success Metrics

- **Win Rate:** Improves from 0% to >50% within 2 weeks
- **Daily Profitability:** Consistent $1-5/day profit (even if small)
- **Strategy Health:** Underperforming strategies auto-disabled
- **Adaptation Speed:** System adjusts within 10-20 trades
- **Consistency:** 5+ consecutive profitable days

## Risk Mitigation

- **Gradual Rollout:** Enable adaptation for one strategy first, then expand
- **Safety Limits:** Minimum position sizes, maximum threshold adjustments
- **Manual Override:** Ability to disable adaptation if needed
- **Monitoring:** Extensive logging of all adaptations
- **Fallback:** System falls back to static config if adaptation fails
- **Emergency Stop:** Automatic pause after consecutive losses
