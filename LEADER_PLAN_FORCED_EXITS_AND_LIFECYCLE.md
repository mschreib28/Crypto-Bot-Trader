# Leader Plan: Forced Exit Logic and Trade Lifecycle Management

## Problem Statement

QA team identified a critical issue after 24h shadow mode run:
- **Dead Position**: SCRT position held for 24h with -88% P&L, never exited
- **Root Cause**: Missing forced exit logic (time stops, max hold, invalidation exits)
- **Risk**: "I sized small, so it's okay to be wrong forever" - this logic does not scale

**Current State:**
- Stop-loss orders are placed but may not trigger (Kraken stop-loss orders can fail)
- No time-based exit logic (max hold duration)
- No structural invalidation exits (VWAP invalidation, RSI failure, HTF regime flip)
- No strategy-level drawdown tracking or disabling
- No R-multiples per trade tracking

**Required State:**
- All positions must exit within max hold duration (time stop)
- Structural invalidation exits when trade setup invalidates
- Strategy-level drawdown tracking with auto-disable
- R-multiples per trade for performance analysis

---

## 1. Scope

### In Scope

**Phase 1: Forced Exit Logic (CRITICAL)**
- Time-based max hold exits (per-strategy configurable)
- Structural invalidation exits (VWAP invalidation, RSI failure, HTF regime flip)
- Exit logging with explicit reason (`EXIT_FORCED` activity type)
- Integration with PositionMonitor service

**Phase 2: Strategy Lifecycle Management**
- R-multiples per trade tracking
- Per-strategy drawdown tracking (rolling window)
- Strategy auto-disable after -X R in rolling window
- Strategy-level performance metrics

**Phase 3: Exit Reason Tracking**
- Track exit reason per position (stop-loss, take-profit, time-stop, invalidation)
- Store in position history/metrics
- Display in UI for analysis

### Out of Scope
- Changing database schemas (use existing Position model with `entry_time`)
- Modifying API contracts (add new activity types only)
- Frontend changes (Phase 1 - backend only)
- Strategy logic changes (exit logic is execution-level, not strategy-level)

---

## 2. File Ownership

### Backend Core (backend/)
- `backend/positions/monitor.py` - **EXTEND**: Add forced exit checking logic
- `backend/positions/models.py` - **VERIFY**: Position model has `entry_time`, `opened_by_strategy_id`
- `backend/positions/tracker.py` - **EXTEND**: Add forced exit execution methods
- `backend/risk/metrics.py` - **EXTEND**: Add R-multiples tracking, strategy drawdown tracking
- `backend/api/routes/events.py` - **EXTEND**: Add `EXIT_FORCED` activity type
- `backend/db/models.py` - **VERIFY**: Strategy model supports status updates

### Strategy Configuration (backend/db/)
- `backend/db/seeds/strategies.sql` - **EXTEND**: Add `max_hold_candles` config per strategy
- Strategy config schema - **EXTEND**: Add max hold and invalidation exit parameters

### Research (research/) - **OUT OF SCOPE**
- No changes to research code (per quant-research scope)

---

## 3. Contracts Impacted

### API Contracts
- **Activity Log**: Add new activity type `EXIT_FORCED`
  - Message format: `"Forced exit: {symbol} [{strategy}] - {reason} (hold: {candles} candles, P&L: {pnl_pct}%)"`
  - Details: `{reason, candles_held, pnl_pct, exit_price, entry_price, strategy_id}`

### Redis Keys
- **NEW**: `strategy:max_hold:{strategy_id}` - Max hold duration per strategy (optional, defaults in code)
- **NEW**: `strategy:drawdown:{strategy_id}` - Rolling drawdown tracking
- **EXISTING**: `position:{symbol}` - Already has `entry_time`, `opened_by_strategy_id`

### Database Schema
- **NO CHANGES**: Use existing `positions` table with `entry_time` field
- **NO CHANGES**: Use existing `strategies` table with `status` field

### Shared Types
- **EXTEND**: `ActivityType` union in frontend to include `EXIT_FORCED`
- **NO CHANGES**: Position model already has required fields

---

## 4. Acceptance Criteria

### Phase 1: Forced Exit Logic

**AC1: Time-Based Max Hold Exit**
- [ ] Position held longer than `max_hold_candles` is automatically exited
- [ ] Max hold configurable per strategy (defaults: VWAP MR 6 candles, Vol Breakout 4 candles, HTF Pullback 3 HTF candles)
- [ ] Exit logged as `EXIT_FORCED` with reason `max_hold`
- [ ] Works in both shadow and live mode
- [ ] Exit happens within 1 candle of max hold duration

**AC2: Structural Invalidation Exit**
- [ ] Position exited if price closes N ATR away from VWAP (mean reversion invalidated)
- [ ] Position exited if RSI fails to mean-revert after M candles
- [ ] Position exited if HTF regime flips against trade direction
- [ ] Exit logged as `EXIT_FORCED` with reason `invalidation_{type}`
- [ ] Invalidation thresholds configurable per strategy

**AC3: Exit Execution**
- [ ] Forced exits create `TradeIntent` with `side="sell"`, `intent_type="exit"`
- [ ] Exit uses actual position quantity (not calculated)
- [ ] Exit respects strategy ownership (only strategy that opened can force exit)
- [ ] Exit cancels any existing stop-loss orders before selling
- [ ] Exit logged to activity feed with full details

**AC4: Integration**
- [ ] Forced exit checks run every position monitor cycle (10s default)
- [ ] Checks run after price updates (so `current_price` is fresh)
- [ ] No duplicate exits (check if position already closed)
- [ ] Handles execution errors gracefully (log, don't crash)

### Phase 2: Strategy Lifecycle Management

**AC5: R-Multiples Tracking**
- [ ] R-multiple calculated per trade: `(exit_price - entry_price) / (entry_price - stop_loss_price)`
- [ ] R-multiples stored in strategy metrics
- [ ] Rolling window of R-multiples (last N trades)
- [ ] Average R-multiple per strategy tracked

**AC6: Strategy Drawdown Tracking**
- [ ] Per-strategy drawdown calculated: `(peak_equity - current_equity) / peak_equity`
- [ ] Rolling window drawdown (last N trades or last M days)
- [ ] Drawdown stored in Redis with TTL
- [ ] Drawdown resets when new peak reached

**AC7: Strategy Auto-Disable**
- [ ] Strategy automatically disabled when drawdown exceeds threshold (e.g., -5 R)
- [ ] Disable logged to activity feed
- [ ] Strategy status updated in database (`status='paused'`)
- [ ] Disabled strategy does not generate new signals
- [ ] Manual re-enable required (no auto-recovery)

### Phase 3: Exit Reason Tracking

**AC8: Exit Reason Storage**
- [ ] Exit reason stored in metrics when position closes
- [ ] Exit reasons: `stop_loss`, `take_profit`, `time_stop`, `invalidation_vwap`, `invalidation_rsi`, `invalidation_htf`, `manual`
- [ ] Exit reasons queryable via metrics API
- [ ] Exit reasons displayed in UI (future work)

---

## 5. Dependencies

### Phase 1 Dependencies
- **TICKET-301** (Time-Based Max Hold) - **NO DEPENDENCIES** - Can start immediately
- **TICKET-302** (Structural Invalidation) - **DEPENDS ON**: TICKET-301 (shared exit execution logic)
- **TICKET-303** (Exit Execution Integration) - **DEPENDS ON**: TICKET-301, TICKET-302

### Phase 2 Dependencies
- **TICKET-304** (R-Multiples Tracking) - **DEPENDS ON**: TICKET-303 (needs exit execution)
- **TICKET-305** (Strategy Drawdown) - **DEPENDS ON**: TICKET-304 (needs R-multiples)
- **TICKET-306** (Strategy Auto-Disable) - **DEPENDS ON**: TICKET-305 (needs drawdown tracking)

### Phase 3 Dependencies
- **TICKET-307** (Exit Reason Tracking) - **DEPENDS ON**: TICKET-303 (needs exit execution)

### External Dependencies
- PositionMonitor service must be running (already running)
- Position model must have `entry_time` and `opened_by_strategy_id` (already exists)
- Strategy config must support max hold parameters (needs extension)

---

## 6. Agent Launch Instructions

### Ticket 1: Time-Based Max Hold Exit Logic

**Agent:** `quant-research`  
**Ticket:** `TICKET-301: Implement time-based max hold forced exit logic`  
**Branch:** `feature/time-based-max-hold-exit`

**Prompt:**
```
Implement time-based max hold forced exit logic in PositionMonitor to automatically exit positions that have been held longer than the maximum allowed duration.

Requirements:
1. Extend `PositionMonitor` in `backend/positions/monitor.py`:
   - Add `_check_max_hold_exits()` method
   - For each position with `opened_by_strategy_id` set:
     - Calculate candles held: `(current_time - entry_time) / strategy_interval`
     - Get max hold limit from strategy config: `config.get("max_hold_candles")` or use defaults:
       - VWAP Mean Reversion: 6 candles (30 min for 5m interval)
       - Volatility Breakout: 4 candles (20 min for 5m interval)
       - HTF Trend Pullback: 3 HTF candles (3h for 1h interval)
     - If candles_held >= max_hold_candles:
       - Log: "Max hold exceeded: {symbol} held {candles_held} candles (limit: {max_hold_candles})"
       - Call `_force_exit_position()` with reason="max_hold"

2. Add `_force_exit_position()` helper method:
   - Create `TradeIntent` with `side="sell"`, `intent_type="exit"`
   - Use `position.opened_by_strategy_id` as strategy_id
   - Use `position.quantity` as quantity
   - Call `execute_trade()` to sell position
   - Log `EXIT_FORCED` activity with details:
     - reason: "max_hold"
     - candles_held: calculated value
     - pnl_pct: (current_price - entry_price) / entry_price * 100
     - exit_price: current_price
     - entry_price: position.entry_price

3. Integration:
   - Call `_check_max_hold_exits()` in `_update_all_positions()` after price updates
   - Run every position monitor cycle (10s default)
   - Only check positions with `opened_by_strategy_id` set

4. Edge cases:
   - If position already closed, skip
   - If strategy config missing, use safe defaults (6 candles for 5m, 3 HTF candles for 1h+)
   - Handle execution errors gracefully (log, don't crash)
   - Ensure only one exit attempt per position (check if position still exists after execution)

Files to modify:
- `backend/positions/monitor.py` - Add max hold checking and forced exit logic
- `backend/api/routes/events.py` - Add `EXIT_FORCED` activity type support
- `backend/positions/tracker.py` - Verify `get_position()` and position closing works

Do not modify:
- Position model (already has required fields)
- Strategy model (config already supports JSONB)
- Execution logic (use existing `execute_trade()`)

Acceptance criteria:
- Positions held longer than max hold are automatically exited
- Exit logged as `EXIT_FORCED` with reason `max_hold`
- Works in both shadow and live mode
- Max hold configurable per strategy
- No duplicate exits
```

---

### Ticket 2: Structural Invalidation Exit Logic

**Agent:** `quant-research`  
**Ticket:** `TICKET-302: Implement structural invalidation forced exit logic`  
**Branch:** `feature/structural-invalidation-exit`

**Prompt:**
```
Implement structural invalidation forced exit logic to exit positions when the trade setup invalidates (VWAP deviation, RSI failure, HTF regime flip).

Requirements:
1. Extend `PositionMonitor` in `backend/positions/monitor.py`:
   - Add `_check_invalidation_exits()` method
   - For each position with `opened_by_strategy_id` set:
     - Get strategy config to determine invalidation rules
     - Check invalidation conditions based on strategy type:
       
       **VWAP Mean Reversion:**
       - Fetch current VWAP and ATR from market data (or use cached indicators)
       - If price closes > N ATR away from VWAP (default: 2.0 ATR), exit with reason="invalidation_vwap"
       - Config key: `config.get("invalidation_vwap_atr_mult", 2.0)`
       
       **RSI Mean Reversion:**
       - Fetch current RSI (or use cached)
       - If RSI fails to mean-revert after M candles (default: 4 candles), exit with reason="invalidation_rsi"
       - Config key: `config.get("invalidation_rsi_candles", 4)`
       - Check: RSI still oversold/overbought after M candles from entry
       
       **HTF Trend Pullback:**
       - Fetch HTF trend direction (or use cached)
       - If HTF regime flips against trade direction, exit with reason="invalidation_htf"
       - Config key: `config.get("invalidation_htf_regime_flip", True)`
       - Check: HTF trend changed from bullish to bearish (for long) or vice versa

2. Integration:
   - Call `_check_invalidation_exits()` in `_update_all_positions()` after price updates
   - Run every position monitor cycle (10s default)
   - Only check positions with `opened_by_strategy_id` set
   - Use same `_force_exit_position()` helper from TICKET-301

3. Data requirements:
   - Need access to current indicators (VWAP, ATR, RSI, HTF trend)
   - Can fetch from Redis screener results or calculate on-demand
   - Cache indicators to avoid excessive API calls

4. Edge cases:
   - If indicators unavailable, skip invalidation check (don't exit)
   - If strategy type unknown, skip invalidation check
   - Handle execution errors gracefully

Files to modify:
- `backend/positions/monitor.py` - Add invalidation checking logic
- `backend/screener/service.py` - May need to expose indicator calculation helpers (if needed)

Do not modify:
- Strategy evaluation logic (invalidation is exit-level, not entry-level)
- Position model (already has required fields)

Acceptance criteria:
- Positions exit when VWAP invalidates (price > N ATR from VWAP)
- Positions exit when RSI fails to mean-revert after M candles
- Positions exit when HTF regime flips against trade
- Exit logged as `EXIT_FORCED` with reason `invalidation_{type}`
- Invalidation thresholds configurable per strategy
- Works in both shadow and live mode
```

---

### Ticket 3: Exit Execution Integration and Activity Logging

**Agent:** `quant-research`  
**Ticket:** `TICKET-303: Integrate forced exit execution with activity logging`  
**Branch:** `feature/forced-exit-integration`

**Prompt:**
```
Integrate forced exit execution logic with activity logging and ensure proper exit reason tracking.

Requirements:
1. Extend `_force_exit_position()` in `backend/positions/monitor.py`:
   - Ensure it handles both time-based and invalidation exits
   - Log `EXIT_FORCED` activity with complete details:
     - activity_type: "EXIT_FORCED"
     - message: "Forced exit: {symbol} [{strategy_name}] - {reason} (hold: {candles_held} candles, P&L: {pnl_pct:.1f}%)"
     - details: {
         "symbol": symbol,
         "strategy": strategy_name,
         "strategy_id": strategy_id,
         "reason": reason,  # "max_hold", "invalidation_vwap", "invalidation_rsi", "invalidation_htf"
         "candles_held": candles_held,
         "pnl_pct": pnl_pct,
         "exit_price": current_price,
         "entry_price": entry_price,
         "entry_time": entry_time,
         "unrealized_pnl": unrealized_pnl,
         "mode": "shadow_live" or "live"
       }

2. Update `backend/api/routes/events.py`:
   - Ensure `EXIT_FORCED` activity type is supported
   - No changes needed if activity_type is already string-based

3. Update frontend `ActivityType`:
   - Add `EXIT_FORCED` to `ActivityType` union in `frontend/src/hooks/useActivity.ts`
   - Add color coding in `frontend/src/components/ActivityLog.tsx`:
     - `EXIT_FORCED` → `text-red-400` (red for forced exits)

4. Integration:
   - Ensure `_force_exit_position()` is called from both `_check_max_hold_exits()` and `_check_invalidation_exits()`
   - Exit execution must cancel stop-loss orders before selling (use existing logic from `execute_trade()`)
   - Exit execution must respect strategy ownership

5. Testing:
   - Verify forced exits log correctly in shadow mode
   - Verify forced exits execute correctly in live mode
   - Verify exit reasons are distinct and accurate

Files to modify:
- `backend/positions/monitor.py` - Complete `_force_exit_position()` implementation
- `frontend/src/hooks/useActivity.ts` - Add `EXIT_FORCED` type
- `frontend/src/components/ActivityLog.tsx` - Add color coding for `EXIT_FORCED`

Do not modify:
- Execution logic (use existing `execute_trade()`)
- Position model (already has required fields)

Acceptance criteria:
- Forced exits log `EXIT_FORCED` activity with complete details
- Exit reasons are distinct and accurate
- Frontend displays forced exits with red color
- Works in both shadow and live mode
- Exit execution cancels stop-loss orders before selling
```

---

### Ticket 4: R-Multiples Per Trade Tracking

**Agent:** `quant-research`  
**Ticket:** `TICKET-304: Track R-multiples per trade for performance analysis`  
**Branch:** `feature/r-multiples-tracking`

**Prompt:**
```
Implement R-multiples tracking per trade to measure trade performance in risk-adjusted terms.

Requirements:
1. Extend `StrategyMetrics` in `backend/risk/metrics.py`:
   - Add `record_trade_exit()` method:
     - Calculate R-multiple: `(exit_price - entry_price) / (entry_price - stop_loss_price)`
     - For long positions: `(exit_price - entry_price) / (entry_price - stop_loss_price)`
     - For short positions: `(exit_price - entry_price) / (stop_loss_price - entry_price)` (inverted)
     - Store R-multiple with trade record
     - Update rolling window of R-multiples (last N trades, default: 20)
   
   - Add `get_r_multiples()` method:
     - Returns list of R-multiples for last N trades
     - Returns average R-multiple
     - Returns win rate (R > 0)
     - Returns average win R, average loss R

2. Integration:
   - Call `record_trade_exit()` when position closes (in `PositionTracker.record_fill()`)
   - Pass exit_price, entry_price, stop_loss_price from position
   - Store R-multiple in Redis: `strategy:r_multiples:{strategy_id}` (list, max 20 entries)

3. Data storage:
   - Store R-multiples as list in Redis (LPUSH, LTRIM to keep last 20)
   - Each entry: JSON with `{r_multiple, exit_price, entry_price, exit_time, exit_reason}`

4. Edge cases:
   - If stop_loss_price missing, use default 5% stop (calculate from entry_price)
   - If exit_price == entry_price, R-multiple = 0
   - Handle division by zero (if stop_loss_price == entry_price, use default)

Files to modify:
- `backend/risk/metrics.py` - Add R-multiples tracking methods
- `backend/positions/tracker.py` - Call `record_trade_exit()` when position closes
- `backend/redis/keys.py` - Add `STRATEGY_R_MULTIPLES_KEY` constant

Do not modify:
- Position model (already has required fields)
- Trade execution logic (R-multiples are calculated post-exit)

Acceptance criteria:
- R-multiples calculated correctly for all trades
- R-multiples stored in Redis with rolling window (last 20 trades)
- Average R-multiple queryable per strategy
- Win rate and average win/loss R queryable
- Works for both shadow and live trades
```

---

### Ticket 5: Per-Strategy Drawdown Tracking

**Agent:** `quant-research`  
**Ticket:** `TICKET-305: Track per-strategy drawdown with rolling window`  
**Branch:** `feature/strategy-drawdown-tracking`

**Prompt:**
```
Implement per-strategy drawdown tracking to monitor strategy performance degradation.

Requirements:
1. Extend `StrategyMetrics` in `backend/risk/metrics.py`:
   - Add `update_strategy_equity()` method:
     - Track peak equity per strategy: `strategy:peak_equity:{strategy_id}`
     - Track current equity per strategy: `strategy:current_equity:{strategy_id}`
     - Calculate drawdown: `(peak_equity - current_equity) / peak_equity * 100`
     - Update peak if current_equity > peak_equity
     - Store drawdown: `strategy:drawdown:{strategy_id}` (float, percentage)
   
   - Add `get_strategy_drawdown()` method:
     - Returns current drawdown percentage
     - Returns peak equity
     - Returns current equity
     - Returns drawdown duration (time since peak)

2. Integration:
   - Call `update_strategy_equity()` when position P&L updates (in `PositionMonitor._update_all_positions()`)
   - Calculate strategy equity: sum of unrealized P&L for all positions opened by strategy
   - Update drawdown after each position P&L update

3. Rolling window:
   - Track drawdown over rolling window (last N trades or last M days)
   - Store drawdown history: `strategy:drawdown_history:{strategy_id}` (list, max 100 entries)
   - Each entry: `{drawdown_pct, timestamp, equity}`

4. Edge cases:
   - If no positions, drawdown = 0
   - If peak_equity == 0, drawdown = 0
   - Handle negative equity gracefully

Files to modify:
- `backend/risk/metrics.py` - Add drawdown tracking methods
- `backend/positions/monitor.py` - Call `update_strategy_equity()` after P&L updates
- `backend/redis/keys.py` - Add drawdown-related keys

Do not modify:
- Position model (already has required fields)
- Strategy model (drawdown is calculated, not stored)

Acceptance criteria:
- Drawdown calculated correctly per strategy
- Drawdown updates when position P&L changes
- Peak equity tracked correctly
- Drawdown history stored with rolling window
- Drawdown queryable via metrics API
```

---

### Ticket 6: Strategy Auto-Disable on Drawdown

**Agent:** `quant-research`  
**Ticket:** `TICKET-306: Auto-disable strategy when drawdown exceeds threshold`  
**Branch:** `feature/strategy-auto-disable-drawdown`

**Prompt:**
```
Implement automatic strategy disabling when drawdown exceeds threshold (e.g., -5 R in rolling window).

Requirements:
1. Extend `StrategyMetrics` in `backend/risk/metrics.py`:
   - Add `check_strategy_drawdown()` method:
     - Get current drawdown from `get_strategy_drawdown()`
     - Get rolling R-multiples from `get_r_multiples()` (last N trades)
     - Calculate cumulative R loss: sum of negative R-multiples in rolling window
     - If cumulative R loss <= threshold (default: -5 R), disable strategy:
       - Update strategy status in database: `status='paused'`
       - Log activity: `EXIT_FORCED` with reason="strategy_disabled_drawdown"
       - Store disable reason: `strategy:disable_reason:{strategy_id}`

2. Integration:
   - Call `check_strategy_drawdown()` after drawdown updates (in `update_strategy_equity()`)
   - Run check every position monitor cycle (10s default)
   - Only check active strategies (`status='active'`)

3. Strategy disable behavior:
   - Disabled strategy does not generate new signals (checked in screener)
   - Disabled strategy does not execute new trades
   - Manual re-enable required (no auto-recovery)
   - Disable reason stored for audit trail

4. Configuration:
   - Drawdown threshold configurable per strategy: `config.get("max_drawdown_r", -5.0)`
   - Rolling window size configurable: `config.get("drawdown_window_trades", 20)`

5. Edge cases:
   - If strategy already disabled, skip check
   - If no trades in rolling window, skip check
   - Handle database update errors gracefully

Files to modify:
- `backend/risk/metrics.py` - Add strategy disable checking
- `backend/db/models.py` - Verify strategy status update works
- `backend/screener/service.py` - Skip signal generation for paused strategies
- `backend/api/routes/events.py` - Log strategy disable activity

Do not modify:
- Strategy model (already has status field)
- Execution logic (screener already filters by status)

Acceptance criteria:
- Strategy automatically disabled when drawdown exceeds threshold
- Disable logged to activity feed
- Strategy status updated in database
- Disabled strategy does not generate signals
- Manual re-enable required
- Threshold configurable per strategy
```

---

### Ticket 7: Exit Reason Tracking in Metrics

**Agent:** `quant-research`  
**Ticket:** `TICKET-307: Track exit reasons per trade for analysis`  
**Branch:** `feature/exit-reason-tracking`

**Prompt:**
```
Implement exit reason tracking per trade to analyze why positions exited (stop-loss, take-profit, time-stop, invalidation, manual).

Requirements:
1. Extend `StrategyMetrics` in `backend/risk/metrics.py`:
   - Add `exit_reason` parameter to `record_trade_exit()`:
     - Exit reasons: `stop_loss`, `take_profit`, `time_stop`, `invalidation_vwap`, `invalidation_rsi`, `invalidation_htf`, `manual`, `unknown`
     - Store exit reason with trade record
     - Store in R-multiples list: `{r_multiple, exit_price, entry_price, exit_time, exit_reason}`

2. Integration:
   - Pass exit reason when calling `record_trade_exit()`:
     - From forced exits: use reason from `EXIT_FORCED` activity
     - From stop-loss: use reason="stop_loss"
     - From take-profit: use reason="take_profit"
     - From manual sell: use reason="manual"
     - Default: reason="unknown"

3. Query methods:
   - Add `get_exit_reason_stats()` method:
     - Returns count per exit reason for last N trades
     - Returns percentage breakdown
     - Returns average R-multiple per exit reason

4. Data storage:
   - Exit reason stored in R-multiples list (already implemented)
   - Exit reason queryable via metrics API

Files to modify:
- `backend/risk/metrics.py` - Add exit_reason parameter to `record_trade_exit()`
- `backend/positions/monitor.py` - Pass exit reason when recording trade exit
- `backend/positions/tracker.py` - Pass exit reason when position closes

Do not modify:
- Position model (exit reason stored in metrics, not position)
- Trade execution logic (exit reason determined post-execution)

Acceptance criteria:
- Exit reasons tracked for all trades
- Exit reasons stored with R-multiples
- Exit reason stats queryable per strategy
- Exit reason breakdown available for analysis
```

---

## 7. Execution Order

### Phase 1: Forced Exit Logic (CRITICAL PATH)
1. **TICKET-301** (Time-Based Max Hold) - **START IMMEDIATELY** - No dependencies
2. **TICKET-302** (Structural Invalidation) - **AFTER TICKET-301** - Shares exit execution logic
3. **TICKET-303** (Exit Integration) - **AFTER TICKET-301, TICKET-302** - Completes Phase 1

### Phase 2: Strategy Lifecycle Management
4. **TICKET-304** (R-Multiples) - **AFTER TICKET-303** - Needs exit execution
5. **TICKET-305** (Strategy Drawdown) - **AFTER TICKET-304** - Needs R-multiples
6. **TICKET-306** (Strategy Auto-Disable) - **AFTER TICKET-305** - Needs drawdown tracking

### Phase 3: Exit Reason Tracking
7. **TICKET-307** (Exit Reason Tracking) - **AFTER TICKET-303** - Needs exit execution

---

## 8. Testing Strategy

### Shadow Mode Validation (24-48h)
After Phase 1 implementation:
- [ ] Run shadow mode for 24-48h
- [ ] Verify no positions held longer than max hold duration
- [ ] Verify invalidation exits trigger correctly
- [ ] Verify exit reasons logged correctly
- [ ] Verify forced exits work in shadow mode (no real orders)

### Live Mode Validation (Single Strategy Probe)
After Phase 1 + Phase 2 implementation:
- [ ] Enable single strategy in live mode
- [ ] Monitor for 24h
- [ ] Verify forced exits execute correctly
- [ ] Verify strategy auto-disable works if drawdown exceeds threshold
- [ ] Verify R-multiples tracked correctly

---

## 9. Success Criteria

**Phase 1 Complete:**
- ✅ No positions held longer than max hold duration
- ✅ Positions exit when trade setup invalidates
- ✅ All exits logged with explicit reason
- ✅ Works in both shadow and live mode

**Phase 2 Complete:**
- ✅ R-multiples tracked per trade
- ✅ Strategy drawdown tracked with rolling window
- ✅ Strategy auto-disables when drawdown exceeds threshold

**Phase 3 Complete:**
- ✅ Exit reasons tracked for all trades
- ✅ Exit reason stats queryable per strategy

**Final Validation:**
- ✅ 24-48h shadow run shows: some losers, losers exit, flat equity with small variance
- ✅ Ready for single-strategy live probe

---

## 10. Risk Mitigation

**Risk 1: Forced exits execute incorrectly**
- Mitigation: Test extensively in shadow mode first
- Mitigation: Log all forced exit attempts with full details
- Mitigation: Respect strategy ownership (only opening strategy can force exit)

**Risk 2: Invalidation exits trigger too early**
- Mitigation: Make invalidation thresholds configurable per strategy
- Mitigation: Use conservative defaults (2.0 ATR for VWAP, 4 candles for RSI)
- Mitigation: Test with historical data before live

**Risk 3: Strategy auto-disable triggers incorrectly**
- Mitigation: Use rolling window (not single trade)
- Mitigation: Make threshold configurable per strategy
- Mitigation: Manual re-enable required (no auto-recovery)

**Risk 4: Performance impact of forced exit checks**
- Mitigation: Run checks every position monitor cycle (10s, already running)
- Mitigation: Cache indicators to avoid excessive API calls
- Mitigation: Skip checks if indicators unavailable (don't exit)

---

## 11. Documentation Updates

After implementation:
- Update `OMNI_BOT_WEBAPP_DOCUMENTATION.md`:
  - Add "Forced Exit Logic" section
  - Document max hold defaults per strategy
  - Document invalidation exit conditions
  - Document strategy auto-disable behavior
  - Document R-multiples tracking
  - Document exit reason tracking

---

## 12. Notes

**Why This Matters:**
- Current system: "I sized small, so it's okay to be wrong forever"
- Required system: "Every trade has a maximum hold duration and invalidation conditions"
- This prevents dead positions like SCRT (-88%) from persisting indefinitely

**Why Time-Based Stops Are Critical:**
- Crypto has no halts, no liquidity guarantees, no mean reversion obligation
- Mean reversion without hard exits will eventually find an asset that never comes back
- Time stops prevent "hope trading" - if setup doesn't work within X candles, exit

**Why Strategy-Level Drawdown Matters:**
- Separates "strategy loss" from "risk loss"
- Allows disabling underperforming strategies automatically
- Prevents death-by-a-thousand-cuts when scaling equity/confidence/trade count
