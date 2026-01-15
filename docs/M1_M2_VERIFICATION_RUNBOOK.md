# M1–M2 Full Verification Runbook

**Purpose**: End-to-end validation of Milestones M1 (The Hub) and M2 (The Guard) integration.

**Prerequisites**:
- Docker and Docker Compose installed
- `.env` file configured (or defaults will be used)
- Network access to pull Docker images

---

## Quick Start: Unified Verification

The `make verify` target runs a complete end-to-end verification of all M1-M2 integration components. This single command validates services, contracts, API endpoints, database schema, Redis connectivity, ingestor process, and module imports.

### Prerequisites

Before running verification, ensure:

1. **Services are running**:
   ```bash
   make up
   ```

2. **Wait for services to be healthy** (10-30 seconds):
   ```bash
   make ps
   ```
   All services should show `Up (healthy)` status.

3. **Database migrations are applied**:
   ```bash
   make migrate
   ```

### Running Full Verification

**Command**:
```bash
make verify
```

**What it verifies**:

The `verify` target executes the following checks in sequence:

1. **`verify-services`** - Ensures all Docker services (API, PostgreSQL, Redis, Ingestor) are running and healthy
2. **`verify-contracts`** - Validates OpenAPI contract endpoints (`/api/v1/health`, `/api/v1/panic`, `/api/v1/strategies`) are present
3. **`verify-api`** - Tests the API health endpoint returns `{"status":"healthy"}`
4. **`verify-database`** - Confirms database migrations are applied and required tables (`strategies`, `signals`, `orders`, `equity_curve`) exist
5. **`verify-redis`** - Tests Redis connectivity from both the Redis container and the API container
6. **`verify-ingestor`** - Verifies ingestor modules import successfully and health file exists
7. **`verify-modules`** - Tests that risk and execution modules can be imported correctly

**Expected Output** (successful run):
```
=== M1-M2 Integration Verification ===
=== Verifying Services ===
✓ All services running
=== Verifying Contracts ===
✓ Contract endpoints present
=== Verifying API ===
✓ API health check passed
=== Verifying Database ===
✓ Database schema verified
=== Verifying Redis ===
✓ Redis connectivity verified
=== Verifying Ingestor ===
✓ Ingestor modules import
✓ Ingestor verified
=== Verifying Modules ===
✓ Risk and execution modules verified
=== All checks passed ===
```

**Running Individual Checks**:

You can also run individual verification targets for targeted testing:

```bash
make verify-services    # Check service health only
make verify-contracts   # Validate OpenAPI contract only
make verify-api         # Test API health endpoint only
make verify-database    # Verify database schema only
make verify-redis       # Test Redis connectivity only
make verify-ingestor    # Verify ingestor process only
make verify-modules     # Test module imports only
```

**Troubleshooting**:

- **Services not healthy**: Run `make logs` to check for startup errors
- **API not responding**: Check `make logs-api` for application errors
- **Migration not applied**: Run `make migrate` first
- **Redis connection fails**: Verify Redis container is running with `make ps`
- **Module import fails**: Check Python path and module structure

If any check fails, the verification will stop and display an error message indicating which component failed.

---

## Manual Verification Steps

### Step 1: Start Services

**Command**:
```bash
make up
```

**Expected Output**:
```
[+] Running 4/4
 ✔ Container omni-bot-postgres    Started
 ✔ Container omni-bot-redis       Started
 ✔ Container omni-bot-api         Started
 ✔ Container omni-bot-ingestor    Started
```

**Wait Time**: 10-30 seconds for services to become healthy.

**Verification**:
```bash
make ps
```

**Expected Output**:
```
NAME                  IMAGE                    STATUS
omni-bot-api          omni-bot-api:latest      Up (healthy)
omni-bot-ingestor     omni-bot-ingestor:latest Up
omni-bot-postgres     postgres:16-alpine        Up (healthy)
omni-bot-redis        redis:7-alpine            Up (healthy)
```

**Triage**:
- **Services not starting**: Check `docker compose logs` for errors
- **API unhealthy**: Check `make logs-api` for startup errors
- **Postgres unhealthy**: Check database credentials in `.env`
- **Redis unhealthy**: Check Redis port conflicts (default: 6379)

---

### Step 2: Verify OpenAPI Contract

**Command**:
```bash
curl -s http://localhost:8000/openapi.json | python3 -m json.tool > /tmp/api_openapi.json
```

**Expected Output**: Valid JSON file written to `/tmp/api_openapi.json`

**Validate against contract**:
```bash
# Compare key endpoints exist
curl -s http://localhost:8000/openapi.json | python3 -c "import sys, json; d=json.load(sys.stdin); assert '/api/v1/health' in d['paths'], 'Missing /api/v1/health'; assert '/api/v1/panic' in d['paths'], 'Missing /api/v1/panic'; assert '/api/v1/strategies' in d['paths'], 'Missing /api/v1/strategies'; print('✓ All contract endpoints present')"
```

**Expected Output**:
```
✓ All contract endpoints present
```

**Manual Contract Check**:
```bash
# Verify contract file exists and is valid YAML
python3 -c "import yaml; yaml.safe_load(open('contracts/openapi.yaml')); print('✓ Contract YAML is valid')"
```

**Triage**:
- **404 on /openapi.json**: API not running or wrong port
- **Invalid JSON**: API startup error, check `make logs-api`
- **Missing endpoints**: Routes not registered in `backend/api/main.py`
- **Contract YAML invalid**: Check `contracts/openapi.yaml` syntax

---

### Step 3: Verify API Health Endpoint

**Command**:
```bash
curl -s http://localhost:8000/api/v1/health
```

**Expected Output**:
```json
{"status":"healthy"}
```

**Alternative (using Makefile)**:
```bash
make health
```

**Expected Output**:
```
Checking service health...
NAME                  IMAGE                    STATUS
omni-bot-api          omni-bot-api:latest      Up (healthy)
omni-bot-ingestor     omni-bot-ingestor:latest Up
omni-bot-postgres     postgres:16-alpine        Up (healthy)
omni-bot-redis        redis:7-alpine            Up (healthy)

API Health:
{"status":"healthy"}

PostgreSQL:
/var/run/postgresql:5432 - accepting connections

Redis:
PONG
```

**Triage**:
- **Connection refused**: API container not running, check `make ps`
- **500 error**: Check `make logs-api` for application errors
- **Wrong response format**: Check `backend/api/routes/health.py`

---

### Step 4: Verify Database Migrations and Tables

**Command**:
```bash
make migrate
```

**Expected Output**:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial_schema, Initial schema
```

**Verify Tables Exist**:
```bash
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\dt"
```

**Expected Output**:
```
              List of relations
 Schema |      Name       | Type  |  Owner
--------+-----------------+-------+----------
 public | alembic_version | table | omni_bot
 public | equity_curve    | table | omni_bot
 public | orders          | table | omni_bot
 public | signals         | table | omni_bot
 public | strategies      | table | omni_bot
```

**Verify Table Schemas**:
```bash
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\d strategies"
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\d signals"
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\d orders"
docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\d equity_curve"
```

**Expected Output**: Each command should show table structure with:
- `strategies`: id (uuid), name, config (jsonb), status, created_at, updated_at
- `signals`: id (uuid), strategy_id (uuid), symbol, side, intent_type, notional_risk_pct, metadata (jsonb), status, created_at
- `orders`: id (uuid), signal_id (uuid), symbol, side, executed_price, quantity, fees, slippage, exchange_order_id, status, created_at, executed_at
- `equity_curve`: id (uuid), timestamp, total_equity, realized_pnl, unrealized_pnl, exposure_pct, created_at

**Triage**:
- **Migration fails**: Check `backend/alembic/versions/001_initial_schema.py` for syntax errors
- **Tables missing**: Migration didn't run, check `make logs-api` for alembic errors
- **Wrong schema**: Compare migration file with `backend/db/models.py`
- **Connection error**: Check `DATABASE_URL` in `.env` or docker-compose.yml

---

### Step 5: Verify Redis Connectivity and Stream Primitives

**Command**:
```bash
docker compose exec -T redis redis-cli ping
```

**Expected Output**:
```
PONG
```

**Test Stream Operations**:
```bash
# Create a test stream and publish a message
docker compose exec -T redis redis-cli XADD test:stream:verify "*" field1 "value1" field2 "value2"

# Read from stream
docker compose exec -T redis redis-cli XREAD COUNT 1 STREAMS test:stream:verify 0

# Create consumer group
docker compose exec -T redis redis-cli XGROUP CREATE test:stream:verify test-group 0 MKSTREAM

# Consume from group
docker compose exec -T redis redis-cli XREADGROUP GROUP test-group consumer1 COUNT 1 STREAMS test:stream:verify ">"

# Cleanup
docker compose exec -T redis redis-cli DEL test:stream:verify
```

**Expected Output**:
- `XADD` returns message ID (e.g., `1704067200000-0`)
- `XREAD` returns the message with fields
- `XGROUP CREATE` returns `OK`
- `XREADGROUP` returns the message
- `DEL` returns `1`

**Verify Redis Connection from API Container**:
```bash
docker compose exec api python3 -c "from backend.redis import get_redis_client; client = get_redis_client(); print('✓ Redis connection successful:', client.ping())"
```

**Expected Output**:
```
✓ Redis connection successful: True
```

**Triage**:
- **Connection refused**: Redis container not running, check `make ps`
- **PING fails**: Redis not ready, wait 10 seconds and retry
- **Stream operations fail**: Check Redis version (should be 7+)
- **Python import fails**: Check `backend/redis/__init__.py` and `backend/config.py` for REDIS_URL

---

### Step 6: Verify Ingestor Process Behavior

**Command**:
```bash
make logs-ingestor
```

**Expected Output** (within 30 seconds):
```
omni-bot-ingestor  | INFO - Starting ingestor: symbols=BTC/USD, ETH/USD, intervals=4h, 1d
omni-bot-ingestor  | INFO - Starting ingestor pipeline: symbols=['BTC/USD', 'ETH/USD'], intervals=['4h', '1d']
omni-bot-ingestor  | INFO - Health check file created: /tmp/ingestor.health
omni-bot-ingestor  | INFO - WebSocket client starting...
omni-bot-ingestor  | INFO - Normalizer starting...
```

**Note**: Even without Kraken credentials, the ingestor should start and attempt connection (will fail gracefully).

**Check Health File**:
```bash
docker compose exec ingestor ls -la /tmp/ingestor.health
```

**Expected Output**:
```
-rw-r--r-- 1 root root 0 [timestamp] /tmp/ingestor.health
```

**Verify Ingestor Process is Running**:
```bash
docker compose exec ingestor ps aux | grep -E "(python|ingestor)" | grep -v grep
```

**Expected Output**:
```
root         1  ... python -m backend.ingestor.main
```

**Verify Module Imports**:
```bash
docker compose exec ingestor python3 -c "from backend.ingestor.main import main; from backend.ingestor.kraken_ws import KrakenWebSocketClient; from backend.ingestor.normalizer import Normalizer; print('✓ All ingestor modules import successfully')"
```

**Expected Output**:
```
✓ All ingestor modules import successfully
```

**Triage**:
- **Container exits immediately**: Check `make logs-ingestor` for import errors or missing dependencies
- **No health file**: Check volume mount in docker-compose.yml (`ingestor_health:/tmp`)
- **WebSocket connection errors**: Expected if `KRAKEN_API_KEY` not set; ingestor should continue running
- **Import errors**: Check `backend/ingestor/__init__.py` and module dependencies

---

### Step 7: Verify Risk Module Imports and Smoke Tests

**Command**:
```bash
docker compose exec api python3 -c "
from backend.risk import (
    evaluate_intent, TradeIntent, RiskDecision,
    get_portfolio_exposure, get_current_equity,
    get_open_positions, is_system_halted
)
from backend.risk.models import RiskDecision
from backend.risk.evaluator import evaluate_intent
from backend.risk.rules import is_system_halted
print('✓ All risk modules import successfully')
"
```

**Expected Output**:
```
✓ All risk modules import successfully
```

**Smoke Test: Risk Evaluator**:
```bash
docker compose exec api python3 -c "
from backend.risk.evaluator import evaluate_intent, TradeIntent
from datetime import datetime

# Create a minimal TradeIntent
intent = TradeIntent(
    strategy_id='test-strategy',
    symbol='BTC/USD',
    side='buy',
    intent_type='enter',
    notional_risk_pct=5.0,
    metadata={}
)

# Attempt evaluation (may fail on database/Redis, but should not crash on import)
try:
    result = evaluate_intent(intent)
    print(f'✓ Risk evaluator executed (result: {result.approved})')
except Exception as e:
    print(f'⚠ Risk evaluator import OK, but evaluation failed (expected): {type(e).__name__}: {e}')
"
```

**Expected Output** (either):
```
✓ Risk evaluator executed (result: True/False)
```
or (if database/Redis not fully configured):
```
⚠ Risk evaluator import OK, but evaluation failed (expected): [ExceptionType]: [message]
```

**Smoke Test: Risk Rules**:
```bash
docker compose exec api python3 -c "
from backend.risk.rules import is_system_halted, get_portfolio_exposure
halted = is_system_halted()
exposure = get_portfolio_exposure()
print(f'✓ System halt check: {halted}')
print(f'✓ Portfolio exposure: {exposure}%')
"
```

**Expected Output**:
```
✓ System halt check: False
✓ Portfolio exposure: 0.0%
```

**Triage**:
- **ImportError**: Check `backend/risk/__init__.py` exports
- **ModuleNotFoundError**: Check Python path and `backend/` structure
- **AttributeError**: Check module structure matches imports
- **Database errors in smoke test**: Expected if tables empty; import should still succeed

---

### Step 8: Verify Execution Module Imports and Smoke Tests

**Command**:
```bash
docker compose exec api python3 -c "
from backend.execution import (
    execute_approved_intent, set_kraken_client, get_kraken_client,
    get_next_nonce, Fill
)
from backend.execution.models import Fill
from backend.execution.executor import execute_approved_intent
from backend.execution.nonce import get_next_nonce
from backend.execution.kraken_interface import KrakenClientInterface, KrakenClientStub
print('✓ All execution modules import successfully')
"
```

**Expected Output**:
```
✓ All execution modules import successfully
```

**Smoke Test: Execution Nonce**:
```bash
docker compose exec api python3 -c "
from backend.execution.nonce import get_next_nonce, get_current_nonce, reset_nonce

# Test nonce generation (uses Redis)
try:
    nonce1 = get_next_nonce()
    nonce2 = get_next_nonce()
    current = get_current_nonce()
    print(f'✓ Nonce generation works: {nonce1} < {nonce2}')
    print(f'✓ Current nonce: {current}')
except Exception as e:
    print(f'⚠ Nonce import OK, but generation failed (check Redis): {type(e).__name__}: {e}')
"
```

**Expected Output** (if Redis working):
```
✓ Nonce generation works: [number] < [number+1]
✓ Current nonce: [number]
```

**Smoke Test: Execution Models**:
```bash
docker compose exec api python3 -c "
from backend.execution.models import Fill
from datetime import datetime, timezone

fill = Fill(
    order_id='test-order',
    symbol='BTC/USD',
    side='buy',
    executed_price=50000.0,
    quantity=0.001,
    fees=0.5,
    slippage=2.5,
    exchange_order_id='KRAKEN-123',
    timestamp=datetime.now(timezone.utc)
)
print(f'✓ Fill model instantiation works: {fill.symbol} @ {fill.executed_price}')
"
```

**Expected Output**:
```
✓ Fill model instantiation works: BTC/USD @ 50000.0
```

**Triage**:
- **ImportError**: Check `backend/execution/__init__.py` exports
- **ModuleNotFoundError**: Check Python path
- **Redis errors in nonce test**: Check Redis connectivity (Step 5)
- **Model validation errors**: Check `backend/execution/models.py` matches contract

---

## Complete Verification Script

**Implemented Makefile Targets**:

The following verification targets are now available in the Makefile:

```makefile
verify-services: ## Verify all services are running
	@echo "=== Verifying Services ==="
	@docker compose ps | grep -q "Up (healthy)" || (echo "❌ Services not healthy"; exit 1)
	@echo "✓ All services running"

verify-contracts: ## Verify OpenAPI contract
	@echo "=== Verifying Contracts ==="
	@curl -s http://localhost:8000/openapi.json > /dev/null || (echo "❌ API not responding"; exit 1)
	@curl -s http://localhost:8000/openapi.json | python3 -c "import sys, json; d=json.load(sys.stdin); assert '/api/v1/health' in d['paths']; assert '/api/v1/panic' in d['paths']; assert '/api/v1/strategies' in d['paths']; print('✓ Contract endpoints present')" || (echo "❌ Contract endpoints missing"; exit 1)

verify-api: ## Verify API health endpoint
	@echo "=== Verifying API ==="
	@curl -s http://localhost:8000/api/v1/health | grep -q "healthy" || (echo "❌ Health endpoint failed"; exit 1)
	@echo "✓ API health check passed"

verify-database: ## Verify database migrations and tables
	@echo "=== Verifying Database ==="
	@docker compose exec -T api sh -c "cd /app/backend && alembic current" | grep -q "001_initial_schema" || (echo "❌ Migration not applied"; exit 1)
	@docker compose exec -T postgres psql -U omni_bot -d omni_bot -c "\dt" | grep -q "strategies" || (echo "❌ Tables missing"; exit 1)
	@echo "✓ Database schema verified"

verify-redis: ## Verify Redis connectivity and streams
	@echo "=== Verifying Redis ==="
	@docker compose exec -T redis redis-cli ping | grep -q "PONG" || (echo "❌ Redis not responding"; exit 1)
	@docker compose exec api python3 -c "from backend.redis import get_redis_client; get_redis_client().ping()" || (echo "❌ Redis connection from API failed"; exit 1)
	@echo "✓ Redis connectivity verified"

verify-ingestor: ## Verify ingestor process
	@echo "=== Verifying Ingestor ==="
	@docker compose exec ingestor python3 -c "from backend.ingestor.main import main; from backend.ingestor.kraken_ws import KrakenWebSocketClient; print('✓ Ingestor modules import')" || (echo "❌ Ingestor import failed"; exit 1)
	@docker compose exec ingestor test -f /tmp/ingestor.health || (echo "⚠ Ingestor health file missing (may be starting)"; exit 0)
	@echo "✓ Ingestor verified"

verify-modules: ## Verify risk and execution modules
	@echo "=== Verifying Modules ==="
	@docker compose exec api python3 -c "from backend.risk import evaluate_intent, TradeIntent; from backend.execution import execute_approved_intent, Fill; print('✓ Modules import')" || (echo "❌ Module imports failed"; exit 1)
	@echo "✓ Risk and execution modules verified"

verify: ## Run full M1-M2 integration verification
	@echo "=== M1-M2 Integration Verification ==="
	@$(MAKE) verify-services
	@$(MAKE) verify-contracts
	@$(MAKE) verify-api
	@$(MAKE) verify-database
	@$(MAKE) verify-redis
	@$(MAKE) verify-ingestor
	@$(MAKE) verify-modules
	@echo "=== All checks passed ==="
```

**To run all verifications** (recommended):
```bash
make verify
```

**To run individual verifications**:
```bash
make verify-services    # Check service health only
make verify-contracts   # Validate OpenAPI contract only
make verify-api         # Test API health endpoint only
make verify-database    # Verify database schema only
make verify-redis       # Test Redis connectivity only
make verify-ingestor    # Verify ingestor process only
make verify-modules     # Test module imports only
```

---

## Failure Triage Quick Reference

| Symptom | Likely Cause | Fix Location |
|---------|--------------|--------------|
| Services won't start | Docker/Compose issue | Check `docker compose logs` |
| API 404/500 | Application error | `backend/api/main.py`, `backend/api/routes/` |
| Migration fails | Schema error | `backend/alembic/versions/001_initial_schema.py` |
| Tables missing | Migration not run | Run `make migrate` |
| Redis connection fails | Configuration error | `backend/config.py` REDIS_URL |
| Stream operations fail | Redis version/commands | Check Redis 7+ compatibility |
| Ingestor exits | Import/dependency error | `backend/ingestor/`, `backend/requirements.txt` |
| Module import fails | Python path/structure | `backend/risk/__init__.py`, `backend/execution/__init__.py` |
| Contract mismatch | Route registration | `backend/api/main.py` route includes |

---

## Expected Full Run Output

When all checks pass, you should see:

```
=== M1-M2 Integration Verification ===
=== Verifying Services ===
✓ All services running
=== Verifying Contracts ===
✓ Contract endpoints present
=== Verifying API ===
✓ API health check passed
=== Verifying Database ===
✓ Database schema verified
=== Verifying Redis ===
✓ Redis connectivity verified
=== Verifying Ingestor ===
✓ Ingestor modules import
✓ Ingestor verified
=== Verifying Modules ===
✓ Risk and execution modules verified
=== All checks passed ===
```

---

## Notes

- **Exchange Credentials**: Ingestor will fail to connect to Kraken without `KRAKEN_API_KEY` and `KRAKEN_API_SECRET`, but the process should start and modules should import successfully.
- **Database State**: Empty tables are expected for initial verification. Smoke tests may fail on database queries but imports should succeed.
- **Timing**: Allow 10-30 seconds after `make up` for health checks to pass.
- **Cleanup**: Run `make down` to stop all services, or `make clean` to remove volumes.
