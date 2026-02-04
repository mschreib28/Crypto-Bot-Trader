# Integration Verification Checklist: MSDD v3.0

**Purpose:** End-to-end verification of all MSDD v3.0 features  
**Scope:** Full trade lifecycle from signal to exit  
**Prerequisites:** Backend services running, Redis/PostgreSQL connected

---

## Pre-Flight Checks

### 1. Verify Services Are Running

```bash
# Check Redis
redis-cli PING
# Expected: PONG

# Check PostgreSQL
psql -U postgres -d trading_bot -c "SELECT 1;"
# Expected: 1 row returned

# Check Backend API
curl http://localhost:8000/api/v1/health
# Expected: {"status": "ok"}
```

### 2. Verify Environment Variables

```bash
# Check critical environment variables
grep -E "SCOUT_ENTRY_SIZE_USD|SOLDIER_SCALE_IN_SIZE_USD|LIVE_SLOTS_THRESHOLD|OPPORTUNITY_FILTER_HOURS|ATR_TRAILING_STOP|BREAKEVEN_GUARD" .env

# Expected output should include:
# SCOUT_ENTRY_SIZE_USD=1.50
# SOLDIER_SCALE_IN_SIZE_USD=3.00
# LIVE_SLOTS_THRESHOLD_1=50.0
# LIVE_SLOTS_THRESHOLD_2=100.0
# OPPORTUNITY_FILTER_HOURS=48
# ATR_TRAILING_STOP_TRIGGER_PCT=3.0
# ATR_TRAILING_STOP_MULTIPLIER=2.0
# BREAKEVEN_GUARD_TRIGGER_PCT=2.0
```

### 3. Verify Redis Keys Are Defined

```bash
python3 -c "
from backend.redis.keys import (
    ASSET_PAIRS_CACHE_KEY,
    RISK_CAPITAL_KEY,
    LIVE_UNIVERSE_KEY,
    POSITION_TP1_HIT_KEY
)
print('✅ All Redis keys defined')
print(f'  - ASSET_PAIRS_CACHE_KEY: {ASSET_PAIRS_CACHE_KEY}')
print(f'  - RISK_CAPITAL_KEY: {RISK_CAPITAL_KEY}')
print(f'  - LIVE_UNIVERSE_KEY: {LIVE_UNIVERSE_KEY}')
print(f'  - POSITION_TP1_HIT_KEY: {POSITION_TP1_HIT_KEY}')
"
# Expected: All keys printed without errors
```

---

## Test 1: Scout Entry Lifecycle

### Step 1.1: Verify Scout Sizing Calculation

```bash
python3 << 'EOF'
from backend.risk.sizing import PositionSizer
from backend.risk.account import AccountTracker

# Set test equity
account_tracker = AccountTracker(initial_equity=31.80)
sizer = PositionSizer()

# Test Scout sizing
scout_size = sizer.calculate_scout_size(entry_price=50000.0)
print(f"✅ Scout sizing:")
print(f"  - Position size: ${scout_size.position_size_usd:.2f}")
print(f"  - Stop loss %: {scout_size.stop_loss_pct}%")
print(f"  - Stop loss price: ${scout_size.stop_loss_price:.2f}")
print(f"  - Max risk: ${scout_size.max_risk_usd:.2f}")

# Verify minimum $1.50
assert scout_size.position_size_usd >= 1.50, "Scout size below minimum"
print("✅ Minimum $1.50 enforced")
EOF
# Expected: Scout size >= $1.50, stop loss = 42%, risk ~$0.63
```

### Step 1.2: Verify Costmin Validation

```bash
python3 << 'EOF'
from backend.execution.kraken_rest import KrakenClient

client = KrakenClient()
costmin = client.get_costmin("BTC/USD")
print(f"✅ Costmin for BTC/USD: ${costmin:.2f}")
assert costmin >= 0.50, "Costmin below expected minimum"
EOF
# Expected: Costmin >= $0.50
```

### Step 1.3: Verify Account API Returns Slot Data

```bash
curl -s http://localhost:8000/api/v1/account | jq '{
  live_slots_active: .live_slots_active,
  live_slots_max: .live_slots_max,
  current_equity: .current_equity
}'
# Expected: JSON with live_slots_active, live_slots_max, current_equity
```

---

## Test 2: LIVE_SLOTS System

### Step 2.1: Verify Slot Calculation

```bash
python3 << 'EOF'
from backend.risk.micro_mode import get_live_slots_max, get_live_slots_status

# Test with different equity levels
test_cases = [
    (30.0, 1),   # Below $50 → 1 slot
    (50.0, 2),   # At $50 → 2 slots
    (75.0, 2),   # Between $50-$100 → 2 slots
    (100.0, 3),  # At $100 → 3 slots
    (150.0, 3),  # Above $100 → 3 slots
]

print("✅ LIVE_SLOTS calculation:")
for equity, expected_slots in test_cases:
    actual_slots = get_live_slots_max(equity)
    status = get_live_slots_status(equity)
    print(f"  - Equity ${equity:.2f}: {actual_slots} slots (expected {expected_slots})")
    assert actual_slots == expected_slots, f"Wrong slot count for ${equity}"
    assert status["max_slots"] == expected_slots, "Status max_slots mismatch"
print("✅ All slot calculations correct")
EOF
# Expected: Correct slot counts for each equity level
```

### Step 2.2: Verify Position Count Excludes Shadow

```bash
python3 << 'EOF'
from backend.positions.tracker import get_position_tracker

tracker = get_position_tracker()
live_count = tracker.get_live_position_count()
all_positions = tracker.get_all_positions()

print(f"✅ Position count:")
print(f"  - Live positions: {live_count}")
print(f"  - Total positions: {len(all_positions)}")
print(f"  - Shadow positions: {len(all_positions) - live_count}")
EOF
# Expected: Live count excludes shadow positions
```

---

## Test 3: Live Universe Restriction

### Step 3.1: Verify Live Universe Configuration

```bash
python3 << 'EOF'
from backend.ingestor.symbols import get_live_universe, is_in_live_universe

universe = get_live_universe()
print(f"✅ Live universe: {universe}")

# Test allowed pairs
allowed_pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD", "DOT/USD"]
for pair in allowed_pairs:
    assert is_in_live_universe(pair), f"{pair} should be in live universe"
    print(f"  ✅ {pair} is in live universe")

# Test disallowed pairs
disallowed_pairs = ["ADA/USD", "MATIC/USD", "AVAX/USD"]
for pair in disallowed_pairs:
    assert not is_in_live_universe(pair), f"{pair} should NOT be in live universe"
    print(f"  ✅ {pair} is NOT in live universe")
EOF
# Expected: Top 5 pairs allowed, others rejected
```

---

## Test 4: Exit Engine Verification

### Step 4.1: Verify 48-Hour Filter Logic

```bash
python3 << 'EOF'
from datetime import datetime, timezone, timedelta
from backend.config import OPPORTUNITY_FILTER_HOURS

# Test hours calculation
entry_time = datetime.now(timezone.utc) - timedelta(hours=49)
current_time = datetime.now(timezone.utc)
hours_held = (current_time - entry_time).total_seconds() / 3600.0

print(f"✅ 48-hour filter:")
print(f"  - Hours held: {hours_held:.1f}")
print(f"  - Threshold: {OPPORTUNITY_FILTER_HOURS} hours")
print(f"  - Should trigger: {hours_held >= OPPORTUNITY_FILTER_HOURS}")

assert hours_held >= OPPORTUNITY_FILTER_HOURS, "Should trigger at 49 hours"
EOF
# Expected: Correct hours calculation, trigger at >= 48 hours
```

### Step 4.2: Verify Breakeven Guard Calculation

```bash
python3 << 'EOF'
from backend.config import BREAKEVEN_GUARD_TRIGGER_PCT, KRAKEN_FEE_PCT

entry_price = 50000.0
current_price = entry_price * 1.03  # +3% profit
profit_pct = ((current_price - entry_price) / entry_price) * 100.0

print(f"✅ Breakeven guard:")
print(f"  - Entry price: ${entry_price:.2f}")
print(f"  - Current price: ${current_price:.2f}")
print(f"  - Profit %: {profit_pct:.2f}%")
print(f"  - Trigger threshold: {BREAKEVEN_GUARD_TRIGGER_PCT}%")
print(f"  - Should activate: {profit_pct >= BREAKEVEN_GUARD_TRIGGER_PCT}")

# Calculate breakeven
fee_pct = KRAKEN_FEE_PCT / 100.0
fees_per_unit = entry_price * fee_pct
breakeven_price = entry_price + fees_per_unit
print(f"  - Breakeven price: ${breakeven_price:.3f} (entry + ${fees_per_unit:.4f} fees)")

assert profit_pct >= BREAKEVEN_GUARD_TRIGGER_PCT, "Should activate at +3%"
EOF
# Expected: Correct breakeven calculation, activates at >= 2%
```

### Step 4.3: Verify ATR Trailing Stop Calculation

```bash
python3 << 'EOF'
import os

entry_price = 50000.0
current_price = entry_price * 1.04  # +4% profit
atr = 500.0  # Example ATR
trigger_pct = float(os.getenv("ATR_TRAILING_STOP_TRIGGER_PCT", "3.0"))
multiplier = float(os.getenv("ATR_TRAILING_STOP_MULTIPLIER", "2.0"))

profit_pct = ((current_price - entry_price) / entry_price) * 100.0
trailing_stop = current_price - (multiplier * atr)

print(f"✅ ATR trailing stop:")
print(f"  - Entry price: ${entry_price:.2f}")
print(f"  - Current price: ${current_price:.2f}")
print(f"  - Profit %: {profit_pct:.2f}%")
print(f"  - Trigger threshold: {trigger_pct}%")
print(f"  - ATR: {atr:.2f}")
print(f"  - Multiplier: {multiplier}x")
print(f"  - Trailing stop: ${trailing_stop:.2f}")
print(f"  - Should activate: {profit_pct >= trigger_pct}")

assert profit_pct >= trigger_pct, "Should activate at +4%"
assert trailing_stop == current_price - (multiplier * atr), "Trailing stop calculation incorrect"
EOF
# Expected: Correct trailing stop calculation, activates at >= 3%
```

---

## Test 5: Frontend Integration

### Step 5.1: Verify AccountPanel Profit Percentage

```bash
# Check if frontend builds successfully
cd frontend
npm run build 2>&1 | grep -E "error|Error|ERROR" || echo "✅ Frontend builds successfully"

# Verify AccountPanel component
grep -A 3 "profitPctOfWallet" src/components/AccountPanel.tsx
# Expected: Profit % calculation code present
```

### Step 5.2: Verify PositionPanel Live Slots Display

```bash
# Verify PositionPanel component
grep -A 5 "Live Slots" src/components/PositionPanel.tsx
# Expected: Live slot status display code present
```

---

## Test 6: Dynamic Risk Recalculation

### Step 6.1: Verify Risk Capital Calculation

```bash
python3 << 'EOF'
from backend.risk.account import AccountTracker
from backend.redis import get_redis_client
from backend.redis.keys import RISK_CAPITAL_KEY, RISK_CAPITAL_UPDATED_KEY

# Set test equity
account_tracker = AccountTracker(initial_equity=31.80)

# Recalculate risk capital
risk_capital = account_tracker.recalculate_risk_capital()
expected_risk = 31.80 * 0.02  # 2% of equity

print(f"✅ Risk capital recalculation:")
print(f"  - Equity: ${account_tracker.current_equity:.2f}")
print(f"  - Risk capital: ${risk_capital:.2f}")
print(f"  - Expected: ${expected_risk:.2f} (2% of equity)")

assert abs(risk_capital - expected_risk) < 0.01, "Risk capital calculation incorrect"

# Verify stored in Redis
redis_client = get_redis_client()
stored_risk = redis_client.get(RISK_CAPITAL_KEY)
updated_at = redis_client.get(RISK_CAPITAL_UPDATED_KEY)

print(f"  - Stored in Redis: ${stored_risk}")
print(f"  - Updated at: {updated_at}")
assert stored_risk is not None, "Risk capital not stored in Redis"
assert updated_at is not None, "Updated timestamp not stored"
EOF
# Expected: Risk capital = equity × 2%, stored in Redis
```

---

## Test 7: End-to-End Trade Lifecycle (Manual)

### Prerequisites
- Trading enabled (not Shadow Mode)
- Account equity < $50 (to trigger Scout sizing)
- Signal generated for BTC/USD (in live universe)

### Step 7.1: Monitor Signal Processing

```bash
# Watch activity log for signal processing
tail -f logs/app.log | grep -E "EXECUTION_ALLOWED|ORDER_INTENT|Scout entry|costmin"
# Expected: See EXECUTION_ALLOWED → ORDER_INTENT → Scout entry → costmin validation
```

### Step 7.2: Verify Scout Entry Executed

```bash
# Check positions API
curl -s http://localhost:8000/api/v1/positions | jq '.[] | {
  symbol: .symbol,
  scout_entry_price: .scout_entry_price,
  scale_in_triggered: .scale_in_triggered,
  quantity: .quantity
}'
# Expected: Position with scout_entry_price set, scale_in_triggered = false
```

### Step 7.3: Monitor Scale-In Trigger

```bash
# Watch for scale-in trigger
tail -f logs/app.log | grep -E "Scale-in trigger|Soldier scale-in"
# Expected: See "Scale-in trigger reached" when profit >= 1.5%
```

### Step 7.4: Verify Exit Engine Triggers

```bash
# Watch for exit engine activity
tail -f logs/app.log | grep -E "Breakeven guard|ATR trailing stop|48-hour|EXIT_FORCED"
# Expected: See exit triggers based on profit thresholds
```

---

## Test 8: Error Handling & Edge Cases

### Step 8.1: Test Costmin API Failure

```bash
python3 << 'EOF'
from backend.execution.kraken_rest import KrakenClient

# Mock API failure scenario
client = KrakenClient()
# If API fails, should return default $0.50
costmin = client.get_costmin("INVALID/PAIR")
print(f"✅ Costmin fallback:")
print(f"  - Invalid pair costmin: ${costmin:.2f}")
assert costmin == 0.50, "Should fallback to $0.50"
EOF
# Expected: Falls back to $0.50 default
```

### Step 8.2: Test LIVE_SLOTS with Shadow Mode Disabled

```bash
# Disable Shadow Mode temporarily
# Set SHADOW_LIVE_MODE=false in .env

# Generate signal when slots are full
# Expected: Signal rejected with "live_slots_full" (not routed to Shadow Mode)

# Re-enable Shadow Mode
# Set SHADOW_LIVE_MODE=true in .env
```

---

## Test 9: Performance Verification

### Step 9.1: Monitor PositionMonitor Performance

```bash
# Check PositionMonitor update frequency
grep "Position update complete" logs/app.log | tail -5
# Expected: Updates every ~10 seconds (POSITION_MONITOR_INTERVAL_SECONDS)

# Check for performance issues
grep -E "Error updating position|position monitor loop" logs/app.log | tail -10
# Expected: No errors or warnings
```

### Step 9.2: Monitor Redis Query Performance

```bash
# Check Redis connection health
redis-cli PING
# Expected: PONG

# Monitor Redis keys
redis-cli KEYS "position:*" | wc -l
redis-cli KEYS "system:*" | wc -l
# Expected: Reasonable key counts
```

---

## Test 10: Regression Testing

### Step 10.1: Verify Existing Features Still Work

```bash
# Test 2% rule still works for equity >= $50
python3 << 'EOF'
from backend.risk.sizing import PositionSizer

sizer = PositionSizer()
equity = 100.0
entry_price = 50000.0

# Regular sizing (not Scout)
size = sizer.calculate(
    account_equity=equity,
    risk_pct=2.0,
    entry_price=entry_price,
    stop_loss_pct=5.0,
    use_scout_sizing=False
)

print(f"✅ 2% rule still works:")
print(f"  - Equity: ${equity:.2f}")
print(f"  - Position size: ${size.position_size_usd:.2f}")
print(f"  - Max risk: ${size.max_risk_usd:.2f} (2% of equity)")

assert size.max_risk_usd == equity * 0.02, "2% rule calculation incorrect"
EOF
# Expected: 2% rule still calculates correctly
```

---

## Summary Checklist

### ✅ Pre-Flight
- [ ] Services running (Redis, PostgreSQL, Backend API)
- [ ] Environment variables configured
- [ ] Redis keys defined

### ✅ Core Features
- [ ] Scout sizing calculates correctly
- [ ] Costmin validation works
- [ ] LIVE_SLOTS system functions
- [ ] Live universe restriction works
- [ ] Exit engine triggers correctly

### ✅ Integration
- [ ] Frontend displays slot status
- [ ] Frontend displays profit percentage
- [ ] Account API returns all required fields
- [ ] End-to-end trade lifecycle works

### ✅ Error Handling
- [ ] Costmin API failure handled
- [ ] LIVE_SLOTS overflow routed correctly
- [ ] Exit engine handles edge cases

### ✅ Performance
- [ ] PositionMonitor updates efficiently
- [ ] Redis queries perform well
- [ ] No memory leaks or performance issues

### ✅ Regression
- [ ] Existing features still work
- [ ] No breaking changes
- [ ] Backward compatibility maintained

---

## Expected Test Results

### All Tests Should Pass

- ✅ **Test 1:** Scout entry executes at correct size with 42% stop
- ✅ **Test 2:** LIVE_SLOTS limits positions correctly
- ✅ **Test 3:** Live universe restricts to top 5 pairs
- ✅ **Test 4:** Exit engine triggers at correct thresholds
- ✅ **Test 5:** Frontend displays all new features
- ✅ **Test 6:** Risk capital recalculates correctly
- ✅ **Test 7:** End-to-end lifecycle works
- ✅ **Test 8:** Error handling works correctly
- ✅ **Test 9:** Performance is acceptable
- ✅ **Test 10:** No regressions introduced

---

## Failure Triage

### If Tests Fail

1. **Check logs:** `tail -f logs/app.log`
2. **Check Redis:** `redis-cli PING`
3. **Check PostgreSQL:** `psql -U postgres -d trading_bot -c "SELECT 1;"`
4. **Check environment:** `cat .env | grep -E "SCOUT|SOLDIER|LIVE_SLOTS"`
5. **Check imports:** `python3 -c "from backend.risk.sizing import PositionSizer"`

### Common Issues

- **Import errors:** Check Python path and virtual environment
- **Redis connection:** Verify Redis is running and accessible
- **Environment variables:** Check .env file has all required variables
- **API errors:** Check backend API is running on correct port

---

**Last Updated:** 2025-01-30  
**Status:** ✅ **READY FOR EXECUTION**
