# Leader Plan: System Recovery After Server Restart

## Problem Statement

After server restart, the system shows:
- **HTTP 500 errors** across all UI components (Positions, Screener Signals, Strategy Setup)
- **System Health: Unhealthy** - All components showing red (Redis, Ingestor, Database, Data Feed)
- **Root Cause**: Redis and Postgres containers exited and are not restarting automatically

## 1. Scope

### In Scope
- Diagnose why Redis and Postgres containers exited
- Fix container restart policies in docker-compose.yml
- Restore all services to healthy state
- Verify UI endpoints return data correctly
- Ensure previous fixes (stablecoin filtering, stop-loss precision) still function
- Add monitoring/alerting for container health

### Out of Scope
- Changing service architectures
- Modifying API contracts or database schemas
- Performance optimization (focus on recovery first)
- Frontend changes (backend recovery is priority)

## 2. File Ownership

### Infrastructure (infra/)
- `docker-compose.yml` - Add restart policies for Redis and Postgres
- `Dockerfile.api` - No changes needed
- `Dockerfile.ingestor` - No changes needed

### Backend (backend/)
- `backend/api/routes/health.py` - Verify health check logic handles container restarts
- `backend/redis/` - Verify connection retry logic works correctly
- `backend/db/` - Verify database connection pooling handles reconnects

### No Changes Required
- Frontend (frontend/)
- Research (research/)
- Contracts (contracts/)

## 3. Contracts Impacted

### API Endpoints
- `/api/v1/health` - Should return healthy status after recovery
- `/api/v1/health/detailed` - Should show all components as healthy
- `/api/v1/status` - Should show Redis and DB connected
- `/api/v1/screener` - Should return screener results (depends on Redis)
- `/api/v1/positions` - Should return positions (depends on DB)
- `/api/v1/strategies` - Should return strategies (depends on DB)

### No Schema Changes
- Database schemas unchanged
- Redis key structures unchanged
- API request/response models unchanged

## 4. Acceptance Criteria

### Critical Path (Must Pass)
1. ✅ **Redis container starts and stays running**
   - `docker compose ps` shows `omni-bot-redis` as `Up (healthy)`
   - `docker exec omni-bot-redis redis-cli ping` returns `PONG`
   - API logs show successful Redis connections (no "Temporary failure in name resolution")

2. ✅ **Postgres container starts and stays running**
   - `docker compose ps` shows `omni-bot-postgres` as `Up (healthy)`
   - `docker exec omni-bot-postgres pg_isready -U omni_bot` returns `accepting connections`
   - API logs show successful database connections

3. ✅ **All services healthy**
   - `GET /api/v1/health/detailed` returns `"status": "healthy"`
   - All components show `"status": "connected"` or `"status": "running"`
   - Frontend System Health panel shows all green indicators

4. ✅ **UI endpoints return data (no HTTP 500)**
   - `GET /api/v1/positions` returns 200 OK with positions array
   - `GET /api/v1/screener` returns 200 OK with screener results
   - `GET /api/v1/strategies` returns 200 OK with strategies array
   - Frontend shows data instead of "HTTP 500: Internal Server Error"

5. ✅ **Containers auto-restart on failure**
   - After `docker compose restart redis`, container restarts automatically
   - After `docker compose restart postgres`, container restarts automatically
   - Containers restart after server reboot (test: `docker compose down && docker compose up -d`)

### Verification (Should Pass)
6. ✅ **Previous fixes still work**
   - Stablecoin filtering: No USDC/USD or USDT/USD in screener results
   - Stop-loss precision: Stop-loss orders place successfully with 3-decimal prices
   - 2% rule: Trade sizing still respects 2% risk limit

7. ✅ **Data persistence**
   - Redis data persists after container restart (check `ingestor:active_symbols`)
   - Database data persists after container restart (check strategies table)
   - Positions tracked correctly after restart

## 5. Dependencies

### Prerequisites
- None - This is a recovery task, no other work blocks it

### Blocks
- **All other work** - System must be healthy before any new features or fixes can be tested
- **Trading operations** - Cannot execute trades without Redis and Database
- **Signal generation** - Screener cannot function without Redis

## Agent Launch Instructions

### Ticket 1: Immediate Service Recovery
**Agent:** `fix-docker-restart-policies`  
**Ticket:** `TICKET-101: Fix Redis and Postgres container restart policies`  
**Branch:** `fix/docker-restart-policies`

**Prompt:**
```
Fix the docker-compose.yml to ensure Redis and Postgres containers automatically restart after server reboots or container failures.

Current issue:
- Redis and Postgres containers exited after server restart and are not restarting
- docker-compose.yml does not have `restart` policies for these services
- Other services (api, ingestor, runner, frontend) have `restart: unless-stopped` but Redis and Postgres do not

Requirements:
1. Add `restart: unless-stopped` to both `redis` and `postgres` services in docker-compose.yml
2. Verify the syntax is correct (YAML indentation matches other services)
3. Test that containers restart after `docker compose restart redis` and `docker compose restart postgres`
4. Ensure health checks still work correctly with restart policies

Files to modify:
- infra/docker-compose.yml (add restart policies to redis and postgres services)

Do not modify:
- Service definitions (ports, volumes, environment variables)
- Health check configurations
- Network configurations
- Other services (they already have restart policies)

After making changes, verify:
- `docker compose config` shows valid YAML
- Containers can be restarted successfully
- Health checks still pass after restart
```

---

### Ticket 2: Start Services and Verify Health
**Agent:** `start-services-verify-health`  
**Ticket:** `TICKET-102: Start Redis and Postgres, verify all services healthy`  
**Branch:** `fix/start-services-verify-health`

**Prompt:**
```
Start the Redis and Postgres containers and verify all services return to healthy state.

Current state:
- Redis container: Exited (255)
- Postgres container: Exited (0)
- API container: Up but unhealthy (cannot connect to Redis/Postgres)
- Ingestor and Runner: Up and healthy (but cannot function without Redis)

Steps:
1. Start Redis and Postgres containers: `docker compose up -d redis postgres`
2. Wait for health checks to pass (check with `docker compose ps`)
3. Restart API service to reconnect: `docker compose restart api`
4. Verify all services are healthy:
   - `docker compose ps` shows all services as `Up (healthy)`
   - `curl http://localhost:8001/api/v1/health/detailed` returns `"status": "healthy"`
   - Check API logs: `docker compose logs api --tail 50` shows successful Redis and DB connections
5. Verify UI endpoints work:
   - `curl http://localhost:8001/api/v1/positions` returns 200 OK
   - `curl http://localhost:8001/api/v1/screener` returns 200 OK
   - `curl http://localhost:8001/api/v1/strategies` returns 200 OK

If services fail to start:
- Check logs: `docker compose logs redis` and `docker compose logs postgres`
- Verify volumes are accessible: `docker volume ls | grep omni-bot`
- Check for port conflicts: `netstat -tuln | grep -E '5433|6380'`

Expected outcome:
- All containers running and healthy
- API endpoints return data (no HTTP 500)
- Frontend shows healthy system status
```

---

### Ticket 3: Verify Previous Fixes Still Work
**Agent:** `verify-previous-fixes`  
**Ticket:** `TICKET-103: Verify stablecoin filtering and stop-loss precision after recovery`  
**Branch:** `verify/previous-fixes-still-work`

**Prompt:**
```
Verify that previous fixes (stablecoin filtering and stop-loss precision) still function correctly after system recovery.

Previous fixes to verify:
1. Stablecoin filtering: USDC/USD, USDT/USD, DAI/USD should not appear in screener results
2. Stop-loss precision: Stop-loss orders should round prices to 3 decimal places

Verification steps:

1. Check stablecoin filtering:
   - Query Redis: `docker exec omni-bot-redis redis-cli GET 'ingestor:active_symbols' | python3 -c 'import sys, json; symbols=json.loads(sys.stdin.read()) if sys.stdin.read() else []; stablecoins=[s for s in symbols if "USDC" in s or "USDT" in s or "DAI" in s]; print("Stablecoins found:", stablecoins if stablecoins else "None")'`
   - Check screener results: `curl http://localhost:8001/api/v1/screener | python3 -m json.tool | grep -E 'USDC|USDT|DAI'` should return no matches
   - Check ingestor logs: `docker compose logs ingestor --tail 100 | grep -i 'stablecoin\|USDC\|USDT'` should show filtering messages

2. Check stop-loss precision:
   - Review code: `grep -A 5 'stop_loss_price_rounded' backend/execution/executor.py` should show rounding to 3 decimals
   - Check if fix is deployed: `docker exec omni-bot-api grep -A 3 'stop_loss_price_rounded' /app/backend/execution/executor.py` should show the fix
   - Monitor next buy order: When a buy order executes, check logs for `"rounded from"` message showing 3-decimal precision

3. Check 2% rule:
   - Verify epsilon fix: `docker exec omni-bot-api grep -A 3 'epsilon' /app/backend/risk/two_percent.py` should show epsilon tolerance

Expected results:
- No stablecoins in active symbols or screener results
- Stop-loss prices rounded to 3 decimals in code
- 2% rule has epsilon tolerance for floating-point precision

If any fix is missing:
- Report which fix is missing and where it should be
- Do not re-implement fixes (they should already be deployed)
```

---

### Ticket 4: Add Container Health Monitoring
**Agent:** `add-container-health-monitoring`  
**Ticket:** `TICKET-104: Add monitoring for container health and auto-restart verification`  
**Branch:** `feature/container-health-monitoring`

**Prompt:**
```
Add monitoring and logging to detect when containers exit unexpectedly and verify restart policies are working.

Requirements:
1. Add a health check script or endpoint that verifies all required containers are running
2. Log warnings when containers are detected as stopped
3. Add a startup check in the API that verifies Redis and Postgres are accessible before starting the screener service
4. Consider adding a simple cron job or systemd timer (outside Docker) to check container health

Implementation approach:
- Add a startup check in `backend/api/main.py` that pings Redis and Postgres before starting services
- If services are unavailable, log errors and retry with exponential backoff
- Add a `/api/v1/health/containers` endpoint that checks Docker container status (optional, requires Docker socket access)

Files to modify:
- `backend/api/main.py` - Add startup health checks
- `backend/api/routes/health.py` - Add container health endpoint (optional)

Do not:
- Modify docker-compose.yml (already handled in TICKET-101)
- Change service startup order (handled by depends_on)
- Add external monitoring tools (keep it simple)

Acceptance criteria:
- API logs show clear errors if Redis/Postgres unavailable at startup
- API retries connection attempts (already implemented, verify it works)
- Health endpoint shows container status if accessible
```

---

## Execution Order

1. **TICKET-101** (Fix restart policies) - **CRITICAL PATH** - Must be done first
2. **TICKET-102** (Start services) - **CRITICAL PATH** - Immediate recovery
3. **TICKET-103** (Verify fixes) - **VERIFICATION** - Can run in parallel with TICKET-104
4. **TICKET-104** (Add monitoring) - **ENHANCEMENT** - Can be done after recovery

## Notes

- **Priority**: TICKET-101 and TICKET-102 are blocking all other work
- **Testing**: After TICKET-102, manually verify the frontend shows healthy status
- **Rollback**: If issues occur, `docker compose down` and `docker compose up -d` should restore previous state
- **Documentation**: Update deployment docs to mention restart policies if they don't already exist
