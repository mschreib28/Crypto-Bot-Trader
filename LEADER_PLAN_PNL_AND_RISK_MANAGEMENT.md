# Leader Plan: P&L Calculation, Stop-Loss Monitoring, and Risk Management Fixes

## Problem Statement

The client reports:
1. **P&L Not Loading**: Dashboard shows $0.00 P&L despite losing $8 (20%) in a week
2. **Poor Risk Management**: Lost 20% within a week despite 2% per-trade rule
3. **No Sells**: Bot hasn't sold any positions, even when stop-losses should trigger
4. **Stop-Loss Not Working**: Stop-loss orders placed but not monitored/executed

**Root Causes Identified:**
- Positions never update `unrealized_pnl` or `current_price` fields
- No service monitors stop-loss orders to detect fills
- No service syncs positions with Kraken to detect closed positions
- No take-profit logic exists
- P&L calculation missing entirely

## 1. Scope

### In Scope
- **P&L Calculation Service**: Periodically update positions with current prices and calculate unrealized P&L
- **Stop-Loss Monitoring**: Monitor stop-loss orders on Kraken and detect when they're filled
- **Position Sync Service**: Sync positions with Kraken holdings to detect closed positions
- **Account P&L Calculation**: Calculate total account P&L from initial equity vs current equity
- **Take-Profit Logic**: Add configurable take-profit thresholds (e.g., sell when up 10%)
- **Position-Level Risk Monitoring**: Check if positions exceed stop-loss thresholds and auto-sell

### Out of Scope
- Changing database schemas (positions table already has required fields)
- Modifying API contracts (endpoints already return P&L fields)
- Frontend changes (frontend already displays P&L correctly, just needs data)
- Strategy logic changes (focus on execution and risk management)

## 2. File Ownership

### Backend Core (backend/)
- `backend/positions/tracker.py` - Add P&L calculation methods
- `backend/positions/monitor.py` - **NEW**: Service to monitor stop-loss orders and update positions
- `backend/risk/portfolio.py` - Add account-level P&L calculation
- `backend/api/routes/positions.py` - Ensure P&L is included in responses

### Backend Execution (backend/execution/)
- `backend/execution/executor.py` - Verify stop-loss order placement
- `backend/execution/kraken_rest.py` - Add methods to query order status

### Backend API (backend/api/)
- `backend/api/main.py` - Start position monitor service
- `backend/api/routes/account.py` - Add account P&L calculation

### No Changes Required
- Frontend (frontend/) - Already displays P&L correctly
- Research (research/) - Strategy logic unchanged
- Database schemas - Already have required fields

## 3. Contracts Impacted

### API Endpoints (No Breaking Changes)
- `/api/v1/positions` - Will now return accurate `unrealized_pnl` and `current_price`
- `/api/v1/account` - Will now return accurate `total_pnl` and `current_equity`
- `/api/v1/health` - No changes

### Data Models (No Breaking Changes)
- `Position` model - Fields already exist, just need to be populated
- `AccountState` model - Fields already exist, just need to be calculated

### Redis Keys (New Keys)
- `position:monitor:last_check` - Timestamp of last position update
- `position:stop_loss:monitoring` - Set of positions with active stop-loss orders

## 4. Acceptance Criteria

### Critical Path (Must Pass)

1. ✅ **P&L Calculation Works**
   - Positions API returns non-zero `unrealized_pnl` for positions with price changes
   - `current_price` field is populated with latest market price
   - P&L calculation: `unrealized_pnl = (current_price - entry_price) * quantity`
   - Account P&L shows correct loss: `current_equity - initial_equity`

2. ✅ **Stop-Loss Monitoring Works**
   - Service checks Kraken orders every 60 seconds
   - Detects when stop-loss orders are filled
   - Closes positions in tracker when stop-loss triggers
   - Logs stop-loss execution to activity feed

3. ✅ **Position Sync Works**
   - Service syncs positions with Kraken holdings every 5 minutes
   - Detects positions closed on Kraken (quantity = 0)
   - Updates position tracker when positions are closed
   - Handles discrepancies between tracker and Kraken

4. ✅ **Take-Profit Logic Works**
   - Configurable take-profit percentage (default: 10%)
   - Auto-sells positions when profit exceeds threshold
   - Respects strategy ownership (only strategy that opened can close)
   - Logs take-profit sales to activity feed

5. ✅ **Risk Management Improved**
   - Position-level stop-loss monitoring (check if price < stop_loss_price)
   - Auto-sell if stop-loss threshold breached (even if order not filled)
   - Daily loss limit enforced (already exists, verify it works)
   - Account P&L tracked accurately

### Verification (Should Pass)

6. ✅ **Performance**
   - Position updates don't block API requests
   - Monitoring service uses minimal resources
   - Kraken API calls rate-limited appropriately

7. ✅ **Data Accuracy**
   - P&L matches actual account balance changes
   - Positions reflect true holdings on Kraken
   - No duplicate positions or orphaned data

## 5. Dependencies

### Prerequisites
- ✅ Kraken API access (already configured)
- ✅ Redis connection (already working)
- ✅ Database connection (already working)
- ✅ Position tracker (already exists)

### Blocks
- **All trading operations** - Cannot assess risk without accurate P&L
- **Dashboard display** - Frontend cannot show accurate data without P&L calculation
- **Risk management** - Cannot enforce stop-losses without monitoring

## Agent Launch Instructions

### Ticket 1: P&L Calculation Service
**Agent:** `quant-research`  
**Ticket:** `TICKET-201: Implement P&L calculation and position price updates`  
**Branch:** `feature/position-pnl-calculation`

**Prompt:**
```
Implement a service that periodically updates positions with current market prices and calculates unrealized P&L.

Requirements:
1. Create a new method in `backend/positions/tracker.py`:
   - `update_position_pnl(symbol: str, current_price: float) -> None`
   - Calculates: `unrealized_pnl = (current_price - entry_price) * quantity`
   - Updates position in Redis with new `unrealized_pnl` and `current_price`

2. Create a background service in `backend/positions/monitor.py`:
   - `PositionMonitor` class that runs every 60 seconds
   - Fetches current prices for all open positions from Kraken ticker API
   - Calls `update_position_pnl()` for each position
   - Logs updates: "Updated P&L for {symbol}: ${pnl:.2f} (price: ${current_price:.2f})"

3. Integration:
   - Start `PositionMonitor` in `backend/api/main.py` alongside screener service
   - Use asyncio background task (similar to screener service)

4. Error handling:
   - If price fetch fails for a symbol, log warning but continue with other positions
   - If position doesn't exist, skip it
   - Rate limit Kraken API calls (max 1 per second)

Files to create/modify:
- `backend/positions/monitor.py` - NEW: Position monitoring service
- `backend/positions/tracker.py` - Add `update_position_pnl()` method
- `backend/api/main.py` - Start PositionMonitor service

Do not modify:
- Position model (fields already exist)
- API endpoints (they already return P&L fields)
- Frontend (no changes needed)

Acceptance criteria:
- Positions API returns non-zero `unrealized_pnl` for positions with price changes
- `current_price` field is populated
- Service runs continuously without errors
- Logs show P&L updates every 60 seconds
```

---

### Ticket 2: Stop-Loss Order Monitoring
**Agent:** `quant-research`  
**Ticket:** `TICKET-202: Monitor stop-loss orders and detect fills`  
**Branch:** `feature/stop-loss-monitoring`

**Prompt:**
```
Implement a service that monitors stop-loss orders on Kraken and detects when they're filled, then closes positions accordingly.

Requirements:
1. Extend `PositionMonitor` in `backend/positions/monitor.py`:
   - Add `_check_stop_loss_orders()` method
   - Query Kraken for all open orders using `query_orders()` API
   - For each position with `stop_loss_order_id`, check if order still exists
   - If order not found (filled or cancelled), verify position is closed on Kraken

2. When stop-loss detected as filled:
   - Query Kraken holdings to confirm position quantity is 0
   - Call `tracker.close_position(symbol)` to remove from tracker
   - Log to activity feed: "Stop-loss triggered: {symbol} sold at ${price:.2f}"
   - Record realized P&L in metrics

3. Integration:
   - Run `_check_stop_loss_orders()` every 60 seconds in PositionMonitor
   - Check all positions that have `stop_loss_order_id` set

4. Edge cases:
   - If stop-loss order cancelled manually, log warning but don't close position
   - If position still exists on Kraken but order is gone, investigate (may be partial fill)
   - Handle Kraken API errors gracefully (retry, don't crash)

Files to modify:
- `backend/positions/monitor.py` - Add stop-loss monitoring logic
- `backend/positions/tracker.py` - Ensure `close_position()` works correctly
- `backend/execution/kraken_rest.py` - Verify `query_orders()` method exists

Do not modify:
- Stop-loss order placement logic (already works)
- Position model (already has `stop_loss_order_id` field)

Acceptance criteria:
- Service detects when stop-loss orders are filled
- Positions are closed in tracker when stop-loss triggers
- Activity log shows stop-loss execution
- No false positives (positions closed incorrectly)
```

---

### Ticket 3: Position Sync with Kraken
**Agent:** `quant-research`  
**Ticket:** `TICKET-203: Sync positions with Kraken holdings to detect closures`  
**Branch:** `feature/position-sync-kraken`

**Prompt:**
```
Implement a service that periodically syncs positions with Kraken holdings to detect when positions are closed (e.g., by stop-loss, manual sell, or external action).

Requirements:
1. Extend `PositionTracker` in `backend/positions/tracker.py`:
   - `sync_with_kraken()` method already exists, verify it works correctly
   - Ensure it calls `update_position_from_holding()` for each Kraken holding
   - Ensure it closes positions in tracker that don't exist on Kraken (quantity = 0)

2. Integration in `PositionMonitor`:
   - Call `tracker.sync_with_kraken()` every 5 minutes (300 seconds)
   - Log sync results: "SYNC: {created} created, {updated} updated, {closed} closed"

3. Handle discrepancies:
   - If position exists in tracker but not on Kraken: close it
   - If position exists on Kraken but not in tracker: create it (may be manual trade)
   - If quantities differ: update tracker to match Kraken (source of truth)

4. P&L calculation after sync:
   - After syncing, update P&L for all positions
   - Calculate realized P&L for closed positions
   - Record in metrics

Files to modify:
- `backend/positions/monitor.py` - Add sync call to PositionMonitor
- `backend/positions/tracker.py` - Verify `sync_with_kraken()` works correctly

Do not modify:
- Kraken API client (already has balance fetching)
- Position model (already has required fields)

Acceptance criteria:
- Positions sync with Kraken every 5 minutes
- Closed positions are detected and removed from tracker
- New positions from manual trades are added to tracker
- Sync logs show accurate counts
```

---

### Ticket 4: Account P&L Calculation
**Agent:** `quant-research`  
**Ticket:** `TICKET-204: Calculate and display account-level P&L`  
**Branch:** `feature/account-pnl-calculation`

**Prompt:**
```
Implement account-level P&L calculation that shows total profit/loss from initial equity.

Requirements:
1. Update `AccountTracker` in `backend/risk/account.py`:
   - `current_equity` already fetches from Kraken (correct)
   - `initial_equity` already exists (from ACCOUNT_EQUITY env var or first balance)
   - Add `total_pnl` property: `current_equity - initial_equity`
   - Add `pnl_percent` property: `(total_pnl / initial_equity) * 100`

2. Update `/api/v1/account` endpoint in `backend/api/routes/account.py`:
   - Include `total_pnl` and `pnl_percent` in response
   - Ensure `current_equity` and `initial_equity` are accurate

3. Update frontend `AccountPanel.tsx` (if needed):
   - Display `total_pnl` instead of hardcoded $0.00
   - Show `pnl_percent` as percentage
   - Color code: green for profit, red for loss

Files to modify:
- `backend/risk/account.py` - Add P&L calculation properties
- `backend/api/routes/account.py` - Include P&L in response
- `frontend/src/components/AccountPanel.tsx` - Display P&L (if not already)

Do not modify:
- Database schemas (no schema changes needed)
- API response models (add fields if missing, don't break existing)

Acceptance criteria:
- Account endpoint returns accurate `total_pnl` and `pnl_percent`
- Frontend displays correct P&L (not $0.00)
- P&L matches actual account balance change
- Initial equity is preserved correctly
```

---

### Ticket 5: Take-Profit Logic
**Agent:** `quant-research`  
**Ticket:** `TICKET-205: Implement take-profit auto-sell logic`  
**Branch:** `feature/take-profit-logic`

**Prompt:**
```
Implement configurable take-profit logic that automatically sells positions when they reach a profit threshold.

Requirements:
1. Add environment variable:
   - `TAKE_PROFIT_PCT` (default: 10.0) - Percentage profit to trigger sell

2. Extend `PositionMonitor` in `backend/positions/monitor.py`:
   - Add `_check_take_profit()` method
   - For each position, calculate profit: `(current_price - entry_price) / entry_price * 100`
   - If profit >= `TAKE_PROFIT_PCT`, trigger sell:
     - Create `TradeIntent` with `side="sell"` and `intent_type="exit"`
     - Use strategy ID from `position.opened_by_strategy_id`
     - Call `execute_trade()` to sell position
     - Log: "Take-profit triggered: {symbol} sold at ${current_price:.2f} (profit: {profit:.1f}%)"

3. Integration:
   - Run `_check_take_profit()` every 60 seconds in PositionMonitor
   - Only check positions with `opened_by_strategy_id` set (strategy-owned positions)
   - Respect strategy ownership (only strategy that opened can trigger take-profit)

4. Edge cases:
   - If position already has SELL signal from strategy, don't duplicate
   - If position quantity is 0, skip
   - Handle execution errors gracefully (log, don't crash)

Files to modify:
- `backend/positions/monitor.py` - Add take-profit checking logic
- `.env` - Add `TAKE_PROFIT_PCT` variable (document default: 10.0)
- `backend/execution/executor.py` - Ensure SELL orders work correctly (already implemented)

Do not modify:
- Strategy logic (take-profit is execution-level, not strategy-level)
- Position model (already has required fields)

Acceptance criteria:
- Positions are automatically sold when profit >= take-profit threshold
- Take-profit respects strategy ownership
- Activity log shows take-profit sales
- Configurable via environment variable
```

---

### Ticket 6: Position-Level Stop-Loss Monitoring
**Agent:** `quant-research`  
**Ticket:** `TICKET-206: Monitor positions for stop-loss threshold breaches`  
**Branch:** `feature/position-stop-loss-monitoring`

**Prompt:**
```
Implement position-level stop-loss monitoring that checks if current price has breached stop-loss threshold and auto-sells if needed.

Requirements:
1. Extend `PositionMonitor` in `backend/positions/monitor.py`:
   - Add `_check_stop_loss_thresholds()` method
   - For each position with `stop_loss_price` set:
     - If `current_price <= stop_loss_price` (for long positions):
       - Log warning: "Stop-loss threshold breached: {symbol} price ${current_price:.2f} <= stop ${stop_loss_price:.2f}"
       - Check if stop-loss order still exists on Kraken
       - If order doesn't exist or isn't filled, manually trigger sell:
         - Create `TradeIntent` with `side="sell"` and `intent_type="exit"`
         - Call `execute_trade()` to sell position
         - Log: "Stop-loss enforced: {symbol} sold at ${current_price:.2f}"

2. Integration:
   - Run `_check_stop_loss_thresholds()` every 60 seconds in PositionMonitor
   - Run after price updates (so current_price is fresh)
   - Only check positions with `stop_loss_price` set

3. Edge cases:
   - If stop-loss order already filled, skip (handled by Ticket 202)
   - If position quantity is 0, skip
   - Handle execution errors gracefully

Files to modify:
- `backend/positions/monitor.py` - Add stop-loss threshold checking
- `backend/positions/models.py` - Verify `stop_loss_price` field exists

Do not modify:
- Stop-loss order placement (already works)
- Position model (already has `stop_loss_price` field)

Acceptance criteria:
- Positions are auto-sold when price breaches stop-loss threshold
- Works even if stop-loss order wasn't filled by Kraken
- Logs show stop-loss enforcement
- No duplicate sells (check if position already closed)
```

---

## Execution Order

1. **TICKET-201** (P&L Calculation) - **CRITICAL PATH** - Must be done first
2. **TICKET-204** (Account P&L) - **CRITICAL PATH** - Depends on TICKET-201
3. **TICKET-202** (Stop-Loss Monitoring) - **HIGH PRIORITY** - Can run parallel with TICKET-203
4. **TICKET-203** (Position Sync) - **HIGH PRIORITY** - Can run parallel with TICKET-202
5. **TICKET-206** (Position Stop-Loss) - **HIGH PRIORITY** - Depends on TICKET-201
6. **TICKET-205** (Take-Profit) - **MEDIUM PRIORITY** - Can be done last

## Notes

- **Priority**: TICKET-201 and TICKET-204 are blocking - client cannot see accurate P&L without these
- **Testing**: After each ticket, verify:
  - Positions API returns updated P&L
  - Account API returns accurate total P&L
  - Stop-loss orders are monitored
  - Positions sync with Kraken
- **Rollback**: Each ticket is independent - can rollback individual features if issues occur
- **Performance**: Monitoring services should use minimal resources and rate-limit API calls
