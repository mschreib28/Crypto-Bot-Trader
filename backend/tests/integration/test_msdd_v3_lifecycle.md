# MSDD v3.0 Integration Test Plan

## Test Plan Overview

This document outlines comprehensive integration tests for the complete MSDD v3.0 trade lifecycle, covering all critical paths from signal confirmation through exit scenarios.

## Test Infrastructure

- **Framework:** pytest with pytest-asyncio
- **Mocking:** unittest.mock for external dependencies
- **Coverage:** All MSDD v3.0 features including Scout/Soldier entries, exit scenarios, LIVE_SLOTS, costmin validation, and dynamic risk recalculation

## Test Categories

### 1. Complete Scout Entry Lifecycle
- Signal confirmed → EXECUTION_ALLOWED → ORDER_INTENT → Scout entry ($1.50)
- Stop-loss placed correctly (42%)
- Position tracked correctly
- Activity log shows correct sequence

### 2. Scale-In Lifecycle
- Position reaches +1.5% profit
- Soldier scale-in executes ($3.00)
- Breakeven guard activates (+2%)
- Stop-loss updated to breakeven

### 3. Exit Scenarios
- 48-hour filter exit (no TP1 hit)
- ATR trailing stop exit (+3% then price drops)
- Breakeven guard exit (price drops to breakeven)
- Manual close (via DELETE endpoint)

### 4. LIVE_SLOTS Overflow
- First signal executes live
- Second signal routes to Shadow Mode
- Slot status updates correctly

### 5. Live Universe Restriction
- Top 5 pair executes live
- Non-top-5 pair routes to Shadow Mode

### 6. Costmin Validation
- Order below costmin rejected
- Order above costmin executes
- Fallback to $0.50 works if API fails

### 7. Dynamic Risk Recalculation
- Risk capital recalculated daily
- Scout size adjusts correctly
- Minimum $1.50 enforced
- Maximum $5.00 enforced (M3 milestone)

### 8. Frontend Integration
- Live Slot Status updates in real-time
- Profit Percentage displays correctly
- Position Panel shows all new fields

### 9. Edge Cases
- Multiple positions (when slots available)
- Position held exactly 48 hours
- Price exactly at trailing stop
- Price exactly at breakeven
- AssetPairs API failure
- Redis cache miss

### 10. Performance Testing
- PositionMonitor performance (all checks)
- API response times
- Redis query performance

## Test Execution

Run all tests:
```bash
pytest backend/tests/integration/test_msdd_v3_lifecycle.py -v
```

Run specific test category:
```bash
pytest backend/tests/integration/test_msdd_v3_lifecycle.py::TestScoutEntryLifecycle -v
```

Run with coverage:
```bash
pytest backend/tests/integration/test_msdd_v3_lifecycle.py --cov=backend --cov-report=html
```

## Expected Results

All tests should pass, demonstrating:
- Complete lifecycle coverage
- Correct state transitions
- Proper error handling
- Performance within acceptable limits
