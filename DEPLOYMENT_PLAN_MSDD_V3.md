# Deployment Plan: MSDD v3.0 to Production Server

**Date:** 2025-01-30  
**Server:** ark@corpus  
**Status:** ⚠️ **CODE SYNC REQUIRED**

---

## Current Status

### ✅ Infrastructure: READY
- All Docker containers running and healthy
- PostgreSQL, Redis, API, Frontend all operational
- Database migrations applied
- Kraken API connected

### ❌ Code: NOT DEPLOYED
Verification tests confirm MSDD v3.0 code is **NOT** on the server:
- ❌ Redis keys missing (`ASSET_PAIRS_CACHE_KEY`, etc.)
- ❌ Position model missing new fields
- ❌ Scout sizing method not found
- ❌ LIVE_SLOTS functions not present
- ❌ Live universe restriction not implemented
- ❌ Account API missing live slots fields

---

## Deployment Steps

### Step 1: Sync Code to Server

**Option A: Git Push/Pull (Recommended if using Git)**

```bash
# On local machine
cd ~/Documents/Projects/Personal/Crypto\ Bot\ Trading
git add .
git commit -m "MSDD v3.0: All tickets completed and verified"
git push origin main  # or your branch name

# On server
ssh ark@corpus
cd ~/crypto-bot
git pull origin main
```

**Option B: rsync (Fast, excludes unnecessary files)**

```bash
# From local machine
cd ~/Documents/Projects/Personal/Crypto\ Bot\ Trading
rsync -avz --exclude 'node_modules' --exclude '__pycache__' --exclude '.git' \
  --exclude '*.pyc' --exclude '.env' --exclude 'dist' \
  ./ ark@corpus:~/crypto-bot/
```

**Option C: Selective File Copy (Most Targeted)**

```bash
# Copy only MSDD v3.0 changed files
cd ~/Documents/Projects/Personal/Crypto\ Bot\ Trading

# Backend files
scp backend/risk/sizing.py \
    backend/positions/models.py \
    backend/positions/monitor.py \
    backend/risk/micro_mode.py \
    backend/execution/executor.py \
    backend/execution/kraken_rest.py \
    backend/ingestor/symbols.py \
    backend/risk/account.py \
    backend/api/routes/account.py \
    backend/redis/keys.py \
    ark@corpus:~/crypto-bot/backend/

# Frontend files
scp frontend/src/components/PositionPanel.tsx \
    frontend/src/components/AccountPanel.tsx \
    ark@corpus:~/crypto-bot/frontend/src/components/

# Test files (if exists)
scp backend/tests/integration/test_msdd_v3_lifecycle.py \
    ark@corpus:~/crypto-bot/backend/tests/integration/ 2>/dev/null || true
```

### Step 2: Rebuild Containers

After syncing code:

```bash
ssh ark@corpus
cd ~/crypto-bot
./deploy.sh --rebuild
```

This will:
- Rebuild Docker images with new code
- Restart all containers
- Run database migrations
- Seed strategies

### Step 3: Verify Deployment

After rebuild:

```bash
ssh ark@corpus
cd ~/crypto-bot
./verify_msdd_v3.sh
```

Expected output:
```
✓ Redis: PONG
✓ PostgreSQL: Connected
✓ API: Healthy
✓ Redis keys verified
✓ Position model verified
✓ Scout sizing verified
✓ LIVE_SLOTS verified
✓ Live universe verified
✓ Account API returns live slots: X/Y
```

### Step 4: Run Integration Tests

```bash
ssh ark@corpus
cd ~/crypto-bot
docker compose exec -T api pytest backend/tests/integration/test_msdd_v3_lifecycle.py -v
```

---

## Files That Must Be Deployed

### Critical Backend Files

1. **`backend/risk/sizing.py`**
   - Contains `calculate_scout_size()` method
   - Dynamic risk recalculation logic

2. **`backend/positions/models.py`**
   - Position model with new fields:
     - `scout_entry_price`
     - `soldier_entry_price`
     - `scale_in_triggered`
     - `breakeven_guard_active`
     - `breakeven_stop_price`
     - `trailing_stop_active`
     - `trailing_stop_price`

3. **`backend/positions/monitor.py`**
   - Exit engine implementations:
     - `_check_scale_in_trigger()`
     - `_execute_soldier_scale_in()`
     - `_check_48h_opportunity_filter()`
     - `_check_atr_trailing_stop()`
     - `_check_breakeven_guard()`

4. **`backend/risk/micro_mode.py`**
   - `get_live_slots_max()` function
   - `get_live_slots_status()` function

5. **`backend/execution/executor.py`**
   - Costmin validation logic
   - Scout entry logic (`use_scout_sizing`)

6. **`backend/execution/kraken_rest.py`**
   - `get_asset_pairs()` method
   - `get_costmin()` method with Redis caching

7. **`backend/ingestor/symbols.py`**
   - `get_live_universe()` function
   - `is_in_live_universe()` function

8. **`backend/risk/account.py`**
   - `recalculate_risk_capital()` method

9. **`backend/api/routes/account.py`**
   - Account API endpoint with `live_slots_active` and `live_slots_max`

10. **`backend/redis/keys.py`**
    - New Redis keys:
      - `ASSET_PAIRS_CACHE_KEY`
      - `RISK_CAPITAL_KEY`
      - `RISK_CAPITAL_UPDATED_KEY`
      - `LIVE_UNIVERSE_KEY`
      - `POSITION_TP1_HIT_KEY`

### Frontend Files

1. **`frontend/src/components/PositionPanel.tsx`**
   - Live slot status display

2. **`frontend/src/components/AccountPanel.tsx`**
   - Profit percentage display

3. **`frontend/src/hooks/useAccount.ts`** (if modified)
   - Account hook with live slots data

4. **`frontend/src/types/account.ts`** (if modified)
   - Type definitions for live slots

---

## Environment Variables (Optional)

These can be added to `.env` on server, but defaults will work:

```bash
# Scout & Soldier Entry
SCOUT_ENTRY_SIZE_USD=1.50
SCOUT_STOP_LOSS_PCT=42.0
SOLDIER_SCALE_IN_SIZE_USD=3.00
SCALE_IN_PROFIT_TRIGGER_PCT=1.5

# LIVE_SLOTS
LIVE_SLOTS_THRESHOLD_1=50.0
LIVE_SLOTS_THRESHOLD_2=100.0

# Exit Engine
OPPORTUNITY_FILTER_HOURS=48
ATR_TRAILING_STOP_TRIGGER_PCT=3.0
ATR_TRAILING_STOP_MULTIPLIER=2.0
BREAKEVEN_GUARD_TRIGGER_PCT=2.0
KRAKEN_FEE_PCT=0.26

# Live Universe
LIVE_UNIVERSE_PAIRS=BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOT/USD
```

---

## Verification Checklist

After deployment, verify:

- [ ] All services healthy (`docker compose ps`)
- [ ] Redis keys defined (run `verify_msdd_v3.sh`)
- [ ] Position model has new fields
- [ ] Scout sizing works
- [ ] LIVE_SLOTS calculation works
- [ ] Live universe restriction works
- [ ] Account API returns live slots
- [ ] Frontend displays live slot status
- [ ] Frontend displays profit percentage
- [ ] Integration tests pass

---

## Rollback Plan

If deployment fails:

1. **Stop services:**
   ```bash
   ssh ark@corpus
   cd ~/crypto-bot
   ./deploy.sh --stop
   ```

2. **Restore from backup** (if you have one):
   ```bash
   git checkout HEAD~1  # or restore from backup
   ```

3. **Rebuild:**
   ```bash
   ./deploy.sh --rebuild
   ```

---

## Post-Deployment Monitoring

After successful deployment:

1. **Monitor logs:**
   ```bash
   ssh ark@corpus
   cd ~/crypto-bot
   ./deploy.sh --logs
   ```

2. **Check API health:**
   ```bash
   curl http://corpus:8001/api/v1/health
   ```

3. **Monitor for errors:**
   - Watch for costmin validation failures
   - Watch for LIVE_SLOTS rejections
   - Watch for exit engine triggers

4. **Test with Shadow Mode first:**
   - Enable Shadow Mode
   - Generate test signals
   - Verify Scout/Soldier entry works
   - Verify exit engine triggers correctly

---

## Quick Deploy Command

If using rsync (Option B):

```bash
cd ~/Documents/Projects/Personal/Crypto\ Bot\ Trading && \
rsync -avz --exclude 'node_modules' --exclude '__pycache__' --exclude '.git' \
  --exclude '*.pyc' --exclude '.env' --exclude 'dist' \
  ./ ark@corpus:~/crypto-bot/ && \
ssh ark@corpus "cd ~/crypto-bot && ./deploy.sh --rebuild && ./verify_msdd_v3.sh"
```

---

**Status:** ⚠️ **READY FOR CODE SYNC**  
**Next Action:** Sync code using one of the methods above
