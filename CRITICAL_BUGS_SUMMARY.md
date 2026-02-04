# Critical Bugs Summary - Production Deployment Failures

**Date:** 2026-02-03  
**Status:** 🔴 **CRITICAL - BLOCKING PRODUCTION**

---

## Bugs Identified from Production Logs

### 🔴 Bug #1: SellSizing Missing Attributes (TICKET-501)

**Error:**
```
AttributeError: 'SellSizing' object has no attribute 'stop_loss_price'
File "/app/backend/execution/executor.py", line 454
```

**Root Cause:**
- `SellSizing` class created at line 263-268 is missing `stop_loss_price` and `stop_loss_pct` attributes
- Line 454 tries to access `sizing.stop_loss_price` for activity logging
- Affects all forced exits (max hold, 48h filter, trailing stop, etc.)

**Impact:** 
- Positions cannot be force-exited
- Positions stuck open indefinitely
- Max hold filter not working
- 48-hour opportunity filter not working

**Fix Required:**
- Add `stop_loss_price` and `stop_loss_pct` to `SellSizing` class
- Get values from `position.stop_loss_price` if available

---

### 🔴 Bug #2: Circular Import (TICKET-502)

**Error:**
```
ImportError: cannot import name 'is_in_live_universe' from partially initialized module 'backend.ingestor.symbols' (most likely due to a circular import)
File "/app/backend/risk/evaluator.py", line 31
```

**Root Cause:**
- Circular dependency chain:
  - `backend.ingestor.symbols` → imports from `backend.execution.auth`
  - `backend.execution.executor` → imports from `backend.risk.models`
  - `backend.risk.__init__` → imports from `backend.risk.evaluator`
  - `backend.risk.evaluator` → imports `is_in_live_universe` from `backend.ingestor.symbols`
- Creates circular import when ingestor tries to initialize

**Impact:**
- Ingestor service crashes on startup
- Continuously restarts (exited with code 1)
- No market data ingestion
- System cannot function

**Fix Required:**
- Use lazy import in `backend/risk/evaluator.py`
- Move import inside function that uses it

---

### 🔴 Bug #3: RISK_PCT_PER_TRADE UnboundLocalError (TICKET-503)

**Error:**
```
UnboundLocalError: cannot access local variable 'RISK_PCT_PER_TRADE' where it is not associated with a value
File "/app/backend/screener/service.py", line 1188
```

**Root Cause:**
- `RISK_PCT_PER_TRADE` imported at module level (line 18)
- Redundant local import at line 1258: `from backend.config import RISK_PCT_PER_TRADE`
- Python treats it as local variable throughout function
- Line 1188 accesses it before local assignment, causing error

**Impact:**
- Auto-execution fails for all signals
- DOT/USD BUY signal fails
- AAVE/USD BUY signal fails
- No trades execute automatically

**Fix Required:**
- Remove redundant local import at line 1258
- Use module-level import from line 18

---

## Deployment Status

### Current State
- ❌ Ingestor: Crashing (circular import)
- ❌ Forced Exits: Failing (SellSizing missing attributes)
- ❌ Auto-Execution: Failing (UnboundLocalError)
- ✅ API: Running (but cannot process signals)
- ✅ Frontend: Running
- ✅ Database: Healthy
- ✅ Redis: Healthy

### Blocking Issues
1. **Cannot ingest market data** (ingestor crashes)
2. **Cannot exit positions** (forced exits fail)
3. **Cannot execute signals** (auto-execution fails)

---

## Fix Priority

1. **TICKET-502** (Circular Import) - **CRITICAL** - Fix first
   - Blocks entire system (no data ingestion)
   
2. **TICKET-501** (SellSizing) - **HIGH** - Fix second
   - Blocks position management
   
3. **TICKET-503** (RISK_PCT_PER_TRADE) - **HIGH** - Fix second
   - Blocks signal execution

---

## Next Steps

1. Fix all three bugs (see LEADER_PLAN_CRITICAL_BUG_FIXES.md)
2. Test fixes locally
3. Deploy to server
4. Verify all services healthy
5. Monitor logs for 24 hours

---

**Status:** 🔴 **CRITICAL - REQUIRES IMMEDIATE FIX**
