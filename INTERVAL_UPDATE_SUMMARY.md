# Interval Update Summary

## Overview
Updated all duration intervals throughout the trading bot to match recommended defaults for near-real-time awareness, efficient screener operation, and proper strategy timeframes.

## Changes Made

### 1. Portfolio/Positions/PnL Updates
**Changed from:** 60 seconds  
**Changed to:** 10 seconds (default), max 30 seconds

**Files Modified:**
- `backend/positions/tracker.py` - Position sync interval
- `backend/positions/monitor.py` - Position monitor interval
- `backend/api/main.py` - Updated docstring
- `frontend/src/hooks/useBalance.ts` - Frontend polling (30s → 10s)
- `frontend/src/hooks/usePositions.ts` - Frontend polling (30s → 10s)

**Rationale:**
- Near-real-time awareness for stops, fills, and risk limits
- Critical for risk management and position monitoring
- Cheap operation when pulling only necessary data

### 2. Screener Updates
**Kept at:** 60 seconds (hard max)

**Files Modified:**
- `backend/runner/config.py` - Updated to use centralized config
- `backend/screener/service.py` - Enhanced debouncing documentation

**Rationale:**
- Screener ticks every 60s for status + previews
- Real signal generation happens on candle close boundaries
- Prevents signal spam while maintaining monitoring capability

### 3. Strategy Timeframes
**Verified:** Already correctly configured

- **VWAP Mean Reversion:** 15m entry, 1h filter ✅
- **Volatility Breakout:** 15m entry, 1h filter ✅
- **HTF Trend Pullback:** 1h entry, 4h filter ✅

**Files:**
- `backend/db/seeds/strategies.sql` - Database seed data
- `research/strategies/*/config.py` - Strategy default configs

### 4. Centralized Configuration
**Created:** `backend/intervals/config.py`

**Purpose:**
- Separates UI_REFRESH_INTERVAL, SCREENER_TICK_INTERVAL, and STRATEGY_TIMEFRAME concepts
- Prevents confusion between UI updates, screener ticks, and strategy evaluation
- Provides single source of truth for interval configuration

**Key Constants:**
- `UI_REFRESH_INTERVAL_SECONDS`: 10s (default), max 30s
- `POSITION_SYNC_INTERVAL_SECONDS`: Uses UI_REFRESH_INTERVAL_SECONDS
- `POSITION_MONITOR_INTERVAL_SECONDS`: Uses UI_REFRESH_INTERVAL_SECONDS
- `SCREENER_TICK_INTERVAL_SECONDS`: 60s (default), max 60s
- `STRATEGY_DEFAULT_TIMEFRAMES`: Per-strategy defaults
- `STRATEGY_DEFAULT_HTF_TIMEFRAMES`: Per-strategy HTF defaults

### 5. Signal Debouncing Enhancement
**Enhanced:** `backend/screener/service.py` - `_should_evaluate()` method

**Mechanism:**
- Interval-based evaluation: strategies only evaluate on candle close boundaries
- Prevents signal spam: actionable signals emitted once per candle close
- Cooldown system: prevents duplicate orders until trade placed or invalidation

**How it works:**
1. Screener ticks every 60s for status + previews
2. `_should_evaluate()` checks if new candle has closed since last evaluation
3. Only evaluates symbols with new bar data (candle close detected)
4. Records evaluation timestamp to prevent duplicate evaluations
5. Cooldown keys prevent duplicate orders (4-hour default)

## Configuration

### Environment Variables

```bash
# UI refresh interval (default: 10s, max: 30s)
UI_REFRESH_INTERVAL_SECONDS=10

# Screener tick interval (default: 60s, max: 60s)
SCREENER_INTERVAL_SECONDS=60
```

### Default Values

| Component | Default | Max | Purpose |
|-----------|---------|-----|---------|
| Position Sync | 10s | 30s | Sync positions from Kraken |
| Position Monitor | 10s | 30s | Update P&L and prices |
| Screener Tick | 60s | 60s | Status + previews |
| Strategy Eval | On candle close | N/A | Real signal generation |

## Signal Debouncing Details

### Per-Candle-Close Evaluation
- Strategies evaluate only when a new candle closes
- Prevents duplicate signals within the same candle period
- Ensures actionable signals are emitted once per candle close

### Cooldown System
- 4-hour cooldown per strategy/symbol after signal execution
- Prevents duplicate orders until:
  - Trade is placed, OR
  - Invalidation occurs, OR
  - N candles pass

### Signal Types
- **SETUP signals (informational):** Can be emitted anytime
- **ACTIONABLE signals (BUY/SELL):** Only once per candle close

## Verification

### Backend
```bash
# Check position sync interval
docker exec omni-bot-api python3 -c "from backend.positions.tracker import SYNC_INTERVAL_SECONDS; print(f'Position sync: {SYNC_INTERVAL_SECONDS}s')"

# Check position monitor interval
docker exec omni-bot-api python3 -c "from backend.positions.monitor import UPDATE_INTERVAL_SECONDS; print(f'Position monitor: {UPDATE_INTERVAL_SECONDS}s')"

# Check screener interval
docker exec omni-bot-api python3 -c "from backend.runner.config import SCREENER_INTERVAL_SECONDS; print(f'Screener: {SCREENER_INTERVAL_SECONDS}s')"
```

### Frontend
- Balance polling: 10s (check browser Network tab)
- Positions polling: 10s (check browser Network tab)
- Screener polling: 30s (unchanged, appropriate for signal updates)

## Migration Notes

1. **No database migration required** - Strategy timeframes already correct
2. **No breaking changes** - All intervals are backward compatible
3. **Environment variables** - Optional, defaults are sensible
4. **Frontend rebuild required** - New polling intervals need frontend rebuild

## Testing Recommendations

1. **Position Updates:**
   - Open a position
   - Verify position updates every 10s in UI
   - Check logs for sync frequency

2. **Screener:**
   - Verify screener ticks every 60s
   - Check that strategies only evaluate on candle close
   - Verify no duplicate signals within same candle period

3. **Signal Debouncing:**
   - Trigger a signal
   - Verify signal appears once per candle close
   - Verify cooldown prevents duplicate orders

## Future Enhancements

1. **Candle Close Detection:**
   - Could add explicit candle close detection logic
   - Currently relies on timestamp comparison (works but could be more explicit)

2. **Dynamic Intervals:**
   - Could make intervals configurable per-strategy
   - Currently uses global defaults with per-strategy timeframes

3. **WebSocket Integration:**
   - Could use WebSockets for real-time position updates
   - Currently uses polling (works but less efficient)
