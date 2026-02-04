# 🛸 Project Omega: $31.80 Live Sprint Implementation Plan

**Status:** Ready for Execution  
**Objective:** Grow initial capital from $31.80 to $50.00 using high-precision "Scout & Soldier" model  
**Risk Limit:** Hard cap of 2% ($0.63) risk per trade  
**Core Philosophy:** Minimize "Time-at-Risk" - liberate capital if trade isn't moving or winning

---

## 1. Scope

### 1.1 In Scope

**Backend Engineering (The Hub):**
- Fixed Scout sizing ($1.50 USD hard-coded for accounts < $50.00)
- Database schema migration (add `is_live` and `execution_mode` to orders table)
- EXECUTION_ALLOWED double-latch audit (query orders table before firing)
- Soldier scaling fix ($2.00 scale-in size)
- Opportunity Filter (48-hour rule for stale positions)
- Slippage threshold monitor (0.2% warning threshold)

**Frontend Engineering (The Window):**
- Growth visualization (P&L as percentage of $31.80 base)
- Live execution preview (PREVIEW: LIVE_ORDER_PENDING flash message)
- Panic button audit (verify CancelAll API integration)

**Operational:**
- Ghost phase testing (dummy API keys)
- Handshake verification (real keys, single BTC/USD trade)
- Watchtower monitoring (EXECUTION_ALLOWED gate verification)

### 1.2 Out of Scope

- Dynamic sizing logic (disabled until M3 milestone at $50.00)
- Multi-strategy concurrent execution (single strategy only until M2)
- Adaptive sizing adjustments (disabled until M3)
- New strategy development
- Exchange integration changes (Kraken only)
- Frontend redesign (only targeted UI updates)

---

## 2. File Ownership

### 2.1 Backend (`backend/**`)

**Risk Management:**
- `backend/risk/sizing.py` - PositionSizer class (TICKET-601, TICKET-602)
- `backend/positions/monitor.py` - PositionMonitor class (TICKET-602, TICKET-605)

**Execution:**
- `backend/execution/executor.py` - execute_trade function (TICKET-601, TICKET-604, TICKET-606)
- `backend/execution/panic.py` - Panic sequence (TICKET-609)

**Database:**
- `backend/db/models.py` - Order model (TICKET-603)
- `backend/db/schema.sql` - Schema definition (TICKET-603)
- `backend/db/migrations/001_initial_schema.sql` - Migration reference (TICKET-603)
- `backend/alembic/versions/003_add_live_execution_fields.py` - New migration (TICKET-603)

**Screener:**
- `backend/screener/service.py` - ScreenerService._process_auto_execution (TICKET-604)

**API Routes:**
- `backend/api/routes/account.py` - Account endpoint (TICKET-607 - backend support)

### 2.2 Frontend (`frontend/**`)

**Components:**
- `frontend/src/components/AccountPanel.tsx` - Account display (TICKET-607)
- `frontend/src/components/ActivityLog.tsx` - Activity log display (TICKET-608)
- `frontend/src/components/Dashboard.tsx` - Dashboard integration (TICKET-609)

**Hooks:**
- `frontend/src/hooks/useAccount.ts` - Account data hook (TICKET-607)

**API Integration:**
- `frontend/src/api/panic.ts` - Panic API client (TICKET-609)

### 2.3 Contracts (`contracts/**`)

**Types:**
- `contracts/types.md` - Order type definitions (TICKET-603)

**OpenAPI:**
- `contracts/openapi.yaml` - Order schema updates (TICKET-603)

### 2.4 Documentation (`docs/**`)

- `OMNI_BOT_WEBAPP_DOCUMENTATION.md` - System documentation updates (all tickets)

---

## 3. Contracts Impacted

### 3.1 Database Schema Changes

**Orders Table (`orders`):**
- **New Field:** `is_live` (BOOLEAN, NOT NULL, DEFAULT FALSE)
  - Purpose: Distinguish live trades from shadow trades
  - Default: FALSE (backward compatible with existing shadow trades)
- **New Field:** `execution_mode` (VARCHAR(50), NOT NULL, DEFAULT 'shadow')
  - Purpose: Track execution context (shadow, live, test)
  - Values: 'shadow', 'live', 'test'
  - Default: 'shadow' (backward compatible)

**Migration Strategy:**
- Add columns with defaults (non-breaking)
- Existing rows will have `is_live=FALSE` and `execution_mode='shadow'`
- New live orders will explicitly set `is_live=TRUE` and `execution_mode='live'`

### 3.2 API Contract Changes

**Order Response (`GET /api/v1/orders`):**
- Add `is_live: boolean` field
- Add `execution_mode: string` field

**Order Creation (`POST /api/v1/orders`):**
- Accept optional `is_live` and `execution_mode` in request body
- Backend will set these based on `LIVE_TRADING` env var and shadow mode state

### 3.3 Event Contract Changes

**Activity Log Events:**
- New event type: `PREVIEW: LIVE_ORDER_PENDING` (TICKET-608)
  - Displayed in ActivityLog for 5 seconds before live execution
  - Only shown during "Probe" phase (first 24 hours)

**No breaking changes to existing event types.**

---

## 4. Acceptance Criteria

### 4.1 TICKET-601: Fixed Scout Sizing

**Criteria:**
1. ✅ When `account_equity < $50.00`, Scout entry size is exactly $1.50 USD
2. ✅ Position size calculation ignores dynamic sizing logic
3. ✅ Stop loss remains at 42% (maintains $0.63 risk = $1.50 × 0.42)
4. ✅ Quantity calculation respects Kraken precision requirements
5. ✅ Log message includes "TICKET-601: fixed $1.50" confirmation

**Test Commands:**
```bash
# Set equity to $31.80 in test environment
# Trigger a BUY signal
# Verify order quantity = $1.50 / entry_price
# Verify stop loss = entry_price × (1 - 0.42)
# Verify log contains "fixed $1.50"
```

### 4.2 TICKET-602: Soldier Scaling Fix

**Criteria:**
1. ✅ When Scout position is up >1.5%, Soldier scale-in size is exactly $2.00 USD
2. ✅ Stop loss moves to breakeven immediately after Soldier execution
3. ✅ Total position value = $3.50 ($1.50 Scout + $2.00 Soldier)
4. ✅ Maximum risk post-breakeven = $0.00
5. ✅ Log message includes "Soldier scale-in: $2.00" confirmation

**Test Commands:**
```bash
# Create Scout position ($1.50)
# Simulate price increase >1.5%
# Verify Soldier scale-in = $2.00
# Verify stop loss updated to breakeven
# Verify total position = $3.50
```

### 4.3 TICKET-603: DB Schema Migration

**Criteria:**
1. ✅ Migration script adds `is_live` and `execution_mode` columns to `orders` table
2. ✅ Existing rows have `is_live=FALSE` and `execution_mode='shadow'`
3. ✅ New live orders have `is_live=TRUE` and `execution_mode='live'`
4. ✅ SQLAlchemy model updated with new fields
5. ✅ API responses include new fields

**Test Commands:**
```bash
# Run migration: alembic upgrade head
# Query existing orders: SELECT is_live, execution_mode FROM orders LIMIT 10;
# Verify all existing rows have is_live=FALSE
# Create new live order
# Verify new order has is_live=TRUE
```

### 4.4 TICKET-604: EXECUTION_ALLOWED Audit

**Criteria:**
1. ✅ Before execution, executor queries `orders` table for current candle ID
2. ✅ If order exists for candle ID, execution is skipped
3. ✅ Redis EXECUTION_ALLOWED key check remains (double-latch)
4. ✅ Log message includes "EXECUTION_ALLOWED gate closed (DB check)" when skipped
5. ✅ No duplicate orders fired on same candle close

**Test Commands:**
```bash
# Trigger signal on candle close
# Verify EXECUTION_ALLOWED logged
# Manually insert order record for same candle ID
# Trigger signal again
# Verify execution skipped with DB check message
```

### 4.5 TICKET-605: Opportunity Filter (48-Hour Rule)

**Criteria:**
1. ✅ Background task scans open positions every 15 minutes
2. ✅ If `hold_time > 48h` AND `pnl < +1%`, position is force-closed
3. ✅ Market sell order is executed
4. ✅ Activity log includes "EXIT_FORCED: 48-hour rule" message
5. ✅ Position removed from Redis after close

**Test Commands:**
```bash
# Create position with entry_time = 49 hours ago
# Set unrealized_pnl = +0.5%
# Run background task
# Verify position closed with market sell
# Verify EXIT_FORCED log entry
```

### 4.6 TICKET-606: Slippage Threshold Monitor

**Criteria:**
1. ✅ After order fill, calculate: `abs(fill_price - signal_price) / signal_price`
2. ✅ If slippage > 0.2%, log `HIGH_SLIPPAGE_WARNING`
3. ✅ Warning includes signal_price, fill_price, slippage_pct
4. ✅ Warning does not block execution (informational only)

**Test Commands:**
```bash
# Create signal with price = $100.00
# Simulate fill at $100.25 (0.25% slippage)
# Verify HIGH_SLIPPAGE_WARNING logged
# Verify fill_price, signal_price, slippage_pct in log
```

### 4.7 TICKET-607: Growth Visualization

**Criteria:**
1. ✅ AccountPanel displays P&L as percentage of $31.80 base
2. ✅ Calculation: `((current_equity - 31.80) / 31.80) * 100`
3. ✅ Display format: "+X.XX%" or "-X.XX%" with color coding
4. ✅ Updates in real-time (every 10 seconds)

**Test Commands:**
```bash
# Set current_equity = $35.00
# Verify AccountPanel shows "+9.75%" (green)
# Set current_equity = $30.00
# Verify AccountPanel shows "-5.66%" (red)
```

### 4.8 TICKET-608: Live Execution Preview

**Criteria:**
1. ✅ Before live order execution, ActivityLog displays "PREVIEW: LIVE_ORDER_PENDING"
2. ✅ Preview message visible for 5 seconds
3. ✅ Preview only shown when `LIVE_TRADING=TRUE` and not in shadow mode
4. ✅ Preview includes symbol, side, quantity, price
5. ✅ After 5 seconds, preview replaced with actual TRADE_PLACED event

**Test Commands:**
```bash
# Enable LIVE_TRADING=TRUE
# Trigger live order
# Verify PREVIEW message appears in ActivityLog
# Wait 5 seconds
# Verify TRADE_PLACED replaces preview
```

### 4.9 TICKET-609: Panic Button Audit

**Criteria:**
1. ✅ Panic button calls `POST /api/v1/panic`
2. ✅ Backend calls Kraken `CancelAll` API
3. ✅ Response includes `orders_cancelled` count
4. ✅ UI displays cancellation confirmation
5. ✅ System halt mode is enabled
6. ✅ Trading is disabled

**Test Commands:**
```bash
# Create 3 open orders on Kraken
# Click panic button in UI
# Verify API returns orders_cancelled=3
# Verify UI shows confirmation
# Verify system halt mode enabled
# Verify trading disabled
```

---

## 5. Dependencies

### 5.1 Critical Path Dependencies

**TICKET-603 (DB Schema Migration) must be completed FIRST:**
- TICKET-604 depends on `is_live` and `execution_mode` fields existing
- TICKET-605 depends on `is_live` field for filtering live positions
- TICKET-606 depends on orders table structure

**TICKET-601 (Fixed Scout Sizing) must be completed BEFORE:**
- TICKET-602 (Soldier Scaling) - assumes Scout size is $1.50

**No dependencies for:**
- TICKET-607 (Growth Visualization) - frontend-only
- TICKET-608 (Live Execution Preview) - frontend-only
- TICKET-609 (Panic Button Audit) - independent

### 5.2 Execution Order

**Phase 1 (Foundation - Day 1):**
1. TICKET-603: DB Schema Migration
2. TICKET-601: Fixed Scout Sizing

**Phase 2 (Execution Logic - Day 2):**
3. TICKET-604: EXECUTION_ALLOWED Audit
4. TICKET-602: Soldier Scaling Fix
5. TICKET-606: Slippage Threshold Monitor

**Phase 3 (Monitoring & Safety - Day 3):**
6. TICKET-605: Opportunity Filter (48-Hour Rule)
7. TICKET-607: Growth Visualization
8. TICKET-608: Live Execution Preview
9. TICKET-609: Panic Button Audit

---

## 6. Agent Launch Instructions

### 6.1 TICKET-601: Fixed Scout Sizing

**Agent:** `/backend-execute`  
**Ticket:** TICKET-601 - Fixed Scout Sizing  
**Branch:** `ticket-601-fixed-scout-sizing`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-601: Hard-code Scout entry size to $1.50 USD for accounts < $50.00.

Requirements:
1. Modify `backend/risk/sizing.py` → `PositionSizer.calculate_scout_size()` method
2. Remove any dynamic calculation logic - hard-code `scout_entry_size_usd = 1.50`
3. Maintain 42% stop loss (ensures $0.63 risk = $1.50 × 0.42)
4. Ensure quantity calculation respects Kraken precision (round to 8 decimals)
5. Add log message: "Scout sizing (TICKET-601): fixed $1.50, entry=$X -> size=$1.50, stop=42%, stop_price=$X, risk=$0.63"

Verification:
- When account_equity < $50.00, Scout size must be exactly $1.50 USD
- Stop loss must be entry_price × (1 - 0.42)
- Log must include "TICKET-601: fixed $1.50" confirmation

Reference: docs/MSSD.md, OMNI_BOT_WEBAPP_DOCUMENTATION.md (Scout & Soldier section)
```

### 6.2 TICKET-602: Soldier Scaling Fix

**Agent:** `/backend-execute`  
**Ticket:** TICKET-602 - Soldier Scaling Fix  
**Branch:** `ticket-602-soldier-scaling-fix`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-602: Adjust Soldier scale-in size to $2.00 USD (currently $3.00).

Requirements:
1. Modify `backend/positions/monitor.py` → `PositionMonitor._execute_soldier_scale_in()` method
2. Change `soldier_scale_in_size_usd` from $3.00 to $2.00
3. Update `backend/execution/executor.py` → `execute_trade()` Soldier sizing logic
4. Ensure stop loss moves to breakeven immediately after Soldier execution
5. Verify total position = $3.50 ($1.50 Scout + $2.00 Soldier)
6. Update log message: "Soldier scale-in: $2.00 @ $X"

Logic:
- If Scout ($1.50) is up >1.5%, add $2.00 (Soldier)
- Move Stop Loss to Breakeven immediately
- Total position = $3.50
- Maximum risk post-breakeven = $0.00

Verification:
- Soldier scale-in must be exactly $2.00 USD
- Stop loss must update to breakeven after execution
- Total position value must equal $3.50

Reference: docs/MSSD.md, backend/positions/monitor.py (lines 273-397)
```

### 6.3 TICKET-603: DB Schema Migration

**Agent:** `/backend-execute`  
**Ticket:** TICKET-603 - DB Schema Migration  
**Branch:** `ticket-603-db-schema-migration`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-603: Add is_live (Boolean) and execution_mode (String) fields to orders table.

Requirements:
1. Create Alembic migration: `backend/alembic/versions/003_add_live_execution_fields.py`
2. Add `is_live` column: BOOLEAN NOT NULL DEFAULT FALSE
3. Add `execution_mode` column: VARCHAR(50) NOT NULL DEFAULT 'shadow'
4. Update `backend/db/models.py` → `Order` model with new fields
5. Update `backend/db/schema.sql` with new columns
6. Update `backend/db/migrations/001_initial_schema.sql` (reference only)

Migration Strategy:
- Add columns with defaults (non-breaking)
- Existing rows: is_live=FALSE, execution_mode='shadow'
- New live orders: is_live=TRUE, execution_mode='live'

Verification:
- Migration runs without errors: `alembic upgrade head`
- Existing orders have is_live=FALSE
- New live orders have is_live=TRUE
- API responses include new fields

Reference: docs/MSSD.md, backend/db/models.py, backend/alembic/versions/001_initial_schema.py
```

### 6.4 TICKET-604: EXECUTION_ALLOWED Audit

**Agent:** `/backend-execute`  
**Ticket:** TICKET-604 - EXECUTION_ALLOWED Audit  
**Branch:** `ticket-604-execution-allowed-audit`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-604: Add secondary check in executor.py that queries orders table for current candle ID before firing.

Requirements:
1. Modify `backend/execution/executor.py` → `execute_trade()` function
2. Before execution, query `orders` table for existing order with same:
   - strategy_id
   - symbol
   - candle_id (from metadata or bar_timestamp)
3. If order exists, skip execution and log: "EXECUTION_ALLOWED gate closed (DB check): order already exists for candle={candle_id}"
4. Keep existing Redis EXECUTION_ALLOWED check (double-latch pattern)
5. Update `backend/screener/service.py` → `_process_auto_execution()` if needed

Double-Latch Pattern:
- First latch: Redis EXECUTION_ALLOWED key (existing)
- Second latch: Database orders table query (new)

Verification:
- No duplicate orders fired on same candle close
- Log includes "DB check" message when skipped
- Both Redis and DB checks must pass for execution

Reference: docs/MSSD.md, backend/execution/executor.py, backend/screener/service.py (lines 1393-1425)
```

### 6.5 TICKET-605: Opportunity Filter (48-Hour Rule)

**Agent:** `/backend-execute`  
**Ticket:** TICKET-605 - Opportunity Filter (48-Hour Rule)  
**Branch:** `ticket-605-opportunity-filter-48h`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-605: Implement cron-job or background task that scans open positions. If hold_time > 48h AND pnl < +1%, execute a market sell.

Requirements:
1. Modify `backend/positions/monitor.py` → `PositionMonitor` class
2. Add method: `_check_48_hour_rule(position, current_price)`
3. Calculate hold_time: `current_time - position.entry_time`
4. Calculate pnl: `(current_price - entry_price) / entry_price * 100`
5. If hold_time > 48 hours AND pnl < +1%, execute market sell
6. Log activity: "EXIT_FORCED: 48-hour rule - hold_time=Xh, pnl=Y%"
7. Integrate check into existing position monitoring loop (runs every 15 minutes)

Logic:
- Scan all open positions
- Check hold_time > 48h AND pnl < +1%
- If true, create SELL TradeIntent and execute
- Remove position from Redis after close

Verification:
- Position held 49 hours with +0.5% P&L is force-closed
- Position held 47 hours is not closed
- Position held 49 hours with +2% P&L is not closed
- EXIT_FORCED log entry created

Reference: docs/MSSD.md, backend/positions/monitor.py (lines 399-500)
```

### 6.6 TICKET-606: Slippage Threshold Monitor

**Agent:** `/backend-execute`  
**Ticket:** TICKET-606 - Slippage Threshold Monitor  
**Branch:** `ticket-606-slippage-threshold-monitor`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-606: If difference between Signal_Price and Fill_Price is >0.2%, log HIGH_SLIPPAGE_WARNING.

Requirements:
1. Modify `backend/execution/executor.py` → `execute_trade()` function
2. After order fill, calculate slippage: `abs(fill_price - signal_price) / signal_price * 100`
3. If slippage > 0.2%, log warning: "HIGH_SLIPPAGE_WARNING: signal_price=$X, fill_price=$Y, slippage=Z%"
4. Include signal_price from TradeIntent metadata or current_price parameter
5. Include fill_price from Fill object
6. Warning is informational only (does not block execution)

Verification:
- Fill at 0.25% slippage triggers warning
- Fill at 0.15% slippage does not trigger warning
- Warning includes all required fields

Reference: docs/MSSD.md, backend/execution/executor.py, backend/execution/models.py
```

### 6.7 TICKET-607: Growth Visualization

**Agent:** `/frontend-execute`  
**Ticket:** TICKET-607 - Growth Visualization  
**Branch:** `ticket-607-growth-visualization`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-607: Update AccountPanel to show P&L as a percentage of the $31.80 base.

Requirements:
1. Modify `frontend/src/components/AccountPanel.tsx`
2. Add calculation: `profitPctOfWallet = ((current_equity - 31.80) / 31.80) * 100`
3. Display format: "+X.XX%" (green) or "-X.XX%" (red)
4. Place below existing P&L display
5. Label: "Growth: +X.XX% of $31.80 base"
6. Update in real-time (hook already polls every 10 seconds)

Constants:
- WALLET_BASE_AMOUNT = 31.80 (already defined in AccountPanel.tsx)

Verification:
- Current equity $35.00 shows "+9.75%" (green)
- Current equity $30.00 shows "-5.66%" (red)
- Updates automatically every 10 seconds

Reference: frontend/src/components/AccountPanel.tsx (lines 1-140)
```

### 6.8 TICKET-608: Live Execution Preview

**Agent:** `/frontend-execute`  
**Ticket:** TICKET-608 - Live Execution Preview  
**Branch:** `ticket-608-live-execution-preview`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-608: Before bot fires a live trade, ActivityLog should flash a PREVIEW: LIVE_ORDER_PENDING message for 5 seconds (only during "Probe" phase).

Requirements:
1. Modify `backend/api/routes/events.py` → `log_activity()` function
2. Before live order execution, log: "PREVIEW: LIVE_ORDER_PENDING: {symbol} {side} {quantity} @ ${price}"
3. Only log when LIVE_TRADING=TRUE and not in shadow mode
4. Modify `frontend/src/components/ActivityLog.tsx` to handle PREVIEW event type
5. Display preview message with yellow/orange color for 5 seconds
6. After 5 seconds, replace with actual TRADE_PLACED event

Backend Changes:
- In `backend/execution/executor.py`, before live execution, call `log_activity("PREVIEW: LIVE_ORDER_PENDING", ...)`
- Include symbol, side, quantity, price in details

Frontend Changes:
- Add 'PREVIEW: LIVE_ORDER_PENDING' to ActivityType
- Style with yellow/orange color
- Auto-remove after 5 seconds (or when TRADE_PLACED arrives)

Verification:
- Preview message appears before live order
- Message disappears after 5 seconds or when trade placed
- Only shown in live mode (not shadow mode)

Reference: frontend/src/components/ActivityLog.tsx, backend/api/routes/events.py, backend/execution/executor.py
```

### 6.9 TICKET-609: Panic Button Audit

**Agent:** `/frontend-execute`  
**Ticket:** TICKET-609 - Panic Button Audit  
**Branch:** `ticket-609-panic-button-audit`  
**Prompt:**
```
We are in 'Project Omega' mode. Starting capital is $31.80. All code must respect the 'Scout & Soldier' fixed-size logic ($1.50/$2.00). Do not use dynamic sizing until we hit M3 ($50).

TICKET-609: Verify that the "Panic Button" can handle Kraken's CancelAll API call and verify the response in the UI.

Requirements:
1. Review `backend/execution/panic.py` → `cancel_all_open_orders()` function
2. Verify it calls Kraken `get_open_orders()` and `cancel_order()` for each order
3. Verify response includes `orders_cancelled` count
4. Modify `frontend/src/components/Dashboard.tsx` or panic button component
5. Display cancellation confirmation: "Panic executed: {count} order(s) cancelled"
6. Verify system halt mode is enabled after panic
7. Verify trading is disabled after panic

Backend Verification:
- `backend/execution/panic.py` correctly calls Kraken API
- Returns `orders_cancelled` count
- Handles errors gracefully (fail-closed)

Frontend Changes:
- Display panic button response in UI
- Show confirmation message with order count
- Update system status indicators

Verification:
- Panic button cancels all open orders
- UI displays cancellation count
- System halt mode enabled
- Trading disabled

Reference: backend/execution/panic.py, backend/api/routes/panic.py, frontend/src/components/Dashboard.tsx
```

---

## 7. Operational Runbook (First 24 Hours)

### 7.1 The "Ghost" Phase (Hours 0-2)

**Objective:** Verify execution flow without real money

**Steps:**
1. Set `LIVE_TRADING=TRUE` in `.env`
2. Set `KRAKEN_API_SECRET=DUMMY_KEY_FOR_TESTING`
3. Start bot and monitor logs
4. Verify bot attempts to fire but fails gracefully with "Auth Error"
5. Verify no real orders placed

**Success Criteria:**
- ✅ Bot attempts execution
- ✅ Auth error logged (expected)
- ✅ No real orders on Kraken

### 7.2 The "Handshake" (Minute 1)

**Objective:** Execute first real trade

**Steps:**
1. Replace `KRAKEN_API_SECRET` with real key
2. Enable ONLY BTC/USD symbol
3. Enable ONLY VWAP Mean Reversion strategy
4. Trigger manual $1.50 "Scout" trade
5. Monitor execution in ActivityLog

**Success Criteria:**
- ✅ Order placed on Kraken
- ✅ Order filled at expected price
- ✅ Position tracked in Redis
- ✅ Stop-loss order placed

### 7.3 The "Watchtower" (Hours 1-12)

**Objective:** Monitor EXECUTION_ALLOWED gate

**Steps:**
1. Monitor ActivityLog for EXECUTION_ALLOWED events
2. Verify only ONE execution per candle
3. Check database for duplicate orders
4. Verify EXECUTION_ALLOWED Redis keys expire correctly

**Success Criteria:**
- ✅ No duplicate orders on same candle
- ✅ EXECUTION_ALLOWED logged once per candle
- ✅ Database check prevents duplicates

---

## 8. Project Board & Milestones

### Milestone M1: The Proof ($32.80)
**Target:** Successfully execute 1 full cycle (Entry → Breakeven → Exit)  
**Tech Unlock:** None (baseline functionality)

### Milestone M2: The Stability ($40.00)
**Target:** Successfully execute 10 trades with zero manual resets  
**Tech Unlock:** Enable second "Live Slot" (Allow 2 concurrent live trades)

### Milestone M3: The Scale ($50.00)
**Target:** Reach $50.00 capital  
**Tech Unlock:** Enable dynamic sizing (remove $1.50/$2.00 hard-coded limits)

---

## 9. Risk Mitigation

### 9.1 Critical Safeguards

1. **Hard Stop Loss:** 42% stop loss on Scout ($0.63 max risk)
2. **Breakeven Guard:** Stop moves to breakeven after Soldier scale-in
3. **48-Hour Rule:** Stale positions auto-closed
4. **Double-Latch Execution:** Redis + Database checks prevent duplicates
5. **Slippage Monitoring:** Warns on >0.2% slippage
6. **Panic Button:** Emergency shutdown with order cancellation

### 9.2 Monitoring Checklist

- [ ] EXECUTION_ALLOWED events logged correctly
- [ ] No duplicate orders on same candle
- [ ] Scout size = $1.50 (verify in logs)
- [ ] Soldier size = $2.00 (verify in logs)
- [ ] Stop-loss orders placed immediately
- [ ] 48-hour rule triggers correctly
- [ ] Slippage warnings logged when >0.2%
- [ ] Panic button cancels all orders

---

## 10. Notes for Agents

**Critical Context:**
- Starting capital: $31.80
- Scout size: Fixed $1.50 USD (hard-coded)
- Soldier size: Fixed $2.00 USD (hard-coded)
- Risk per trade: $0.63 (42% of $1.50)
- Dynamic sizing: DISABLED until M3 ($50.00)

**Ownership Boundaries:**
- Backend owns: `backend/**`
- Frontend owns: `frontend/**`
- Contracts owns: `contracts/**`
- Do not modify files outside your ownership without explicit approval

**Documentation:**
- Authoritative docs: `docs/MSSD.md`, `OMNI_BOT_WEBAPP_DOCUMENTATION.md`
- Contract definitions: `contracts/types.md`, `contracts/openapi.yaml`

**Testing:**
- All changes must be tested in shadow mode first
- Verify backward compatibility (existing data)
- Check logs for expected messages

---

**End of Implementation Plan**
