# Execution Plan: Milestones M1 & M2
## The Hub (M1) and The Guard (M2)

**Authoritative Source:** `docs/MSSD.md`  
**Status:** Planning  
**Target Milestones:** M1 (The Hub), M2 (The Guard)

---

## Overview

This plan breaks down M1 and M2 into 15 parallelizable tickets that can be executed by specialized agents. All tickets assume contracts are established first (Tickets 1-2) before implementation begins.

**Dependency Graph:**
- Tickets 1-2: Foundation (Contracts + DB Schema) - **BLOCKING**
- Tickets 3-7: M1 Core (Redis + Data Ingestor + API Foundation) - Can parallelize after Ticket 2
- Tickets 8-15: M2 Core (Risk Manager + Execution Engine + API Completion) - Can parallelize after Tickets 1-2, 6

---

## Ticket 1: Finalize v1 Contract Schemas

**Priority:** P0 (BLOCKING)  
**Owner:** Contracts  
**Scope:** `contracts/`  
**Dependencies:** None

### Files to Create/Modify
- `contracts/types.md` - Finalize TradeIntent, RiskDecision, Fill field definitions with types
- `contracts/openapi.yaml` - Add complete schemas for TradeIntent, RiskDecision, Fill
- `contracts/events.md` - Finalize MarketDataEvent, TradeIntentEvent, RiskDecisionEvent, OrderExecutedEvent schemas

### Acceptance Criteria
- [ ] All contract types have explicit field types (string, number, boolean, timestamp format)
- [ ] TradeIntent schema includes: strategy_id (string), symbol (string), side (enum: "buy"|"sell"), intent_type (enum: "enter"|"exit"|"reduce"), notional_risk_pct (number), metadata (object)
- [ ] RiskDecision schema includes: intent_id (string), approved (boolean), rejection_reason (string|null), evaluated_portfolio_risk (number), timestamp (ISO8601)
- [ ] Fill schema includes: order_id (string), symbol (string), side (string), executed_price (number), quantity (number), fees (number), slippage (number), exchange_order_id (string), timestamp (ISO8601)
- [ ] MarketDataEvent schema includes: symbol, interval, open, high, low, close, volume, timestamp (all typed)
- [ ] OpenAPI schemas are valid YAML and parseable by OpenAPI tools
- [ ] All schemas include field descriptions

### Verification Steps
```bash
# Validate OpenAPI schema
docker run --rm -v $(pwd)/contracts:/spec openapitools/openapi-validator openapi.yaml

# Verify types.md is readable and complete
grep -E "(TradeIntent|RiskDecision|Fill)" contracts/types.md | wc -l
# Expected: At least 3 matches (one per type)

# Check all required fields are present
grep -A 10 "TradeIntent" contracts/types.md | grep -E "(strategy_id|symbol|side|intent_type|notional_risk_pct)"
# Expected: All 5 fields found
```

---

## Ticket 2: PostgreSQL Schema and Migration Infrastructure

**Priority:** P0 (BLOCKING)  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 1 (for type alignment)

### Files to Create/Modify
- `backend/db/schema.sql` - Initial schema with tables: strategies, signals, orders, equity_curve
- `backend/db/migrations/001_initial_schema.sql` - Alembic-compatible migration
- `backend/db/__init__.py` - Database connection utilities
- `backend/db/models.py` - SQLAlchemy ORM models (optional, can be deferred)
- `backend/requirements.txt` - Add psycopg2-binary, alembic, sqlalchemy

### Acceptance Criteria
- [ ] `strategies` table: id (PK), name (unique), config (JSONB), status (enum), created_at, updated_at
- [ ] `signals` table: id (PK), strategy_id (FK), symbol, side, intent_type, notional_risk_pct, metadata (JSONB), status (enum: pending|approved|rejected), created_at
- [ ] `orders` table: id (PK), signal_id (FK), symbol, side, executed_price, quantity, fees, slippage, exchange_order_id (unique), status, created_at, executed_at
- [ ] `equity_curve` table: id (PK), timestamp (indexed), total_equity, realized_pnl, unrealized_pnl, exposure_pct
- [ ] Foreign key constraints defined
- [ ] Indexes on: signals(strategy_id, created_at), orders(signal_id, executed_at), equity_curve(timestamp)
- [ ] Migration can be applied and rolled back cleanly

### Verification Steps
```bash
cd backend
# Apply migration
alembic upgrade head
# Expected: Migration applies without errors

# Verify tables exist
psql $DATABASE_URL -c "\dt"
# Expected: strategies, signals, orders, equity_curve tables listed

# Verify schema matches requirements
psql $DATABASE_URL -c "\d strategies"
psql $DATABASE_URL -c "\d signals"
# Expected: All required columns present

# Rollback test
alembic downgrade -1
alembic upgrade head
# Expected: Rollback and re-apply succeed
```

---

## Ticket 3: Redis Configuration and Connection Utilities

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** None (can parallelize with Ticket 2)

### Files to Create/Modify
- `backend/redis/__init__.py` - Redis connection pool and utilities
- `backend/redis/streams.py` - Redis Streams helper functions (publish, consume)
- `backend/redis/keys.py` - Key naming constants (e.g., `market:ohlcv:{symbol}:{interval}`)
- `backend/config.py` - Configuration management (Redis URL, connection pool size)
- `backend/requirements.txt` - Add redis, redis-py

### Acceptance Criteria
- [ ] Redis connection pool configured with max_connections=10
- [ ] Stream publish function: `publish_to_stream(stream_key, data)` returns message ID
- [ ] Stream consume function: `consume_stream(stream_key, consumer_group, consumer_name, count=1)` returns messages
- [ ] Key constants defined for: market data streams, portfolio exposure, strategy status, system halt
- [ ] Connection retry logic with exponential backoff (max 3 retries)
- [ ] Configuration loaded from environment variables (REDIS_URL)

### Verification Steps
```bash
cd backend
# Start Redis (if not running)
docker compose up -d redis

# Run connection test
python -c "from backend.redis import get_redis_client; r = get_redis_client(); r.ping()"
# Expected: No errors, connection successful

# Test stream publish
python -c "from backend.redis.streams import publish_to_stream; from backend.redis.keys import MARKET_OHLCV_STREAM; id = publish_to_stream(MARKET_OHLCV_STREAM.format(symbol='BTCUSD', interval='1h'), {'test': 'data'}); print(id)"
# Expected: Message ID returned

# Verify stream exists
redis-cli XINFO STREAM market:ohlcv:BTCUSD:1h
# Expected: Stream info returned with at least 1 message
```

---

## Ticket 4: Data Ingestor - Kraken WebSocket Client

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 3 (Redis utilities)

### Files to Create/Modify
- `backend/ingestor/kraken_ws.py` - Kraken WebSocket client
- `backend/ingestor/__init__.py` - Ingestor module exports
- `backend/ingestor/main.py` - Ingestor service entry point
- `backend/requirements.txt` - Add websockets, aiohttp

### Acceptance Criteria
- [ ] WebSocket client connects to Kraken public WebSocket endpoint
- [ ] Subscribes to ticker/OHLC channels for configured symbols (BTC/USD, ETH/USD)
- [ ] Handles WebSocket reconnection with < 5 second delay (per MSSD constraint)
- [ ] Reconnection logic uses exponential backoff (max 3 attempts, then 5s fixed)
- [ ] Publishes raw ticks to Redis Stream `market:raw:{symbol}` (for Ticket 5 to consume)
- [ ] Logs connection status, errors, and reconnection events
- [ ] Graceful shutdown on SIGTERM/SIGINT

### Verification Steps
```bash
cd backend
# Start Redis
docker compose up -d redis

# Run ingestor (should connect and subscribe)
python -m backend.ingestor.main --symbols BTC/USD ETH/USD
# Expected: Logs show "Connected to Kraken WebSocket" and subscription confirmations

# Verify raw ticks in Redis
redis-cli XREAD COUNT 10 STREAMS market:raw:BTC/USD 0
# Expected: At least one tick message within 10 seconds

# Test reconnection (kill WebSocket connection, verify reconnects)
# Simulate network failure, verify logs show reconnection within 5 seconds
```

---

## Ticket 5: Data Ingestor - OHLCV Normalization

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 3 (Redis), Ticket 4 (raw data source)

### Files to Create/Modify
- `backend/ingestor/normalizer.py` - OHLCV bar aggregation logic
- `backend/ingestor/bar_builder.py` - Time-windowed bar construction (4H, 1D intervals)
- `backend/ingestor/main.py` - Integrate normalizer into ingestor pipeline

### Acceptance Criteria
- [ ] Consumes raw ticks from Redis Stream `market:raw:{symbol}`
- [ ] Aggregates ticks into OHLCV bars for intervals: 4H, 1D
- [ ] Bar structure matches MarketDataEvent schema (symbol, interval, open, high, low, close, volume, timestamp)
- [ ] Timestamps aligned to interval boundaries (4H: 00:00, 04:00, 08:00...; 1D: 00:00 UTC)
- [ ] Publishes normalized bars to Redis Stream `market:ohlcv:{symbol}:{interval}`
- [ ] Handles missing ticks gracefully (no crashes on gaps)
- [ ] Volume aggregation sums tick volumes within bar window

### Verification Steps
```bash
cd backend
# Ensure Ticket 4 is running and producing raw ticks

# Run normalizer (consumes raw, produces OHLCV)
python -m backend.ingestor.normalizer --intervals 4h 1d
# Expected: Logs show bars being created and published

# Verify OHLCV bars in Redis
redis-cli XREAD COUNT 5 STREAMS market:ohlcv:BTC/USD:4h 0
# Expected: Bar messages with open, high, low, close, volume, timestamp fields

# Verify bar structure matches schema
redis-cli XREAD COUNT 1 STREAMS market:ohlcv:BTC/USD:4h 0 | grep -E "(open|high|low|close|volume|timestamp)"
# Expected: All required fields present

# Verify timestamp alignment (check bar timestamps are on 4H boundaries)
# Expected: Timestamps are multiples of 4 hours (e.g., 2024-01-01T00:00:00Z, 2024-01-01T04:00:00Z)
```

---

## Ticket 6: Data Ingestor - Redis Streams Publisher Integration

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 3, Ticket 5

### Files to Create/Modify
- `backend/ingestor/main.py` - Orchestrate WebSocket → Normalizer → Redis Streams pipeline
- `backend/ingestor/config.py` - Ingestor configuration (symbols, intervals)

### Acceptance Criteria
- [ ] End-to-end pipeline: Kraken WS → Raw Ticks → OHLCV Bars → Redis Streams
- [ ] Single process runs both WebSocket client and normalizer (or two coordinated processes)
- [ ] Configuration via environment variables or config file (symbols, intervals)
- [ ] Health check endpoint or signal file indicates ingestor is running
- [ ] CPU usage < 15% on Intel i5-7500T (per MSSD DoD)

### Verification Steps
```bash
cd backend
# Run full ingestor pipeline
python -m backend.ingestor.main

# Monitor CPU usage
top -p $(pgrep -f "ingestor.main")
# Expected: CPU usage < 15%

# Verify end-to-end data flow
redis-cli XREAD COUNT 1 STREAMS market:ohlcv:BTC/USD:4h 0
# Expected: Fresh bars appearing every 4 hours (or on tick aggregation)

# Check logs for errors
# Expected: No connection errors, no normalization errors
```

---

## Ticket 7: API Gateway - FastAPI Foundation and Health Endpoint

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** None (can parallelize)

### Files to Create/Modify
- `backend/api/__init__.py` - API module
- `backend/api/main.py` - FastAPI app initialization
- `backend/api/routes/health.py` - Health check endpoint
- `backend/api/routes/__init__.py` - Route registration
- `backend/requirements.txt` - Add fastapi, uvicorn

### Acceptance Criteria
- [ ] FastAPI app created with title "Omni-Bot API", version "0.1.0"
- [ ] Health endpoint: `GET /api/v1/health` returns `{"status": "healthy"}` with 200 status
- [ ] OpenAPI docs available at `/docs` (FastAPI default)
- [ ] Server runs on port 8000 (configurable via env)
- [ ] CORS configured (if needed for frontend)
- [ ] Structured logging configured

### Verification Steps
```bash
cd backend
# Start API server
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000

# Test health endpoint
curl http://localhost:8000/api/v1/health
# Expected: {"status":"healthy"} with HTTP 200

# Verify OpenAPI docs
curl http://localhost:8000/docs
# Expected: HTML page loads (or JSON at /openapi.json)

# Check server logs
# Expected: Startup logs, no errors
```

---

## Ticket 8: Risk Manager - Core Evaluation Logic

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 1 (TradeIntent schema), Ticket 2 (DB for exposure queries)

### Files to Create/Modify
- `backend/risk/__init__.py` - Risk Manager module
- `backend/risk/evaluator.py` - Core evaluation function `evaluate_intent(trade_intent) -> RiskDecision`
- `backend/risk/rules.py` - Risk rule implementations (portfolio exposure, per-strategy limits)
- `backend/risk/models.py` - RiskDecision model (matches contract schema)

### Acceptance Criteria
- [ ] `evaluate_intent()` function accepts TradeIntent, returns RiskDecision
- [ ] Evaluates against: current portfolio exposure, pending intents exposure, per-strategy risk limit, system halt state
- [ ] Risk calculation: `notional_risk_pct` of TradeIntent compared to total equity
- [ ] Rejection reasons logged: "exceeds_portfolio_limit", "exceeds_strategy_limit", "system_halted", "stale_market_data"
- [ ] Default behavior: fail closed (reject if uncertain)
- [ ] RiskDecision matches contract schema exactly

### Verification Steps
```bash
cd backend
# Unit test risk evaluator
pytest backend/risk/tests/test_evaluator.py -v
# Expected: All tests pass, coverage ≥ 80%

# Test with sample TradeIntent
python -c "
from backend.risk.evaluator import evaluate_intent
from contracts.types import TradeIntent
intent = TradeIntent(strategy_id='test', symbol='BTC/USD', side='buy', intent_type='enter', notional_risk_pct=5.0, metadata={})
decision = evaluate_intent(intent)
print(decision.approved, decision.rejection_reason)
"
# Expected: RiskDecision object returned with approved boolean

# Verify fail-closed behavior (test with invalid/missing data)
# Expected: Returns approved=False with rejection_reason
```

---

## Ticket 9: Risk Manager - Portfolio Exposure Tracking

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 2 (DB schema), Ticket 3 (Redis)

### Files to Create/Modify
- `backend/risk/exposure.py` - Portfolio exposure calculation
- `backend/risk/portfolio.py` - Portfolio state queries (current positions, pending intents)
- `backend/risk/cache.py` - Redis caching for exposure (optional, for performance)

### Acceptance Criteria
- [ ] Calculates total portfolio exposure as sum of: open positions (unrealized PnL), pending approved intents
- [ ] Exposure stored in Redis key `portfolio:exposure:total` (updated on position changes)
- [ ] Queries PostgreSQL for: current equity (from equity_curve latest), open orders (from orders table)
- [ ] Exposure calculation: `(total_exposure / total_equity) * 100` (percentage)
- [ ] Handles edge cases: zero equity, missing data (defaults to 0% exposure)

### Verification Steps
```bash
cd backend
# Test exposure calculation
python -c "
from backend.risk.exposure import get_portfolio_exposure
exposure = get_portfolio_exposure()
print(f'Portfolio exposure: {exposure}%')
"
# Expected: Numeric value (0-100) returned

# Verify Redis cache
redis-cli GET portfolio:exposure:total
# Expected: Numeric value or null (if not cached yet)

# Test with mock data in DB
# Insert test orders, verify exposure updates
# Expected: Exposure reflects current positions
```

---

## Ticket 10: Risk Manager - Halt Mode Management

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 3 (Redis)

### Files to Create/Modify
- `backend/risk/halt.py` - Halt mode state management
- `backend/risk/evaluator.py` - Integrate halt check into evaluation

### Acceptance Criteria
- [ ] Halt state stored in Redis key `system:halt` (boolean, default false)
- [ ] Functions: `set_halt_mode(enabled: bool)`, `is_halted() -> bool`
- [ ] When halted, all TradeIntents are rejected with reason "system_halted"
- [ ] Halt state persists across Risk Manager restarts (Redis-backed)
- [ ] Halt can be cleared via API (Ticket 14)

### Verification Steps
```bash
cd backend
# Test halt mode
python -c "
from backend.risk.halt import set_halt_mode, is_halted
set_halt_mode(True)
print(is_halted())
"
# Expected: True

# Verify Redis state
redis-cli GET system:halt
# Expected: "1" or "true"

# Test rejection when halted
python -c "
from backend.risk.evaluator import evaluate_intent
from contracts.types import TradeIntent
intent = TradeIntent(...)
decision = evaluate_intent(intent)
assert decision.approved == False
assert 'system_halted' in decision.rejection_reason
"
# Expected: All intents rejected when halted
```

---

## Ticket 11: Execution Engine - Kraken REST Client

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 1 (RiskDecision, Fill schemas)

### Files to Create/Modify
- `backend/execution/kraken_rest.py` - Kraken REST API client
- `backend/execution/auth.py` - API key signing (Kraken private endpoint authentication)
- `backend/execution/__init__.py` - Execution module exports
- `backend/config.py` - Add Kraken API credentials (from env vars)

### Acceptance Criteria
- [ ] REST client for Kraken private endpoints (AddOrder, CancelOrder, QueryOrders)
- [ ] API authentication using Kraken signature scheme (API-Key, API-Sign)
- [ ] Rate limiting: respects Kraken rate limits (single egress point constraint)
- [ ] Nonce management: monotonically increasing nonces (prevents collisions)
- [ ] Error handling: retries on transient errors, fails on auth errors
- [ ] Credentials loaded from environment: KRAKEN_API_KEY, KRAKEN_API_SECRET

### Verification Steps
```bash
cd backend
# Test authentication (without placing real order)
python -c "
from backend.execution.kraken_rest import KrakenClient
client = KrakenClient()
balance = client.get_balance()
print('Auth successful' if balance else 'Auth failed')
"
# Expected: Authentication succeeds (or graceful error if keys not configured)

# Test nonce generation
python -c "
from backend.execution.kraken_rest import get_nonce
n1 = get_nonce()
n2 = get_nonce()
assert n2 > n1
print('Nonce monotonic: OK')
"
# Expected: Nonces are strictly increasing

# Verify rate limiting
# Make multiple rapid requests, verify delays
# Expected: Rate limits respected
```

---

## Ticket 12: Execution Engine - Order Management and Nonce Handling

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 11 (Kraken client), Ticket 1 (RiskDecision schema)

### Files to Create/Modify
- `backend/execution/order_manager.py` - Order execution logic
- `backend/execution/nonce.py` - Nonce storage and retrieval (Redis or file-based)
- `backend/execution/executor.py` - Main execution function `execute_approved_intent(risk_decision) -> Fill`

### Acceptance Criteria
- [ ] `execute_approved_intent()` accepts RiskDecision, converts to Kraken order, executes, returns Fill
- [ ] Order conversion: TradeIntent → Kraken order params (symbol, side, type, volume)
- [ ] Nonce stored in Redis key `execution:nonce` (atomic increment)
- [ ] Order execution is serialized (only one order at a time, prevents nonce collisions)
- [ ] Fill object matches contract schema (order_id, symbol, side, executed_price, quantity, fees, slippage, exchange_order_id, timestamp)
- [ ] Handles partial fills and order rejections gracefully

### Verification Steps
```bash
cd backend
# Test nonce atomicity
python -c "
from backend.execution.nonce import get_next_nonce
import concurrent.futures
nonces = []
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(get_next_nonce) for _ in range(10)]
    nonces = [f.result() for f in futures]
assert len(set(nonces)) == 10, 'No duplicate nonces'
assert sorted(nonces) == nonces, 'Nonces are monotonic'
print('Nonce atomicity: OK')
"
# Expected: No duplicate nonces, all unique and monotonic

# Test order execution (dry-run or sandbox)
python -c "
from backend.execution.executor import execute_approved_intent
from contracts.types import RiskDecision, TradeIntent
# Create mock RiskDecision with approved intent
# Execute (may require sandbox/testnet)
# Expected: Fill object returned or graceful error
"
```

---

## Ticket 13: Execution Engine - Persistence Layer

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 2 (DB schema), Ticket 12 (Fill objects)

### Files to Create/Modify
- `backend/execution/persistence.py` - Database write functions
- `backend/execution/executor.py` - Integrate persistence into execution flow

### Acceptance Criteria
- [ ] Persists Fill objects to `orders` table (all fields from Fill schema)
- [ ] Updates `signals` table: sets status to "executed" when order fills
- [ ] Transaction safety: order and signal updates in same transaction
- [ ] Error handling: if persistence fails, log error but don't fail execution (eventual consistency)
- [ ] Idempotency: duplicate order_id writes are ignored (upsert logic)

### Verification Steps
```bash
cd backend
# Test persistence
python -c "
from backend.execution.persistence import persist_fill
from contracts.types import Fill
fill = Fill(order_id='test-123', symbol='BTC/USD', side='buy', executed_price=50000, quantity=0.001, fees=0.5, slippage=0.0, exchange_order_id='kraken-456', timestamp='2024-01-01T00:00:00Z')
persist_fill(fill)
"
# Expected: No errors, data written to DB

# Verify in database
psql $DATABASE_URL -c "SELECT * FROM orders WHERE order_id = 'test-123';"
# Expected: Row exists with all fields

# Test idempotency (insert same fill twice)
# Expected: Second insert is ignored or updates existing row
```

---

## Ticket 14: API Gateway - Panic Endpoint

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 7 (API foundation), Ticket 10 (Halt mode), Ticket 12 (Order cancellation)

### Files to Create/Modify
- `backend/api/routes/panic.py` - Panic endpoint implementation
- `backend/api/routes/__init__.py` - Register panic route
- `backend/execution/panic.py` - Panic sequence logic (cancel orders, flatten positions)

### Acceptance Criteria
- [ ] Endpoint: `POST /api/v1/panic` (matches OpenAPI contract)
- [ ] Sets system halt mode to true
- [ ] Cancels all open orders via Kraken REST API
- [ ] Attempts to flatten positions (if supported by exchange)
- [ ] Returns 200 with message: `{"status": "panic_initiated", "orders_cancelled": N}`
- [ ] Idempotent: multiple calls are safe (returns same result)
- [ ] If execution fails, system remains halted (fail-closed)

### Verification Steps
```bash
cd backend
# Start API server
uvicorn backend.api.main:app --port 8000

# Test panic endpoint
curl -X POST http://localhost:8000/api/v1/panic
# Expected: {"status":"panic_initiated","orders_cancelled":N} with HTTP 200

# Verify halt mode is set
redis-cli GET system:halt
# Expected: "1" or "true"

# Test idempotency (call twice)
curl -X POST http://localhost:8000/api/v1/panic
curl -X POST http://localhost:8000/api/v1/panic
# Expected: Both calls succeed, second returns same result

# Verify in OpenAPI docs
curl http://localhost:8000/openapi.json | jq '.paths["/api/v1/panic"]'
# Expected: Panic endpoint defined in OpenAPI spec
```

---

## Ticket 15: API Gateway - Strategies Endpoint

**Priority:** P1  
**Owner:** Backend  
**Scope:** `backend/`  
**Dependencies:** Ticket 2 (DB schema), Ticket 7 (API foundation)

### Files to Create/Modify
- `backend/api/routes/strategies.py` - Strategies list endpoint
- `backend/api/routes/__init__.py` - Register strategies route
- `backend/api/models.py` - Response models (StrategyList, StrategyItem)

### Acceptance Criteria
- [ ] Endpoint: `GET /api/v1/strategies` (matches OpenAPI contract)
- [ ] Returns list of strategies from `strategies` table
- [ ] Response format: `{"strategies": [{"id": "...", "name": "...", "status": "...", ...}]}`
- [ ] Includes: id, name, status, created_at for each strategy
- [ ] Returns empty list if no strategies registered
- [ ] Response matches OpenAPI schema

### Verification Steps
```bash
cd backend
# Start API server
uvicorn backend.api.main:app --port 8000

# Test strategies endpoint
curl http://localhost:8000/api/v1/strategies
# Expected: {"strategies":[]} or list of strategies with HTTP 200

# Insert test strategy in DB
psql $DATABASE_URL -c "INSERT INTO strategies (name, config, status) VALUES ('test-strategy', '{}', 'active');"

# Verify endpoint returns it
curl http://localhost:8000/api/v1/strategies | jq '.strategies[0].name'
# Expected: "test-strategy"

# Verify OpenAPI schema
curl http://localhost:8000/openapi.json | jq '.paths["/api/v1/strategies"]'
# Expected: Endpoint defined in OpenAPI spec
```

---

## Dependencies Summary

**Critical Path:**
1. Ticket 1 (Contracts) → All implementation tickets
2. Ticket 2 (DB Schema) → Tickets 8, 9, 13, 15
3. Ticket 3 (Redis) → Tickets 4, 5, 6, 9, 10
4. Ticket 6 (Ingestor Complete) → M2 can proceed independently

**Parallelizable Groups:**
- **Group A (Foundation):** Tickets 1, 2, 3, 7 (can start immediately)
- **Group B (M1 Core):** Tickets 4, 5 (after 3), Ticket 6 (after 4, 5)
- **Group C (M2 Risk):** Tickets 8, 9, 10 (after 1, 2, 3)
- **Group D (M2 Execution):** Tickets 11, 12, 13 (after 1, 2)
- **Group E (M2 API):** Tickets 14, 15 (after 7, and dependencies)

---

## Verification Checklist (End of M1+M2)

Before marking M1 and M2 complete, verify:

- [ ] All 15 tickets completed and verified
- [ ] Unit test coverage ≥ 80% for all modules
- [ ] Integration test: Strategy → Risk → Execution flow works end-to-end
- [ ] CPU usage < 15% for Data Ingestor (on Intel i5-7500T)
- [ ] WebSocket reconnection < 5 seconds
- [ ] All contracts match implementations (types.md, openapi.yaml, events.md)
- [ ] Database migrations can be applied from scratch
- [ ] API endpoints match OpenAPI contract
- [ ] Risk Manager fails closed (defaults to reject)
- [ ] Execution Engine serializes orders (no nonce collisions)

---

## Notes for Executing Agents

1. **Contracts First:** Do not begin implementation tickets until Ticket 1 is complete and reviewed.
2. **Ownership Boundaries:** Respect folder ownership (Backend owns `backend/`, Contracts owns `contracts/`).
3. **No Inference:** If requirements are unclear, raise questions before implementing.
4. **Testability:** All acceptance criteria must be verifiable via commands or automated tests.
5. **MSSD Compliance:** All implementations must align with `docs/MSSD.md` constraints and semantics.
