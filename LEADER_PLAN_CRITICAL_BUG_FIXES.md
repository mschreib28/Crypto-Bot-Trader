# Leader Plan: Critical Bug Fixes

**Date:** 2026-02-03  
**Priority:** 🔴 **CRITICAL** - Production bugs causing crashes  
**Status:** ⚠️ **BLOCKING DEPLOYMENT**

---

## Executive Summary

Three critical bugs identified from production logs are preventing successful deployment:

1. **TICKET-501:** `SellSizing` missing `stop_loss_price` attribute (causing forced exits to fail)
2. **TICKET-502:** Circular import between `backend.ingestor.symbols` and `backend.risk.evaluator` (causing ingestor crashes)
3. **TICKET-503:** `RISK_PCT_PER_TRADE` UnboundLocalError in screener service (causing auto-execution failures)

**Impact:** 
- Positions cannot be force-exited (max hold, 48h filter, etc.)
- Ingestor service crashes repeatedly
- Auto-execution fails for all signals

---

## 1. Scope

### In Scope

#### Critical Bug Fixes
- **TICKET-501:** Fix `SellSizing` class to include `stop_loss_price` and `stop_loss_pct` attributes
- **TICKET-502:** Resolve circular import by moving `is_in_live_universe` to a shared module or using lazy imports
- **TICKET-503:** Fix `RISK_PCT_PER_TRADE` UnboundLocalError by removing redundant local import

### Out of Scope

- New features
- Performance optimizations
- Refactoring beyond what's necessary to fix bugs
- Changes to MSDD v3.0 features (these are separate)

---

## 2. File Ownership

### Backend Team (backend-execute)

**TICKET-501:**
- `backend/execution/executor.py` - Fix SellSizing class definition

**TICKET-502:**
- `backend/ingestor/symbols.py` - Move `is_in_live_universe` or use lazy import
- `backend/risk/evaluator.py` - Use lazy import for `is_in_live_universe`

**TICKET-503:**
- `backend/screener/service.py` - Remove redundant local import

---

## 3. Contracts Impacted

### No Breaking Changes

- ✅ All fixes are internal implementation changes
- ✅ No API contract changes
- ✅ No schema changes
- ✅ No shared type changes

---

## 4. Acceptance Criteria

### TICKET-501: SellSizing Missing Attributes

**Acceptance Criteria:**
1. ✅ `SellSizing` class includes `stop_loss_price` attribute
2. ✅ `SellSizing` class includes `stop_loss_pct` attribute
3. ✅ Forced exits (max hold, 48h filter, etc.) execute without AttributeError
4. ✅ Activity log includes stop_loss_price for SELL orders
5. ✅ No regression in BUY order execution

### TICKET-502: Circular Import Resolution

**Acceptance Criteria:**
1. ✅ Ingestor service starts without ImportError
2. ✅ `is_in_live_universe()` function accessible from `backend.risk.evaluator`
3. ✅ Live universe restriction works correctly
4. ✅ No circular import warnings in logs
5. ✅ All services start successfully

### TICKET-503: RISK_PCT_PER_TRADE UnboundLocalError

**Acceptance Criteria:**
1. ✅ Auto-execution creates TradeIntent without UnboundLocalError
2. ✅ `RISK_PCT_PER_TRADE` accessible throughout `_process_auto_execution()` function
3. ✅ Signals execute successfully (DOT/USD, AAVE/USD, etc.)
4. ✅ No regression in signal processing

---

## 5. Dependencies

### Prerequisites

- ✅ Existing codebase (no new dependencies)
- ✅ All fixes are self-contained

### Execution Order

**CRITICAL:** All three tickets must be fixed before deployment:
1. **TICKET-502** (Circular Import) - Must fix first (blocks ingestor startup)
2. **TICKET-501** (SellSizing) - Can fix in parallel with TICKET-503
3. **TICKET-503** (RISK_PCT_PER_TRADE) - Can fix in parallel with TICKET-501

---

## Agent Launch Instructions

### TICKET-501: Fix SellSizing Missing Attributes

**Role:** backend-execute  
**Agent:** `/backend-execute`  
**Ticket:** TICKET-501: Fix SellSizing Missing Attributes  
**Branch:** `bugfix/sellsizing-missing-attributes`

**Prompt:**
```
Fix SellSizing class in backend/execution/executor.py to include stop_loss_price and stop_loss_pct attributes.

Problem:
- Line 263-268 creates a minimal SellSizing class for SELL orders
- Line 454 accesses sizing.stop_loss_price, causing AttributeError
- Forced exits (max hold, 48h filter, etc.) fail with: 'SellSizing' object has no attribute 'stop_loss_price'

Requirements:
1. Update SellSizing class definition (around line 263):
   - Add stop_loss_price attribute (use position.stop_loss_price if available, else None)
   - Add stop_loss_pct attribute (calculate from stop_loss_price and entry_price if available, else 0.0)
   - Ensure all attributes match PositionSize dataclass structure:
     - quantity
     - position_size_usd
     - max_risk_usd
     - stop_loss_price
     - stop_loss_pct

2. For SELL orders (intent.side == "sell"):
   - Get stop_loss_price from position.stop_loss_price if available
   - Calculate stop_loss_pct: ((entry_price - stop_loss_price) / entry_price) × 100 for long positions
   - Set both attributes on sizing object

3. Ensure backward compatibility:
   - If position has no stop_loss_price, use None for stop_loss_price and 0.0 for stop_loss_pct
   - Don't break existing BUY order logic

Reference: backend/risk/sizing.py PositionSize dataclass for attribute structure.

Acceptance Criteria:
- SellSizing includes stop_loss_price and stop_loss_pct attributes
- Forced exits execute without AttributeError
- Activity log includes stop_loss_price for SELL orders
- No regression in BUY order execution
```

---

### TICKET-502: Fix Circular Import

**Role:** backend-execute  
**Agent:** `/backend-execute`  
**Ticket:** TICKET-502: Fix Circular Import  
**Branch:** `bugfix/circular-import-live-universe`

**Prompt:**
```
Fix circular import between backend.ingestor.symbols and backend.risk.evaluator.

Problem:
- backend.risk.evaluator imports is_in_live_universe from backend.ingestor.symbols (line 31)
- backend.ingestor.symbols imports from backend.execution.auth (line 12)
- backend.execution.executor imports from backend.risk.models
- backend.risk.__init__ imports from backend.risk.evaluator
- This creates circular dependency: ingestor.symbols → execution → risk → evaluator → ingestor.symbols
- Error: ImportError: cannot import name 'is_in_live_universe' from partially initialized module

Requirements:
1. Option A (Preferred): Use lazy import in backend/risk/evaluator.py
   - Remove: `from backend.ingestor.symbols import is_in_live_universe` (line 31)
   - Add lazy import inside evaluate_intent() function:
     ```python
     from backend.ingestor.symbols import is_in_live_universe
     ```
   - Only import when needed (inside the function that uses it)

2. Option B (Alternative): Move is_in_live_universe to shared module
   - Create backend/shared/symbols.py (or similar)
   - Move is_in_live_universe and get_live_universe functions
   - Update imports in both ingestor.symbols and risk.evaluator

3. Ensure functionality preserved:
   - Live universe restriction still works
   - No performance degradation
   - All existing tests pass

Reference: backend/risk/evaluator.py line 31, backend/ingestor/symbols.py line 12.

Acceptance Criteria:
- Ingestor service starts without ImportError
- is_in_live_universe() accessible from risk.evaluator
- Live universe restriction works correctly
- No circular import warnings
- All services start successfully
```

---

### TICKET-503: Fix RISK_PCT_PER_TRADE UnboundLocalError

**Role:** backend-execute  
**Agent:** `/backend-execute`  
**Ticket:** TICKET-503: Fix RISK_PCT_PER_TRADE UnboundLocalError  
**Branch:** `bugfix/risk-pct-unbound-local`

**Prompt:**
```
Fix UnboundLocalError for RISK_PCT_PER_TRADE in backend/screener/service.py.

Problem:
- RISK_PCT_PER_TRADE imported at top of file (line 18)
- Used at line 1188 in _process_auto_execution() function
- Redundant local import at line 1258 inside try block: `from backend.config import RISK_PCT_PER_TRADE`
- Python treats RISK_PCT_PER_TRADE as local variable throughout function
- Line 1188 accesses it before assignment, causing UnboundLocalError

Requirements:
1. Remove redundant local import (line 1258):
   - Delete: `from backend.config import RISK_PCT_PER_TRADE`
   - Use the module-level import from line 18 instead

2. Verify usage:
   - Line 1188: `notional_risk_pct=RISK_PCT_PER_TRADE` - should work after fix
   - Line 1278: `risk_pct=RISK_PCT_PER_TRADE` - should work after fix

3. Ensure no other local assignments:
   - Check for any `RISK_PCT_PER_TRADE = ...` assignments in function
   - Remove if found

Reference: backend/screener/service.py lines 18, 1188, 1258.

Acceptance Criteria:
- Auto-execution creates TradeIntent without UnboundLocalError
- RISK_PCT_PER_TRADE accessible throughout _process_auto_execution()
- Signals execute successfully
- No regression in signal processing
```

---

## Execution Order

### Phase 1: Critical Fixes (IMMEDIATE)

1. **TICKET-502** (Circular Import) - **CRITICAL** - Must fix first
   - Blocks ingestor service startup
   - Prevents all data ingestion

2. **TICKET-501** (SellSizing) - **HIGH** - Can fix in parallel
   - Blocks forced exits
   - Positions stuck open

3. **TICKET-503** (RISK_PCT_PER_TRADE) - **HIGH** - Can fix in parallel
   - Blocks auto-execution
   - Signals not executing

---

## Testing & Verification

### Manual Testing Checklist

- [ ] Ingestor service starts without errors
- [ ] Forced exit executes successfully (test max hold exit)
- [ ] Auto-execution creates TradeIntent successfully
- [ ] Activity log includes stop_loss_price for SELL orders
- [ ] Live universe restriction still works
- [ ] No circular import warnings in logs

### Verification Commands

```bash
# 1. Check ingestor starts
docker compose logs ingestor | grep -i "import\|error" | tail -20

# 2. Test forced exit (should not error)
# Monitor logs for forced exit attempts

# 3. Test auto-execution (should not error)
# Monitor logs for auto-execution attempts

# 4. Verify no circular imports
python3 -c "from backend.ingestor.symbols import is_in_live_universe; from backend.risk.evaluator import evaluate_intent; print('✓ No circular import')"
```

---

## Risk Mitigation

### Rollback Plan

- All fixes are isolated to specific functions
- Can revert individual tickets if issues arise
- No database or schema changes

### Monitoring

- Watch ingestor logs for import errors
- Watch executor logs for AttributeError on SELL orders
- Watch screener logs for UnboundLocalError

---

## Success Criteria

- ✅ Ingestor service starts successfully
- ✅ Forced exits execute without AttributeError
- ✅ Auto-execution creates TradeIntent without UnboundLocalError
- ✅ All services healthy
- ✅ No errors in production logs

---

**Status:** 🔴 **CRITICAL - BLOCKING DEPLOYMENT**  
**Next Action:** Fix all three tickets before redeploying
