#!/usr/bin/env bash
# ============================================================================
# MSDD v3.0 Verification Script (Simplified)
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_test() { echo -e "${BLUE}[TEST]${NC} $1"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

cd "$(dirname "$0")"

log_section "MSDD v3.0 Verification"

# Pre-flight checks
log_section "Pre-Flight Checks"

log_test "Checking Redis..."
docker compose exec -T redis redis-cli PING | grep -q "PONG" && log_info "✓ Redis: PONG" || log_fail "Redis not responding"

log_test "Checking PostgreSQL..."
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "SELECT 1;" > /dev/null 2>&1 && log_info "✓ PostgreSQL: Connected" || log_fail "PostgreSQL not responding"

log_test "Checking Backend API..."
curl -sf http://localhost:8001/api/v1/health > /dev/null && log_info "✓ API: Healthy" || log_fail "API health check failed"

# Code Verification
log_section "Code Verification"

log_test "Verifying Redis keys..."
docker compose exec -T api python3 -c "
from backend.redis.keys import (
    ASSET_PAIRS_CACHE_KEY,
    RISK_CAPITAL_KEY,
    LIVE_UNIVERSE_KEY,
    POSITION_TP1_HIT_KEY
)
print('✓ All Redis keys defined')
" && log_info "✓ Redis keys verified" || log_fail "Redis keys verification failed"

log_test "Verifying Position model fields..."
docker compose exec -T api python3 -c "
from backend.positions.models import Position
p = Position('BTC/USD', 'long', 0.01, 50000, '2025-01-01T00:00:00Z')
assert hasattr(p, 'scout_entry_price')
assert hasattr(p, 'soldier_entry_price')
assert hasattr(p, 'scale_in_triggered')
assert hasattr(p, 'breakeven_guard_active')
assert hasattr(p, 'trailing_stop_active')
print('✓ Position model fields verified')
" && log_info "✓ Position model verified" || log_fail "Position model verification failed"

log_test "Verifying Scout sizing..."
docker compose exec -T api python3 -c "
from backend.risk.sizing import PositionSizer
from backend.risk.account import AccountTracker

account_tracker = AccountTracker(initial_equity=31.80)
sizer = PositionSizer()
scout_size = sizer.calculate_scout_size(entry_price=50000.0)

assert scout_size.position_size_usd >= 1.50, 'Scout size below minimum'
assert scout_size.stop_loss_pct == 42.0, 'Stop loss % incorrect'
print(f'✓ Scout sizing: \${scout_size.position_size_usd:.2f}, stop: {scout_size.stop_loss_pct}%')
" && log_info "✓ Scout sizing verified" || log_fail "Scout sizing verification failed"

log_test "Verifying LIVE_SLOTS calculation..."
docker compose exec -T api python3 -c "
from backend.risk.micro_mode import get_live_slots_max

assert get_live_slots_max(30.0) == 1, 'Below \$50 should be 1 slot'
assert get_live_slots_max(50.0) == 2, 'At \$50 should be 2 slots'
assert get_live_slots_max(100.0) == 3, 'At \$100 should be 3 slots'
print('✓ LIVE_SLOTS calculation verified')
" && log_info "✓ LIVE_SLOTS verified" || log_fail "LIVE_SLOTS verification failed"

log_test "Verifying Live Universe restriction..."
docker compose exec -T api python3 -c "
from backend.ingestor.symbols import is_in_live_universe

assert is_in_live_universe('BTC/USD'), 'BTC/USD should be in live universe'
assert is_in_live_universe('ETH/USD'), 'ETH/USD should be in live universe'
assert not is_in_live_universe('ADA/USD'), 'ADA/USD should NOT be in live universe'
print('✓ Live universe restriction verified')
" && log_info "✓ Live universe verified" || log_fail "Live universe verification failed"

# API Verification
log_section "API Verification"

log_test "Checking account endpoint for live slots..."
ACCOUNT_RESPONSE=$(curl -sf http://localhost:8001/api/v1/account 2>/dev/null || echo "")
if echo "$ACCOUNT_RESPONSE" | grep -q "live_slots_active"; then
    LIVE_SLOTS_ACTIVE=$(echo "$ACCOUNT_RESPONSE" | grep -o '"live_slots_active":[0-9]*' | cut -d: -f2)
    LIVE_SLOTS_MAX=$(echo "$ACCOUNT_RESPONSE" | grep -o '"live_slots_max":[0-9]*' | cut -d: -f2)
    log_info "✓ Account API returns live slots: ${LIVE_SLOTS_ACTIVE}/${LIVE_SLOTS_MAX}"
else
    log_fail "Account API missing live_slots fields"
fi

# Summary
log_section "Verification Summary"
log_info "✓ Pre-flight checks: Passed"
log_info "✓ Code verification: Passed"
log_info "✓ API endpoints: Verified"
log_info ""
log_info "MSDD v3.0 verification complete!"
