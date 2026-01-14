# Sprint: Group A (Foundation)
## Parallel Execution Coordination

**Sprint:** Group A  
**Status:** Active  
**Tickets:** 1, 2, 3, 7  
**Target:** Establish foundation for M1 + M2

---

## Assignment Table

| Ticket | Title | Owner | Branch | Folders Touched | Blocking? |
|--------|-------|-------|--------|-----------------|-----------|
| 1 | Finalize v1 Contract Schemas | Contracts Agent | `feat/t1-contract-schemas` | `contracts/` | **YES** |
| 2 | PostgreSQL Schema & Migrations | Backend Agent A | `feat/t2-db-schema` | `backend/db/` | YES (after T1) |
| 3 | Redis Configuration & Utilities | Backend Agent B | `feat/t3-redis-utils` | `backend/redis/`, `backend/config.py` | No |
| 7 | FastAPI Foundation & Health | Backend Agent C | `feat/t7-api-gateway` | `backend/api/` | No |

---

## Parallel Execution Rules

### Timeline

```
T0 ─────────────────────────────────────────────────────────────────────►
│
├── Ticket 1 (Contracts) ──────► MERGE ──► Gate
│                                           │
├── Ticket 3 (Redis) ─────────────────────► │ wait for T1 merge
│                                           │
├── Ticket 7 (API Gateway) ───────────────► │ wait for T1 merge
│                                           │
└── Ticket 2 (DB Schema) ─────────────────► │ wait for T1 merge ──► MERGE
                                            │
                                            └──► Group B can start
```

### Execution Windows

| Phase | Tickets | Action |
|-------|---------|--------|
| Phase 1 | 1, 3, 7 | Execute in parallel. Ticket 3 and 7 can merge after T1. |
| Phase 2 | 2 | Wait for T1 merge (type alignment), then merge. |
| Gate | — | All Group A merged → Group B unlocked. |

---

## Merge Order (Strict)

1. **Ticket 1** (Contracts) — MUST merge first
2. **Ticket 3** (Redis) — Can merge after T1
3. **Ticket 7** (API Gateway) — Can merge after T1
4. **Ticket 2** (DB Schema) — MUST merge last (depends on T1 types)

### Rationale
- Ticket 1 defines authoritative types (`TradeIntent`, `RiskDecision`, `Fill`, `MarketDataEvent`)
- Ticket 2's `signals` and `orders` tables must align with contract types
- Tickets 3 and 7 have no type dependencies, can merge in any order after T1

---

## Authoritative Documents (Precedence Order)

| Priority | Document | Scope | Owner |
|----------|----------|-------|-------|
| 1 | `docs/MSSD.md` | System design, constraints, risk model | Leader |
| 2 | `contracts/types.md` | Domain types (TradeIntent, RiskDecision, Fill) | Contracts |
| 3 | `contracts/openapi.yaml` | HTTP API schemas | Contracts |
| 4 | `contracts/events.md` | Event schemas (MarketDataEvent, etc.) | Contracts |
| 5 | `docs/OWNERSHIP.md` | Folder boundaries | Leader |

**Rule:** If implementation conflicts with contracts, contracts win. If contracts conflict with MSSD, MSSD wins.

---

## Conflict Rules

### File Ownership (No Cross-Boundary Writes)

| Agent | May Write | May NOT Write |
|-------|-----------|---------------|
| Contracts Agent | `contracts/**` | `backend/**`, `frontend/**`, `research/**` |
| Backend Agent A | `backend/db/**` | `contracts/**`, `backend/redis/**`, `backend/api/**` |
| Backend Agent B | `backend/redis/**`, `backend/config.py` | `contracts/**`, `backend/db/**`, `backend/api/**` |
| Backend Agent C | `backend/api/**` | `contracts/**`, `backend/db/**`, `backend/redis/**` |

### Shared File: `backend/requirements.txt`

**Conflict Zone:** Tickets 2, 3, and 7 all add dependencies to `backend/requirements.txt`.

**Resolution Protocol:**
1. Each agent appends their dependencies to the file (do not remove existing lines)
2. Use alphabetical order within sections
3. On merge conflict:
   - Combine all unique dependencies
   - Preserve version pins from the earliest merged branch
   - Leader resolves any version conflicts

**Expected Dependencies:**

| Ticket | Dependencies to Add |
|--------|---------------------|
| 2 | `psycopg2-binary`, `alembic`, `sqlalchemy` |
| 3 | `redis` |
| 7 | `fastapi`, `uvicorn` |

### Shared File: `backend/config.py`

**Conflict Zone:** Tickets 3 and 7 may both create/modify `backend/config.py`.

**Resolution Protocol:**
1. Ticket 3 creates `backend/config.py` with Redis configuration
2. Ticket 7 adds API configuration to the same file
3. If created in parallel:
   - Ticket 3 merges first (Redis config is blocking for more tickets)
   - Ticket 7 rebases and extends the file

**Config Structure (Guidance):**
```python
# backend/config.py
# Section: Redis (Ticket 3)
REDIS_URL = ...

# Section: API (Ticket 7)
API_HOST = ...
API_PORT = ...

# Section: Database (Ticket 2) - added later
DATABASE_URL = ...
```

---

## Pre-Merge Checklist

### Ticket 1 (Contracts)
- [ ] `contracts/types.md` has TradeIntent, RiskDecision, Fill with typed fields
- [ ] `contracts/openapi.yaml` is valid YAML (parseable)
- [ ] `contracts/events.md` has MarketDataEvent with typed fields
- [ ] All fields have types and descriptions
- [ ] No placeholders (`additionalProperties: true` removed from schemas)

### Ticket 2 (DB Schema)
- [ ] Tables: strategies, signals, orders, equity_curve exist
- [ ] `signals` table fields align with TradeIntent (from T1)
- [ ] `orders` table fields align with Fill (from T1)
- [ ] Migration applies cleanly: `alembic upgrade head`
- [ ] Migration rollback works: `alembic downgrade -1`

### Ticket 3 (Redis)
- [ ] `backend/redis/__init__.py` exports `get_redis_client()`
- [ ] `backend/redis/streams.py` has `publish_to_stream()`, `consume_stream()`
- [ ] `backend/redis/keys.py` has key constants matching MSSD § 6.1
- [ ] Connection test passes: `python -c "from backend.redis import get_redis_client; get_redis_client().ping()"`

### Ticket 7 (API Gateway)
- [ ] `GET /api/v1/health` returns `{"status": "healthy"}`
- [ ] OpenAPI docs available at `/docs`
- [ ] Server starts: `uvicorn backend.api.main:app --port 8000`

---

## Handoff Signals

### Ticket 1 Complete → Unblocks
- Ticket 2 (DB Schema) — can finalize table columns
- Group B, C, D, E — can reference contract types

### Ticket 3 Complete → Unblocks
- Ticket 4 (Kraken WS Client) — needs Redis streams
- Ticket 9 (Portfolio Exposure) — needs Redis cache
- Ticket 10 (Halt Mode) — needs Redis key storage

### Ticket 7 Complete → Unblocks
- Ticket 14 (Panic Endpoint) — needs API foundation
- Ticket 15 (Strategies Endpoint) — needs API foundation

### Group A Complete → Unblocks
- Group B (M1 Core): Tickets 4, 5, 6
- Group C (M2 Risk): Tickets 8, 9, 10
- Group D (M2 Execution): Tickets 11, 12, 13
- Group E (M2 API): Tickets 14, 15

---

## Communication Protocol

1. **Completion Signal:** When a ticket is complete, agent posts:
   ```
   ✓ Ticket N complete. Branch: feat/tN-xxx. Ready for merge.
   ```

2. **Blocked Signal:** If blocked on another ticket:
   ```
   ⏸ Ticket N blocked on Ticket M (reason: ...).
   ```

3. **Conflict Signal:** If merge conflict detected:
   ```
   ⚠ Merge conflict on file X. Awaiting resolution.
   ```

4. **Question Signal:** If requirements unclear:
   ```
   ? Ticket N: Clarification needed on [specific question].
   ```

---

## Notes for Agents

1. **Do not start implementation until assigned branch is created.**
2. **Contracts Agent (T1):** Your work is blocking. Prioritize completeness over speed.
3. **Backend Agents (T2, T3, T7):** You may start scaffolding, but do not merge until T1 is merged.
4. **Shared files:** Follow conflict resolution protocol. Do not overwrite others' changes.
5. **MSSD Compliance:** All implementations must satisfy `docs/MSSD.md` constraints.
