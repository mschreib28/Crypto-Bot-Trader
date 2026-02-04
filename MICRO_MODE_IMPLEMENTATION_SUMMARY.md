# Micro-Account Mode Implementation Summary

## Overview
Implemented micro-account mode to handle small account sizes (<$250) where standard 2% risk per trade creates issues:
- Position sizing produces notional below Kraken minimums
- Fees dominate "R" (risk/reward ratio)
- Tiny stops cause constant stop-outs

## Implementation

### 1. Micro Mode Detection
**File:** `backend/risk/micro_mode.py`

**Threshold:** Default $250 (configurable via `MICRO_MODE_THRESHOLD`)

**Function:**
```python
is_micro_mode(equity: float) -> bool
```

**Behavior:**
- Returns `True` if equity < $250
- Automatically activates when account drops below threshold

### 2. Minimum Stop Distance Enforcement
**Purpose:** Avoid tiny stops that cause constant stop-outs

**Implementation:**
- `check_min_stop_distance()` validates stop distance
- In micro mode: Minimum stop = 2.0 ATR (configurable via `MICRO_MODE_MIN_STOP_ATR`)
- Fallback: 5% minimum if ATR not available
- Integrated into `PositionSizer.calculate()` - returns `None` if stop too close

**Example:**
```python
# With equity=$31.79, 2% risk = $0.64
# If stop is only 1.0 ATR away, trade is skipped
# Minimum required: 2.0 ATR
```

### 3. Minimum Notional Logic
**Purpose:** Ensure position size meets minimum requirements

**Implementation:**
- `check_min_notional()` validates position size
- Minimum notional: $5.0 (configurable via `MICRO_MODE_MIN_NOTIONAL`)
- If calculated size < $5.0:
  - If fixed size ($5.0) <= 20% of equity: Use fixed size
  - Otherwise: Skip trade

**Example:**
```python
# Equity=$31.79, 2% risk = $0.64
# With 5% stop: position_size = $0.64 / 0.05 = $12.80 ✅ (proceeds)
# With 10% stop: position_size = $0.64 / 0.10 = $6.40 ✅ (proceeds)
# With 15% stop: position_size = $0.64 / 0.15 = $4.27 ❌ (below $5.0)
#   → Try fixed $5.0 size (if <= 20% of equity = $6.36) ✅ (proceeds with $5.0)
```

### 4. Maximum Positions Limit
**Purpose:** Reduce frequency aggressively (max 1 position open total)

**Implementation:**
- `check_max_positions()` checks current position count
- In micro mode: Max 1 position open (configurable via `MICRO_MODE_MAX_POSITIONS`)
- Integrated into `evaluate_intent()` - rejects new trades if limit reached

**Example:**
```python
# If 1 position already open, new BUY signals are rejected
# Reason: "micro_mode_max_positions_reached: 1 >= 1"
```

### 5. UI Banner
**Purpose:** Visible indicator when micro mode is active

**Implementation:**
- Added `micro_mode` status to `/api/v1/account` endpoint
- Added `MicroModeBanner` component to `Header.tsx`
- Displays yellow banner with warning message when active

**Banner Message:**
```
⚠️ MICRO MODE ACTIVE
MICRO MODE ACTIVE: Equity $31.79 < $250.00 threshold. Max 1 position, min stop 2.0ATR, min notional $5.00
```

## Configuration

### Environment Variables

```bash
# Micro mode threshold (default: $250)
MICRO_MODE_THRESHOLD=250.0

# Minimum stop distance in ATR (default: 2.0)
MICRO_MODE_MIN_STOP_ATR=2.0

# Minimum notional size (default: $5.0)
MICRO_MODE_MIN_NOTIONAL=5.0

# Maximum positions in micro mode (default: 1)
MICRO_MODE_MAX_POSITIONS=1
```

## Code Changes

### Backend

**New File:**
- `backend/risk/micro_mode.py` - Micro mode detection and validation logic

**Modified Files:**
- `backend/risk/sizing.py` - Added micro mode checks to `calculate()`
- `backend/risk/evaluator.py` - Added max positions check
- `backend/execution/executor.py` - Extract ATR/stop from metadata, handle None sizing
- `backend/api/routes/account.py` - Added micro_mode status to response
- `backend/risk/__init__.py` - Export micro mode functions

### Frontend

**Modified Files:**
- `frontend/src/components/Header.tsx` - Added MicroModeBanner component
- `frontend/src/hooks/useAccount.ts` - Added micro_mode to AccountState interface

## Flow Diagram

```
Trade Intent Generated
    ↓
Risk Evaluator (evaluate_intent)
    ↓
Check Micro Mode? (equity < $250)
    ↓ YES
    ├─ Check Max Positions (current_count >= 1?)
    │   └─ YES → REJECT ("micro_mode_max_positions_reached")
    │   └─ NO → Continue
    ↓
Position Sizer (calculate)
    ↓
Check Micro Mode?
    ↓ YES
    ├─ Check Min Stop Distance (stop < 2.0 ATR?)
    │   └─ YES → RETURN None (skip trade)
    │   └─ NO → Continue
    ├─ Check Min Notional (size < $5.0?)
    │   ├─ YES → Try fixed $5.0 size
    │   │   └─ If fixed <= 20% equity → Use $5.0
    │   │   └─ Otherwise → RETURN None (skip trade)
    │   └─ NO → Use calculated size
    ↓
Execute Trade (if sizing not None)
```

## Example Scenarios

### Scenario 1: Stop Too Close
```
Equity: $31.79
Risk: 2% = $0.64
Entry: $50,000 (BTC/USD)
ATR: $500
Stop: $49,500 (1.0% = 1.0 ATR)

Result: ❌ SKIPPED
Reason: "stop_too_close: 1.0ATR < 2.0ATR minimum"
```

### Scenario 2: Notional Too Small
```
Equity: $31.79
Risk: 2% = $0.64
Entry: $50,000
Stop: 15% → Position size = $4.27

Result: ✅ PROCEEDS with $5.0 fixed size
Reason: Fixed $5.0 <= 20% of equity ($6.36)
```

### Scenario 3: Max Positions Reached
```
Equity: $31.79
Current positions: 1 (ETH/USD)
New signal: BTC/USD BUY

Result: ❌ REJECTED
Reason: "micro_mode_max_positions_reached: 1 >= 1"
```

### Scenario 4: Valid Trade
```
Equity: $31.79
Risk: 2% = $0.64
Entry: $50,000
Stop: 5% → Position size = $12.80
ATR: $500 → Stop distance = 2.5 ATR ✅

Result: ✅ PROCEEDS
Size: $12.80 (meets all requirements)
```

## Testing Recommendations

### 1. Micro Mode Detection
```python
# Test with equity < $250
equity = 31.79
assert is_micro_mode(equity) == True

# Test with equity >= $250
equity = 250.0
assert is_micro_mode(equity) == False
```

### 2. Stop Distance Check
```python
# Test stop too close
entry = 50000.0
stop = 49500.0  # 1.0% = 1.0 ATR
atr = 500.0
valid, reason = check_min_stop_distance(entry, stop, atr)
assert valid == False

# Test stop valid
stop = 49000.0  # 2.0% = 2.0 ATR
valid, reason = check_min_stop_distance(entry, stop, atr)
assert valid == True
```

### 3. Notional Check
```python
# Test below minimum
equity = 31.79
size = 4.27
proceed, adjusted, reason = check_min_notional(size, equity)
assert proceed == True
assert adjusted == 5.0  # Uses fixed size

# Test above minimum
size = 12.80
proceed, adjusted, reason = check_min_notional(size, equity)
assert proceed == True
assert adjusted == 12.80  # Uses calculated size
```

### 4. Max Positions Check
```python
# Test max reached
count = 1
can_open, reason = check_max_positions(count)
assert can_open == False

# Test can open
count = 0
can_open, reason = check_max_positions(count)
assert can_open == True
```

## Verification Commands

### Check Micro Mode Status
```bash
curl http://corpus:8001/api/v1/account | python3 -m json.tool | grep -A 10 micro_mode
```

### Check Logs for Micro Mode Rejections
```bash
docker logs omni-bot-api 2>&1 | grep -i "micro mode"
```

### Test Position Count
```bash
docker exec omni-bot-api python3 << 'PYEOF'
from backend.positions.tracker import get_position_tracker
tracker = get_position_tracker()
positions = tracker.get_all_positions()
print(f"Current positions: {len(positions)}")
PYEOF
```

## Files Modified

**Backend:**
- `backend/risk/micro_mode.py` (new)
- `backend/risk/sizing.py`
- `backend/risk/evaluator.py`
- `backend/execution/executor.py`
- `backend/api/routes/account.py`
- `backend/risk/__init__.py`

**Frontend:**
- `frontend/src/components/Header.tsx`
- `frontend/src/hooks/useAccount.ts`

## Migration Notes

1. **No database migration required** - All logic is code-based
2. **Backward compatible** - Micro mode only activates when equity < threshold
3. **Configurable** - All thresholds can be adjusted via environment variables
4. **Automatic** - No manual activation needed, detects based on equity

## Benefits

1. **Prevents Fee Domination:** Minimum notional ensures fees don't dominate risk
2. **Reduces Stop-Outs:** Minimum stop distance prevents constant stop-outs
3. **Controls Frequency:** Max 1 position prevents over-trading
4. **Transparent:** UI banner clearly shows when micro mode is active
5. **Auditable:** All rejections logged with specific reasons
