# Leader Plan: MSDD v3.0 - Micro-Precision Execution Blueprint

## Project Identity & Constraints

**Current Phase:** Live-Strategy Probe (Single Strategy: VWAP Mean Reversion)  
**Starting Capital:** $31.80 USD  
**Execution Tier:** Kraken Pro API (Cost Minimum: ~$0.50 USD)  
**Risk Mandate:** 2% of total wallet per trade ($0.63 risk capital)

## Role Assignments

### Backend Team (backend-execute)
**Primary Responsibilities:** Execution logic, risk management, position tracking, API integration

- **TICKET-401:** Kraken AssetPairs Costmin Integration
  - Files: `backend/execution/kraken_rest.py`, `backend/execution/executor.py`, `backend/redis/keys.py`
  - Dependencies: None (foundation ticket)
  
- **TICKET-402:** Scout & Soldier Entry Model
  - Files: `backend/risk/sizing.py`, `backend/positions/models.py`, `backend/execution/executor.py`, `backend/positions/monitor.py`
  - Dependencies: TICKET-401 (needs costmin validation)
  
- **TICKET-403:** LIVE_SLOTS System
  - Files: `backend/risk/micro_mode.py`, `backend/risk/evaluator.py`, `backend/screener/service.py`, `backend/positions/tracker.py`, `backend/api/routes/account.py`
  - Dependencies: None (uses existing position counting)
  
- **TICKET-404:** Exit Engine - 48-Hour Opportunity Filter
  - Files: `backend/positions/monitor.py`, `backend/redis/keys.py`
  - Dependencies: None (can run parallel)
  
- **TICKET-405:** Exit Engine - ATR Trailing Stop
  - Files: `backend/positions/monitor.py`, `backend/positions/models.py`
  - Dependencies: TICKET-402 (needs position fields)
  
- **TICKET-406:** Exit Engine - Breakeven Guard
  - Files: `backend/positions/monitor.py`, `backend/execution/executor.py`
  - Dependencies: TICKET-402 (needs position fields)
  
- **TICKET-407:** Live Universe Restriction
  - Files: `backend/ingestor/symbols.py`, `backend/screener/service.py`, `backend/risk/evaluator.py`, `backend/redis/keys.py`
  - Dependencies: None (can run parallel)
  
- **TICKET-408:** Dynamic Risk Recalculation
  - Files: `backend/risk/account.py`, `backend/risk/sizing.py`, `backend/api/main.py`, `backend/redis/keys.py`
  - Dependencies: None (can run parallel)

### Frontend Team (frontend-execute)
**Primary Responsibilities:** UI components, user experience, data display

- **TICKET-409:** Frontend - Live Slot Status
  - Files: `frontend/src/components/PositionPanel.tsx`, `frontend/src/hooks/useAccount.ts`, `frontend/src/types/account.ts`
  - Dependencies: TICKET-403 (needs slot data from backend)
  
- **TICKET-410:** Frontend - Profit Percentage Display
  - Files: `frontend/src/components/AccountPanel.tsx`, `frontend/src/hooks/useAccount.ts`
  - Dependencies: None (independent)

### QA Team (qa-verify)
**Primary Responsibilities:** Testing, verification, edge case identification, regression testing

- **TICKET-411:** QA Verification - MSDD v3.0 Features
  - Scope: Verify all tickets (TICKET-401 through TICKET-410) meet acceptance criteria
  - Dependencies: All implementation tickets must be complete
  - Deliverables: Findings report, test recommendations, verification commands
  
- **TICKET-412:** Integration Testing - Full Trade Lifecycle
  - Scope: End-to-end testing of complete trade lifecycle
  - Dependencies: All implementation tickets must be complete
  - Deliverables: Test plan, test results, performance metrics

### No Role Assignments Needed

- **Contracts Team:** No breaking API changes (all Position model fields are optional, backward compatible)
- **Infrastructure Team:** No infrastructure changes required (uses existing Redis, PostgreSQL, Docker setup)
- **Quant-Research Team:** Strategy logic unchanged (VWAP Mean Reversion strategy remains as-is)

---

## 1. Scope

### In Scope

#### Core Execution Features
- **Scout & Soldier Entry Model:** Two-stage entry system with scale-in logic
- **Adaptive Position Sizing:** Fixed $1.50 Scout entry with 42% stop (maintains $0.63 risk)
- **Scale-In Logic:** $3.00 Soldier entry triggered at +1.5% profit with breakeven stop
- **Kraken AssetPairs Integration:** Query costmin per pair to enforce minimum order sizes
- **LIVE_SLOTS System:** Limit to 1 live position when balance < $50, route overflow to Shadow Mode

#### Exit Engine
- **48-Hour Opportunity Filter:** Auto-close positions that haven't hit TP1 within 48 hours
- **ATR Trailing Stop:** Activate at +3% profit, trail by 2.0 ATR (never moves down)
- **Breakeven Guard:** Move stop to entry+fees at +2% profit (risk-free trade)

#### Risk & Universe Management
- **Live Universe Restriction:** Limit to top 5 high-liquidity pairs (BTC, ETH, SOL, LINK, DOT)
- **Dynamic Risk Recalculation:** Recalculate $0.63 risk daily based on current equity
- **Hybrid Gate:** LIVE_SLOTS limit in Risk Evaluator (1 slot when balance < $50)

#### Frontend Enhancements
- **Live Slot Status Display:** Show "1/1 Slots Active" in PositionPanel
- **Profit Percentage Display:** Show profit as % of $31.80 wallet (growth-focused)

#### Growth Milestones
- **M1 ($35.00):** Enable after 5 successful Scout trades
- **M2 ($50.00):** Enable second Live Slot (2 concurrent positions)
- **M3 ($100.00):** Increase Scout size to $5.00

### Out of Scope
- Strategy logic changes (VWAP Mean Reversion strategy unchanged)
- Database schema changes (existing Position model sufficient)
- Shadow mode modifications (only overflow routing)
- Multi-strategy execution (single strategy focus)
- Take-profit partial exits (TP1/TP2 logic unchanged, only time-based exits)

---

## 2. File Ownership

### Backend Execution (`backend/execution/`)
- `backend/execution/executor.py` - Kraken AssetPairs costmin query, Scout/Soldier sizing logic
- `backend/execution/kraken_rest.py` - Add AssetPairs query method
- `backend/execution/order_manager.py` - No changes (order conversion unchanged)

### Backend Risk (`backend/risk/`)
- `backend/risk/sizing.py` - Add Scout/Soldier sizing methods, fixed $1.50 entry
- `backend/risk/evaluator.py` - Add LIVE_SLOTS check, route overflow to Shadow Mode
- `backend/risk/micro_mode.py` - Add LIVE_SLOTS configuration (1 slot when < $50)
- `backend/risk/account.py` - Add daily risk recalculation method

### Backend Positions (`backend/positions/`)
- `backend/positions/monitor.py` - Add scale-in check (+1.5%), breakeven guard (+2%), ATR trailing (+3%), 48-hour filter
- `backend/positions/models.py` - Add fields: `scout_entry_price`, `soldier_entry_price`, `scale_in_triggered`, `trailing_stop_active`, `breakeven_guard_active`
- `backend/positions/tracker.py` - Add scale-in recording, stop-loss update methods

### Backend Ingestor (`backend/ingestor/`)
- `backend/ingestor/symbols.py` - Add live universe filter (top 5 pairs: BTC, ETH, SOL, LINK, DOT)

### Backend API (`backend/api/routes/`)
- `backend/api/routes/positions.py` - No changes (positions endpoint unchanged)
- `backend/api/routes/account.py` - Add live slot status to account response

### Backend Redis (`backend/redis/`)
- `backend/redis/keys.py` - Add keys: `LIVE_SLOTS_COUNT_KEY`, `LIVE_UNIVERSE_KEY`, `ASSET_PAIRS_CACHE_KEY`

### Frontend (`frontend/src/`)
- `frontend/src/components/PositionPanel.tsx` - Add Live Slot Status display
- `frontend/src/components/AccountPanel.tsx` - Add profit % display (of $31.80)
- `frontend/src/types/position.ts` - Add new Position fields (optional for backward compatibility)

### Research (`research/`)
- No changes (strategy logic unchanged)

---

## 3. Contracts Impacted

### API Endpoints (No Breaking Changes)
- `GET /api/v1/positions` - Returns new Position fields (optional, backward compatible)
- `GET /api/v1/account` - Returns `live_slots_active`, `live_slots_max`, `profit_pct_of_wallet`
- `GET /api/v1/balance` - No changes

### Data Models (Backward Compatible Extensions)
- `Position` model - New optional fields:
  - `scout_entry_price: Optional[float]` - Initial Scout entry price
  - `soldier_entry_price: Optional[float]` - Scale-in Soldier entry price
  - `scale_in_triggered: bool` - Whether Soldier scale-in has occurred
  - `trailing_stop_active: bool` - Whether ATR trailing stop is active
  - `breakeven_guard_active: bool` - Whether breakeven guard is active
  - `trailing_stop_price: Optional[float]` - Current trailing stop price
  - `breakeven_stop_price: Optional[float]` - Breakeven stop price (entry + fees)

### Redis Keys (New Keys)
- `system:live_slots:count` - Current number of live positions (int)
- `system:live_slots:max` - Maximum live slots allowed (int, based on balance)
- `system:live_universe` - Set of symbols allowed for live trading (Set)
- `market:asset_pairs:{pair}` - Cached AssetPairs data with costmin (Hash, TTL: 1 hour)

### Environment Variables (New)
- `SCOUT_ENTRY_SIZE_USD=1.50` - Scout entry position size
- `SCOUT_STOP_LOSS_PCT=42.0` - Scout stop loss percentage (maintains $0.63 risk)
- `SOLDIER_SCALE_IN_SIZE_USD=3.00` - Soldier scale-in position size
- `SCALE_IN_PROFIT_TRIGGER_PCT=1.5` - Profit % to trigger Soldier scale-in
- `BREAKEVEN_GUARD_TRIGGER_PCT=2.0` - Profit % to trigger breakeven guard
- `ATR_TRAILING_STOP_TRIGGER_PCT=3.0` - Profit % to activate ATR trailing stop
- `ATR_TRAILING_STOP_MULTIPLIER=2.0` - ATR multiplier for trailing stop distance
- `OPPORTUNITY_FILTER_HOURS=48` - Hours before closing non-TP1 positions
- `LIVE_SLOTS_THRESHOLD_1=50.0` - Balance threshold for 1 slot (default)
- `LIVE_SLOTS_THRESHOLD_2=100.0` - Balance threshold for 2 slots (M2 milestone)
- `LIVE_UNIVERSE_PAIRS=BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOT/USD` - Allowed live trading pairs

---

## 4. Acceptance Criteria

### Critical Path (Must Pass)

#### TICKET-401: Kraken AssetPairs Costmin Integration
1. ✅ `KrakenClient.get_asset_pairs()` queries Kraken AssetPairs API
2. ✅ Costmin extracted and cached per pair (Redis, 1-hour TTL)
3. ✅ `execute_trade()` validates order size >= costmin before execution
4. ✅ Rejects orders below costmin with clear error message
5. ✅ Falls back to $0.50 default if AssetPairs query fails

#### TICKET-402: Scout & Soldier Entry Model
1. ✅ Scout entry: Fixed $1.50 position size (regardless of 2% calculation)
2. ✅ Scout stop: 42% stop loss (maintains $0.63 risk = $1.50 × 0.42)
3. ✅ Position model tracks `scout_entry_price` and `scale_in_triggered=False`
4. ✅ Scale-in check: Monitors for +1.5% profit from Scout entry
5. ✅ Soldier entry: $3.00 scale-in when trigger reached
6. ✅ Breakeven stop: Stop moved to Scout entry + fees after Soldier entry
7. ✅ Position model tracks `soldier_entry_price` and `breakeven_guard_active=True`

#### TICKET-403: LIVE_SLOTS System
1. ✅ `LIVE_SLOTS_MAX` calculated based on balance:
   - Balance < $50: 1 slot
   - Balance >= $50: 2 slots (M2 milestone)
   - Balance >= $100: 3 slots (future)
2. ✅ Risk Evaluator checks `current_live_positions < LIVE_SLOTS_MAX`
3. ✅ If slots full: Route signal to Shadow Mode, log ORDER_INTENT
4. ✅ If slots available: Proceed with live execution
5. ✅ PositionPanel displays "X/Y Slots Active"

#### TICKET-404: Exit Engine - 48-Hour Opportunity Filter
1. ✅ PositionMonitor checks time since entry for each position
2. ✅ If position held > 48 hours AND TP1 not hit: Force exit
3. ✅ Logs EXIT_FORCED with reason "opportunity_filter_48h"
4. ✅ Calculates P&L and records in metrics

#### TICKET-405: Exit Engine - ATR Trailing Stop
1. ✅ PositionMonitor checks if position is +3% profit
2. ✅ If +3%: Activate trailing stop, set `trailing_stop_active=True`
3. ✅ Trailing stop = current_price - (2.0 × ATR)
4. ✅ Stop only moves UP (never down) as price increases
5. ✅ If price drops to trailing stop: Execute exit
6. ✅ Logs EXIT_FORCED with reason "atr_trailing_stop"

#### TICKET-406: Exit Engine - Breakeven Guard
1. ✅ PositionMonitor checks if position is +2% profit
2. ✅ If +2%: Move stop to `entry_price + fees` (breakeven)
3. ✅ Set `breakeven_guard_active=True` and `breakeven_stop_price`
4. ✅ Update Kraken stop-loss order to new price
5. ✅ Logs activity: "Breakeven guard activated: {symbol} stop moved to ${price}"

#### TICKET-407: Live Universe Restriction
1. ✅ Ingestor filters live universe to top 5 pairs (BTC, ETH, SOL, LINK, DOT)
2. ✅ Screener only evaluates these pairs for live execution
3. ✅ Other pairs still evaluated for Shadow Mode
4. ✅ Redis key `system:live_universe` stores allowed pairs

#### TICKET-408: Dynamic Risk Recalculation
1. ✅ Daily recalculation: `risk_capital = current_equity × 0.02`
2. ✅ Scout entry size adjusted if needed (maintains 42% stop ratio)
3. ✅ Logs risk recalculation: "Risk capital recalculated: ${old} -> ${new}"

#### TICKET-409: Frontend - Live Slot Status
1. ✅ PositionPanel displays "Live Slots: X/Y Active"
2. ✅ Updates in real-time as positions open/close
3. ✅ Color coding: Green if slots available, Yellow if 1 slot remaining, Red if full

#### TICKET-410: Frontend - Profit Percentage Display
1. ✅ AccountPanel shows profit as % of $31.80 wallet
2. ✅ Example: "+5.2% of wallet" instead of "+$1.65"
3. ✅ Emphasizes growth over dollar amounts

### Success Criteria (Definition of Done)
- ✅ Signal-to-Live: Bot executes real $1.50 trade on Kraken Pro
- ✅ Stop Attachment: Stop-loss confirmed on Kraken UI immediately after buy
- ✅ Scale-In Trigger: $3.00 scale-in executes when position reaches +1.5%
- ✅ Breakeven Guard: Stop moved to breakeven when position reaches +2%
- ✅ Trailing Stop: ATR trailing stop activates at +3% and follows price up
- ✅ 48-Hour Filter: Position auto-closed if TP1 not hit within 48 hours
- ✅ Live Slot Limit: Second signal routes to Shadow Mode when 1 slot active
- ✅ Activity Proof: Activity Log shows EXECUTION_ALLOWED with correct $31.80 risk math

---

## 5. Dependencies

### Prerequisites (Must Complete First)
- ✅ Existing position tracking system (already implemented)
- ✅ Stop-loss order placement (already implemented)
- ✅ Shadow mode routing (already implemented)
- ✅ PositionMonitor service (already implemented)

### Parallel Work (Can Run Simultaneously)
- TICKET-401 (AssetPairs) and TICKET-407 (Live Universe) - Independent
- TICKET-402 (Scout/Soldier) and TICKET-403 (LIVE_SLOTS) - Independent
- TICKET-404, TICKET-405, TICKET-406 (Exit Engine) - Can be done in parallel
- TICKET-409 and TICKET-410 (Frontend) - Independent

### Sequential Dependencies
- TICKET-402 (Scout/Soldier) must complete before TICKET-405 (Trailing Stop) - Needs position fields
- TICKET-401 (AssetPairs) must complete before TICKET-402 (Scout/Soldier) - Needs costmin validation
- TICKET-403 (LIVE_SLOTS) must complete before TICKET-409 (Frontend) - Frontend needs slot data

---

## Agent Launch Instructions

### TICKET-401: Kraken AssetPairs Costmin Integration

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-401: Kraken AssetPairs Costmin Integration  
**Branch:** feature/msdd-v3-assetpairs-costmin

**Prompt:**
```
Implement Kraken AssetPairs API integration to query costmin per trading pair and enforce minimum order sizes.

Requirements:
1. Add `get_asset_pairs()` method to `backend/execution/kraken_rest.py`:
   - Calls Kraken public API: `https://api.kraken.com/0/public/AssetPairs`
   - Extracts `costmin` field for each pair
   - Returns dict: `{pair: costmin}` (e.g., {"XBTUSD": 0.50, "ETHUSD": 0.50})
   - Handles errors gracefully (fallback to $0.50 default)

2. Add caching layer:
   - Cache AssetPairs data in Redis: `market:asset_pairs:{pair}` (Hash)
   - TTL: 1 hour (refresh hourly)
   - Key structure: `market:asset_pairs:{normalized_pair}` -> `{"costmin": float, "updated_at": ISO timestamp}`

3. Update `backend/execution/executor.py`:
   - Before executing order, query costmin for pair
   - Validate: `position_size_usd >= costmin`
   - If below costmin: Reject with error "below_costmin: ${size} < ${costmin}"
   - Log costmin validation: "Order validated: ${size} >= ${costmin} (pair: {pair})"

4. Add Redis key to `backend/redis/keys.py`:
   - `ASSET_PAIRS_CACHE_KEY = "market:asset_pairs:{pair}"`

5. Error handling:
   - If AssetPairs API fails: Use default $0.50 costmin
   - Log warning: "AssetPairs query failed, using default costmin $0.50"
   - Don't block execution if cache miss (use default)

Reference: `backend/ingestor/symbols.py` has example AssetPairs query code.

Acceptance Criteria:
- AssetPairs data cached in Redis with 1-hour TTL
- Orders below costmin rejected with clear error
- Default $0.50 used if API fails
- Costmin validation logged for audit trail
```

---

### TICKET-402: Scout & Soldier Entry Model

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-402: Scout & Soldier Entry Model  
**Branch:** feature/msdd-v3-scout-soldier-entry

**Prompt:**
```
Implement the Scout & Soldier two-stage entry model for micro-precision execution.

Requirements:
1. Update `backend/risk/sizing.py`:
   - Add `calculate_scout_size()` method:
     - Returns fixed $1.50 position size (from env: `SCOUT_ENTRY_SIZE_USD=1.50`)
     - Calculates stop loss: `entry_price × (1 - 0.42)` (42% stop maintains $0.63 risk)
     - Returns `PositionSize` with `position_size_usd=1.50`, `stop_loss_pct=42.0`
   - Modify `calculate()` to accept `use_scout_sizing: bool` parameter
   - If `use_scout_sizing=True`: Use Scout sizing instead of 2% rule

2. Update `backend/positions/models.py`:
   - Add optional fields to `Position` dataclass:
     - `scout_entry_price: Optional[float] = None`
     - `soldier_entry_price: Optional[float] = None`
     - `scale_in_triggered: bool = False`
     - `breakeven_guard_active: bool = False`
     - `breakeven_stop_price: Optional[float] = None`
   - Update `to_dict()` and `from_dict()` to handle new fields

3. Update `backend/execution/executor.py`:
   - Check if equity < $50: Use Scout sizing (`use_scout_sizing=True`)
   - Set `position.scout_entry_price = entry_price` when creating new position
   - Log: "Scout entry: ${1.50} @ ${entry_price}, stop: ${stop_price} (42%)"

4. Update `backend/positions/monitor.py`:
   - Add `_check_scale_in_trigger()` method:
     - Check if position has `scale_in_triggered=False`
     - Calculate profit %: `(current_price - scout_entry_price) / scout_entry_price × 100`
     - If profit >= 1.5%: Trigger Soldier scale-in
   - Add `_execute_soldier_scale_in()` method:
     - Create TradeIntent: BUY $3.00 (from env: `SOLDIER_SCALE_IN_SIZE_USD=3.00`)
     - Execute via `execute_trade()`
     - Update position: `soldier_entry_price`, `scale_in_triggered=True`
     - Move stop to breakeven: `breakeven_stop_price = scout_entry_price + fees`
     - Update Kraken stop-loss order to new price
     - Log: "Soldier scale-in: ${3.00} @ ${current_price}, stop moved to breakeven ${breakeven_price}"
   - Call `_check_scale_in_trigger()` in `_update_all_positions()` loop

5. Environment variables (`.env`):
   - `SCOUT_ENTRY_SIZE_USD=1.50`
   - `SCOUT_STOP_LOSS_PCT=42.0`
   - `SOLDIER_SCALE_IN_SIZE_USD=3.00`
   - `SCALE_IN_PROFIT_TRIGGER_PCT=1.5`

6. Redis keys (`backend/redis/keys.py`):
   - No new keys needed (uses existing position keys)

Reference: `backend/positions/monitor.py` has example forced exit logic that can be adapted.

Acceptance Criteria:
- Scout entry: Fixed $1.50 with 42% stop (maintains $0.63 risk)
- Scale-in trigger: +1.5% profit detection works
- Soldier entry: $3.00 scale-in executes correctly
- Breakeven stop: Stop moved to entry+fees after Soldier entry
- Position fields: All new fields stored and retrieved correctly
```

---

### TICKET-403: LIVE_SLOTS System

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-403: LIVE_SLOTS System  
**Branch:** feature/msdd-v3-live-slots

**Prompt:**
```
Implement LIVE_SLOTS system to limit concurrent live positions and route overflow to Shadow Mode.

Requirements:
1. Update `backend/risk/micro_mode.py`:
   - Add `get_live_slots_max(equity: float) -> int`:
     - Balance < $50: Return 1 slot
     - Balance >= $50: Return 2 slots (M2 milestone)
     - Balance >= $100: Return 3 slots (future)
   - Add `get_live_slots_status(equity: float) -> dict`:
     - Returns: `{"max_slots": int, "current_slots": int, "available": bool}`
   - Use env: `LIVE_SLOTS_THRESHOLD_1=50.0`, `LIVE_SLOTS_THRESHOLD_2=100.0`

2. Update `backend/risk/evaluator.py`:
   - In `evaluate_intent()`, after micro mode check:
     - Get current live position count (exclude shadow positions)
     - Get `LIVE_SLOTS_MAX` from `get_live_slots_max(current_equity)`
     - If `current_live_positions >= LIVE_SLOTS_MAX`:
       - Check if Shadow Mode is enabled
       - If Shadow Mode: Return `RiskDecision(approved=False, rejection_reason="live_slots_full_routed_to_shadow")`
       - If Shadow Mode disabled: Return `RiskDecision(approved=False, rejection_reason="live_slots_full")`
     - Log: "LIVE_SLOTS check: {current}/{max} slots used"

3. Update `backend/screener/service.py`:
   - In `_process_auto_execution()`, when `decision.approved=False` and `rejection_reason="live_slots_full_routed_to_shadow"`:
     - Log ORDER_INTENT to Shadow Mode (as if shadow mode was active)
     - Create simulated Fill and position (shadow position)
     - Log: "Signal routed to Shadow Mode: Live slots full ({current}/{max})"

4. Update `backend/positions/tracker.py`:
   - Add `get_live_position_count()` method:
     - Counts positions where `opened_by_strategy_id` is not None (excludes shadow positions)
     - Returns: `int` count

5. Update `backend/api/routes/account.py`:
   - Add `live_slots_active` and `live_slots_max` to account response
   - Calculate from `get_live_slots_status(current_equity)`

6. Redis keys (`backend/redis/keys.py`):
   - `LIVE_SLOTS_COUNT_KEY = "system:live_slots:count"` (optional, for caching)

Reference: `backend/risk/micro_mode.py` has similar threshold logic for micro mode.

Acceptance Criteria:
- LIVE_SLOTS_MAX calculated correctly based on balance
- Overflow signals routed to Shadow Mode when slots full
- Account API returns live slot status
- Position count excludes shadow positions
- Logging shows slot usage clearly
```

---

### TICKET-404: Exit Engine - 48-Hour Opportunity Filter

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-404: Exit Engine - 48-Hour Opportunity Filter  
**Branch:** feature/msdd-v3-48h-opportunity-filter

**Prompt:**
```
Implement 48-hour opportunity filter to auto-close positions that haven't hit TP1 within 48 hours.

Requirements:
1. Update `backend/positions/monitor.py`:
   - Add `_check_48h_opportunity_filter()` method:
     - For each position:
       - Calculate hours since entry: `(current_time - entry_time).total_seconds() / 3600`
       - If `hours_held >= 48`:
         - Check if TP1 was hit (need to track TP1 hit status)
         - If TP1 not hit: Force exit
   - Add TP1 hit tracking:
     - Store in Redis: `position:tp1_hit:{symbol}` (Set to "1" when TP1 hit)
     - Check this key before forcing exit
   - Call `_check_48h_opportunity_filter()` in `_update_all_positions()` loop

2. TP1 Hit Detection:
   - In `_update_all_positions()`, check if `current_price >= tp1_price` (for long positions)
   - If TP1 hit: Set Redis key `position:tp1_hit:{symbol}` = "1"
   - Log: "TP1 hit: {symbol} @ ${current_price} >= ${tp1_price}"

3. Force Exit Logic:
   - Use existing `_force_exit_position()` method
   - Reason: "opportunity_filter_48h"
   - Log: "48-hour opportunity filter: Closing {symbol} (held {hours:.1f}h, TP1 not hit)"

4. Environment variables (`.env`):
   - `OPPORTUNITY_FILTER_HOURS=48`

5. Redis keys (`backend/redis/keys.py`):
   - `POSITION_TP1_HIT_KEY = "position:tp1_hit:{symbol}"`

Reference: `backend/positions/monitor.py` has existing `_check_max_hold_exit()` that can be adapted.

Acceptance Criteria:
- Positions held > 48 hours without TP1 hit are auto-closed
- TP1 hit status tracked correctly
- EXIT_FORCED logged with reason "opportunity_filter_48h"
- P&L calculated and recorded correctly
```

---

### TICKET-405: Exit Engine - ATR Trailing Stop

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-405: Exit Engine - ATR Trailing Stop  
**Branch:** feature/msdd-v3-atr-trailing-stop

**Prompt:**
```
Implement ATR trailing stop that activates at +3% profit and trails price up by 2.0 ATR.

Requirements:
1. Update `backend/positions/models.py`:
   - Add fields to `Position`:
     - `trailing_stop_active: bool = False`
     - `trailing_stop_price: Optional[float] = None`
   - Update `to_dict()` and `from_dict()` to handle new fields

2. Update `backend/positions/monitor.py`:
   - Add `_check_atr_trailing_stop()` method:
     - For each position with `trailing_stop_active=False`:
       - Calculate profit %: `(current_price - entry_price) / entry_price × 100`
       - If profit >= 3.0%: Activate trailing stop
     - For each position with `trailing_stop_active=True`:
       - Get ATR from cached screener results or metadata
       - Calculate new trailing stop: `current_price - (2.0 × ATR)`
       - If new trailing stop > current `trailing_stop_price`: Update (only moves UP)
       - If `current_price <= trailing_stop_price`: Execute exit
   - Call `_check_atr_trailing_stop()` in `_update_all_positions()` loop

3. Trailing Stop Activation:
   - Set `position.trailing_stop_active = True`
   - Set `position.trailing_stop_price = current_price - (2.0 × ATR)`
   - Update Kraken stop-loss order to new trailing stop price
   - Log: "ATR trailing stop activated: {symbol} @ ${current_price}, trailing stop: ${trailing_stop_price}"

4. Trailing Stop Update:
   - Only update if new stop > current stop (never moves down)
   - Update Redis position and Kraken stop-loss order
   - Log: "Trailing stop updated: {symbol} stop moved to ${new_stop} (was ${old_stop})"

5. Trailing Stop Exit:
   - Use `_force_exit_position()` with reason "atr_trailing_stop"
   - Log: "ATR trailing stop triggered: {symbol} @ ${current_price} <= ${trailing_stop_price}"

6. Environment variables (`.env`):
   - `ATR_TRAILING_STOP_TRIGGER_PCT=3.0`
   - `ATR_TRAILING_STOP_MULTIPLIER=2.0`

7. ATR Retrieval:
   - Get ATR from position metadata or cached screener results
   - If ATR unavailable: Skip trailing stop (log warning)

Reference: `backend/positions/monitor.py` has existing forced exit logic and ATR retrieval examples.

Acceptance Criteria:
- Trailing stop activates at +3% profit
- Trailing stop trails price up by 2.0 ATR
- Stop never moves down (only up)
- Exit executes when price drops to trailing stop
- EXIT_FORCED logged with reason "atr_trailing_stop"
```

---

### TICKET-406: Exit Engine - Breakeven Guard

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-406: Exit Engine - Breakeven Guard  
**Branch:** feature/msdd-v3-breakeven-guard

**Prompt:**
```
Implement breakeven guard that moves stop to entry+fees when position reaches +2% profit.

Requirements:
1. Update `backend/positions/monitor.py`:
   - Add `_check_breakeven_guard()` method:
     - For each position with `breakeven_guard_active=False`:
       - Calculate profit %: `(current_price - entry_price) / entry_price × 100`
       - If profit >= 2.0%: Activate breakeven guard
   - Call `_check_breakeven_guard()` in `_update_all_positions()` loop

2. Breakeven Guard Activation:
   - Calculate breakeven price: `entry_price + estimated_fees`
   - Estimated fees: Use 0.26% of entry_price (Kraken maker fee)
   - Set `position.breakeven_guard_active = True`
   - Set `position.breakeven_stop_price = breakeven_price`
   - Update Kraken stop-loss order to new breakeven price
   - Log: "Breakeven guard activated: {symbol} stop moved to ${breakeven_price} (entry: ${entry_price} + fees)"

3. Handle Scout + Soldier Positions:
   - For positions with Soldier scale-in:
   - Use `scout_entry_price` as breakeven reference (first entry)
   - Breakeven = `scout_entry_price + fees`
   - This ensures "playing with house money" after Soldier entry

4. Environment variables (`.env`):
   - `BREAKEVEN_GUARD_TRIGGER_PCT=2.0`
   - `KRAKEN_FEE_PCT=0.26` (maker fee)

5. Integration with Existing Stop-Loss:
   - If trailing stop is active: Breakeven guard takes precedence (wider stop)
   - If breakeven stop > trailing stop: Use breakeven stop
   - Otherwise: Use trailing stop

Reference: `backend/execution/executor.py` has stop-loss order update logic.

Acceptance Criteria:
- Breakeven guard activates at +2% profit
- Stop moved to entry+fees correctly
- Kraken stop-loss order updated
- Works correctly for Scout-only and Scout+Soldier positions
- Activity log shows breakeven guard activation
```

---

### TICKET-407: Live Universe Restriction

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-407: Live Universe Restriction  
**Branch:** feature/msdd-v3-live-universe

**Prompt:**
```
Implement live universe restriction to top 5 high-liquidity pairs (BTC, ETH, SOL, LINK, DOT).

Requirements:
1. Update `backend/ingestor/symbols.py`:
   - Add `get_live_universe() -> List[str]` function:
     - Returns: `["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "DOT/USD"]`
     - Reads from env: `LIVE_UNIVERSE_PAIRS` (comma-separated)
   - Add `is_in_live_universe(symbol: str) -> bool`:
     - Checks if symbol is in live universe list

2. Update `backend/screener/service.py`:
   - In `_process_auto_execution()`, before risk evaluation:
     - Check `is_in_live_universe(signal.symbol)`
     - If NOT in live universe AND trading enabled (not shadow):
       - Skip live execution (don't create TradeIntent)
       - Log: "Signal {symbol} skipped: Not in live universe (shadow mode only)"
     - If in live universe OR shadow mode: Proceed normally

3. Update `backend/risk/evaluator.py`:
   - Add live universe check:
     - If `is_in_live_universe(intent.symbol) == False`:
       - Return `RiskDecision(approved=False, rejection_reason="not_in_live_universe")`

4. Redis keys (`backend/redis/keys.py`):
   - `LIVE_UNIVERSE_KEY = "system:live_universe"` (Set of allowed symbols)

5. Environment variables (`.env`):
   - `LIVE_UNIVERSE_PAIRS=BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOT/USD`

Reference: `backend/ingestor/symbols.py` has symbol filtering logic.

Acceptance Criteria:
- Only top 5 pairs allowed for live execution
- Other pairs still evaluated for Shadow Mode
- Live universe configurable via environment variable
- Clear logging when symbols excluded from live trading
```

---

### TICKET-408: Dynamic Risk Recalculation

**Role:** backend-execute  
**Agent:** backend-execute  
**Ticket:** TICKET-408: Dynamic Risk Recalculation  
**Branch:** feature/msdd-v3-dynamic-risk

**Prompt:**
```
Implement daily risk recalculation based on current equity (maintains $0.63 risk target).

Requirements:
1. Update `backend/risk/account.py`:
   - Add `recalculate_risk_capital()` method:
     - Gets current equity from `AccountTracker.current_equity`
     - Calculates: `risk_capital = current_equity × 0.02`
     - Stores in Redis: `system:risk_capital` (updated daily)
     - Logs: "Risk capital recalculated: ${old} -> ${new} (equity: ${equity})"

2. Update `backend/risk/sizing.py`:
   - Modify `calculate_scout_size()` to use dynamic risk:
     - Get risk capital from Redis (or calculate if not set)
     - Scout size = `risk_capital / 0.42` (42% stop maintains risk)
     - If Scout size < $1.50: Use $1.50 minimum
     - If Scout size > $5.00: Cap at $5.00 (M3 milestone)

3. Add daily recalculation trigger:
   - In `backend/api/main.py` startup:
     - Schedule daily task: Run at midnight UTC
     - Calls `recalculate_risk_capital()`
   - Or: Recalculate on each position sizing call if >24h since last update

4. Redis keys (`backend/redis/keys.py`):
   - `RISK_CAPITAL_KEY = "system:risk_capital"`
   - `RISK_CAPITAL_UPDATED_KEY = "system:risk_capital:updated_at"`

5. Environment variables (`.env`):
   - `RISK_PCT_PER_TRADE=2.0` (already exists)

Reference: `backend/risk/account.py` has equity tracking logic.

Acceptance Criteria:
- Risk capital recalculated daily based on current equity
- Scout size adjusts to maintain $0.63 risk (or 2% of equity)
- Minimum $1.50 Scout size enforced
- Maximum $5.00 Scout size enforced (M3 milestone)
- Logging shows risk recalculation clearly
```

---

### TICKET-409: Frontend - Live Slot Status

**Role:** frontend-execute  
**Agent:** frontend-execute  
**Ticket:** TICKET-409: Frontend - Live Slot Status  
**Branch:** feature/msdd-v3-live-slot-status

**Prompt:**
```
Add Live Slot Status display to PositionPanel showing "X/Y Slots Active".

Requirements:
1. Update `frontend/src/components/PositionPanel.tsx`:
   - Add Live Slot Status section above positions table:
     - Display: "Live Slots: {active}/{max} Active"
     - Color coding:
       - Green: Slots available (active < max)
       - Yellow: 1 slot remaining (active == max - 1)
       - Red: All slots full (active == max)
   - Fetch slot data from account API

2. Update `frontend/src/hooks/useAccount.ts`:
   - Add `live_slots_active` and `live_slots_max` to AccountData interface
   - Parse from API response

3. Update `frontend/src/types/account.ts`:
   - Add fields to AccountState type:
     - `live_slots_active?: number`
     - `live_slots_max?: number`

4. API Integration:
   - Account API already returns slot data (from TICKET-403)
   - No backend changes needed

Reference: `frontend/src/components/PositionPanel.tsx` has similar status displays.

Acceptance Criteria:
- Live Slot Status displays correctly
- Updates in real-time as positions open/close
- Color coding works correctly
- Shows "1/1 Slots Active" when balance < $50
```

---

### TICKET-410: Frontend - Profit Percentage Display

**Role:** frontend-execute  
**Agent:** frontend-execute  
**Ticket:** TICKET-410: Frontend - Profit Percentage Display  
**Branch:** feature/msdd-v3-profit-percentage

**Prompt:**
```
Add profit percentage display showing profit as % of $31.80 wallet (growth-focused).

Requirements:
1. Update `frontend/src/components/AccountPanel.tsx`:
   - Add "Profit % of Wallet" display:
     - Calculate: `(current_equity - 31.80) / 31.80 × 100`
     - Display: "+X.X% of wallet" or "-X.X% of wallet"
     - Color: Green if positive, Red if negative
   - Show alongside existing dollar P&L

2. Update `frontend/src/hooks/useAccount.ts`:
   - Add `profit_pct_of_wallet` calculation:
     - `profit_pct_of_wallet = ((current_equity - 31.80) / 31.80) × 100`
   - Or: Get from backend API if added there

3. Wallet Base Amount:
   - Use constant: `WALLET_BASE_AMOUNT = 31.80`
   - Or: Get from environment/config

4. Display Format:
   - Positive: "+5.2% of wallet" (green)
   - Negative: "-2.1% of wallet" (red)
   - Zero: "0.0% of wallet" (gray)

Reference: `frontend/src/components/AccountPanel.tsx` has existing P&L display logic.

Acceptance Criteria:
- Profit % of wallet displays correctly
- Calculation: (current_equity - 31.80) / 31.80 × 100
- Color coding works (green/red)
- Updates in real-time with account data
```

---

## Execution Order

### Phase 1: Foundation (Week 1)
1. **TICKET-401** (AssetPairs) - **CRITICAL** - Role: backend-execute - Must complete first (needed for costmin validation)
2. **TICKET-407** (Live Universe) - Role: backend-execute - Can run parallel with TICKET-401
3. **TICKET-403** (LIVE_SLOTS) - Role: backend-execute - Depends on position counting (already implemented)

### Phase 2: Entry Model (Week 1-2)
4. **TICKET-402** (Scout/Soldier) - **CRITICAL** - Role: backend-execute - Core entry logic
5. **TICKET-408** (Dynamic Risk) - Role: backend-execute - Can run parallel with TICKET-402

### Phase 3: Exit Engine (Week 2)
6. **TICKET-404** (48-Hour Filter) - Role: backend-execute - Can run parallel with TICKET-405/406
7. **TICKET-406** (Breakeven Guard) - Role: backend-execute - Depends on TICKET-402 (needs position fields)
8. **TICKET-405** (ATR Trailing) - Role: backend-execute - Depends on TICKET-402 (needs position fields)

### Phase 4: Frontend (Week 2)
9. **TICKET-409** (Live Slot Status) - Role: frontend-execute - Depends on TICKET-403
10. **TICKET-410** (Profit Percentage) - Role: frontend-execute - Independent

### Phase 5: QA Verification (Week 2-3)
11. **TICKET-411** (QA Verification) - Role: qa-verify - Verify all tickets meet acceptance criteria
12. **TICKET-412** (Integration Testing) - Role: qa-verify - End-to-end testing of full trade lifecycle

---

## Role Summary Table

| Ticket | Role | Agent Command | Priority | Dependencies |
|--------|------|---------------|----------|--------------|
| TICKET-401 | backend-execute | `/backend-execute` | CRITICAL | None |
| TICKET-402 | backend-execute | `/backend-execute` | CRITICAL | TICKET-401 |
| TICKET-403 | backend-execute | `/backend-execute` | HIGH | None |
| TICKET-404 | backend-execute | `/backend-execute` | HIGH | None |
| TICKET-405 | backend-execute | `/backend-execute` | HIGH | TICKET-402 |
| TICKET-406 | backend-execute | `/backend-execute` | HIGH | TICKET-402 |
| TICKET-407 | backend-execute | `/backend-execute` | MEDIUM | None |
| TICKET-408 | backend-execute | `/backend-execute` | MEDIUM | None |
| TICKET-409 | frontend-execute | `/frontend-execute` | MEDIUM | TICKET-403 |
| TICKET-410 | frontend-execute | `/frontend-execute` | LOW | None |
| TICKET-411 | qa-verify | `/qa-verify` | CRITICAL | TICKET-401-410 |
| TICKET-412 | qa-verify | `/qa-verify` | HIGH | TICKET-401-410 |

---

## Testing & Verification

### Manual Testing Checklist
- [ ] Scout entry executes at $1.50 with 42% stop
- [ ] Scale-in triggers at +1.5% profit
- [ ] Soldier entry executes at $3.00
- [ ] Breakeven guard activates at +2%
- [ ] Trailing stop activates at +3%
- [ ] 48-hour filter closes non-TP1 positions
- [ ] Live slots limit routes overflow to Shadow Mode
- [ ] Only top 5 pairs execute live
- [ ] Costmin validation rejects orders below minimum

### Automated Testing (Future)
- Unit tests for Scout/Soldier sizing
- Unit tests for exit engine triggers
- Integration tests for LIVE_SLOTS routing
- E2E test for full trade lifecycle

---

## Risk Mitigation

### Rollback Plan
- All changes are backward compatible (optional fields)
- Can disable via environment variables
- Shadow Mode routing provides safety net

### Monitoring
- Log all Scout/Soldier entries
- Log all exit engine triggers
- Monitor LIVE_SLOTS usage
- Track costmin validation failures

---

## Success Metrics

### M1: Stability ($35.00)
- 5 successful Scout trades executed
- Zero manual intervention required
- All exits triggered automatically

### M2: Scaling ($50.00)
- Second Live Slot enabled
- 2 concurrent positions working correctly
- No slot conflicts

### M3: Triple Digits ($100.00)
- Scout size increased to $5.00
- Risk capital recalculated correctly
- System handles larger positions

---

### TICKET-411: QA Verification - MSDD v3.0 Features

**Role:** qa-verify  
**Agent:** qa-verify  
**Ticket:** TICKET-411: QA Verification - MSDD v3.0 Features  
**Branch:** qa/msdd-v3-verification

**Prompt:**
```
Verify all MSDD v3.0 features meet acceptance criteria and identify any issues, edge cases, or risks.

Requirements:
1. Review all implemented tickets (TICKET-401 through TICKET-410):
   - Verify acceptance criteria are met
   - Check for edge cases and failure modes
   - Identify security risks (e.g., order size validation, costmin enforcement)
   - Check for operational risks (e.g., stop-loss updates, position tracking)

2. Test Scout & Soldier Entry Model:
   - Verify Scout entry executes at $1.50 with 42% stop
   - Verify scale-in triggers at +1.5% profit
   - Verify Soldier entry executes at $3.00
   - Verify breakeven guard activates at +2%
   - Verify position fields are stored correctly

3. Test Exit Engine:
   - Verify 48-hour filter closes non-TP1 positions
   - Verify ATR trailing stop activates at +3% and trails correctly
   - Verify breakeven guard moves stop to entry+fees
   - Verify exits are logged correctly (EXIT_FORCED)

4. Test LIVE_SLOTS System:
   - Verify slot limit enforced correctly (1 slot when < $50)
   - Verify overflow routes to Shadow Mode
   - Verify slot count excludes shadow positions

5. Test Live Universe Restriction:
   - Verify only top 5 pairs execute live
   - Verify other pairs still work in Shadow Mode

6. Test Kraken AssetPairs Integration:
   - Verify costmin validation rejects orders below minimum
   - Verify fallback to $0.50 default works

7. Test Frontend:
   - Verify Live Slot Status displays correctly
   - Verify Profit Percentage displays correctly

8. Identify Missing Tests:
   - Unit tests for Scout/Soldier sizing
   - Unit tests for exit engine triggers
   - Integration tests for LIVE_SLOTS routing
   - E2E tests for full trade lifecycle

9. Security & Safety Checks:
   - Verify no orders can bypass costmin validation
   - Verify stop-loss orders are updated correctly
   - Verify position tracking is accurate
   - Verify risk calculations are correct

10. Documentation Review:
    - Verify all features are documented
    - Verify API contracts are updated
    - Verify environment variables are documented

Output:
- Findings (bulleted issues/risks)
- Recommended tests (what to add and where)
- Verification commands (exact commands to run and expected results)
- Regression risks (what could break)
```

---

### TICKET-412: Integration Testing - Full Trade Lifecycle

**Role:** qa-verify  
**Agent:** qa-verify  
**Ticket:** TICKET-412: Integration Testing - Full Trade Lifecycle  
**Branch:** qa/msdd-v3-integration-testing

**Prompt:**
```
Create and execute integration tests for the complete MSDD v3.0 trade lifecycle.

Requirements:
1. Test Complete Scout Entry Lifecycle:
   - Signal confirmed → EXECUTION_ALLOWED → ORDER_INTENT → Scout entry ($1.50)
   - Stop-loss placed correctly (42%)
   - Position tracked correctly
   - Activity log shows correct sequence

2. Test Scale-In Lifecycle:
   - Position reaches +1.5% profit
   - Soldier scale-in executes ($3.00)
   - Breakeven guard activates (+2%)
   - Stop-loss updated to breakeven

3. Test Exit Scenarios:
   - 48-hour filter exit (no TP1 hit)
   - ATR trailing stop exit (+3% then price drops)
   - Breakeven guard exit (price drops to breakeven)
   - Manual close (via DELETE endpoint)

4. Test LIVE_SLOTS Overflow:
   - First signal executes live
   - Second signal routes to Shadow Mode
   - Slot status updates correctly

5. Test Live Universe Restriction:
   - Top 5 pair executes live
   - Non-top-5 pair routes to Shadow Mode

6. Test Costmin Validation:
   - Order below costmin rejected
   - Order above costmin executes
   - Fallback to $0.50 works if API fails

7. Test Dynamic Risk Recalculation:
   - Risk capital recalculated daily
   - Scout size adjusts correctly
   - Minimum $1.50 enforced
   - Maximum $5.00 enforced (M3 milestone)

8. Test Frontend Integration:
   - Live Slot Status updates in real-time
   - Profit Percentage displays correctly
   - Position Panel shows all new fields

9. Edge Cases:
   - Multiple positions (when slots available)
   - Position held exactly 48 hours
   - Price exactly at trailing stop
   - Price exactly at breakeven
   - AssetPairs API failure
   - Redis cache miss

10. Performance Testing:
    - PositionMonitor performance (all checks)
    - API response times
    - Redis query performance

Output:
- Test plan (step-by-step test cases)
- Test results (pass/fail for each test)
- Issues found (with reproduction steps)
- Performance metrics
- Recommendations for improvements
```

---

## Notes

- All Position model changes are backward compatible (optional fields)
- Shadow Mode provides safety net for overflow signals
- Exit engine runs in PositionMonitor (already implemented service)
- Growth milestones are manual gates (not automatic)
- Costmin validation prevents Kraken API rejections
