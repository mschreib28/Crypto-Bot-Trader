# 📑 Master System Design Document (MSDD)
## The Omni-Bot Trading Platform

**Status:** Authoritative Design  
**Audience:** Engineering, Quantitative, Frontend, and Operations Agents  
**Purpose:** Single source of truth for architecture, constraints, and execution semantics

---

## 1. Vision & Problem Statement

### 1.1 The Core Problem

Most retail trading bots are architected as **isolated, strategy-centric systems** where each strategy independently manages:

- market data connections
- execution logic
- risk configuration
- persistence

On constrained hardware (e.g., **Intel i5-7500T**), scaling this model to many strategies results in:

1. **Resource exhaustion** from redundant WebSocket connections and container overhead
2. **Risk blindness** due to lack of portfolio-wide exposure awareness
3. **Maintenance debt** where exchange or API changes must be patched across many code paths

### 1.2 The Solution

The Omni-Bot platform adopts a **Hub-and-Spoke architecture**:

- **The Hub** centralizes all stateful, resource-heavy concerns:
  - market data ingestion
  - risk management
  - order execution
  - persistence
- **The Spokes** are lightweight strategy modules that express intent, not authority

This enables:
- efficient scaling on limited hardware
- global risk enforcement
- consistent execution semantics
- clean parallel development by specialized agents

---

## 2. Domain Scope & Use Cases

### 2.1 Target Domain

- **Asset Class:** Cryptocurrency
- **Exchange:** Kraken
- **Timeframes:** Swing trading (4H, 1D)
- **Market Regimes:** Momentum and Mean-Reversion

### 2.2 Primary Use Cases

1. **Strategy Multiplexing**  
   Run multiple strategies (e.g., Momentum on BTC, Mean-Reversion on ETH) concurrently with shared infrastructure.

2. **Global Risk Gating**  
   A strategy may generate a valid signal that is rejected because portfolio-level risk constraints are already met.

3. **Historical Replay**  
   Backtest new strategies using the same data formats, schemas, and workflows as the live system.

---

## 3. System Constraints

### 3.1 Hardware Constraints

- CPU: Intel i5-7500T (4 cores / 4 threads)
- RAM: 16 GB
- Storage: 256 GB SSD

### 3.2 External Constraints

- Kraken REST and WebSocket rate limits must be respected via a **single egress point**
- Order execution must be serialized to prevent nonce collisions

### 3.3 Reliability Targets

- Market data WebSocket reconnection: **< 5 seconds**
- Risk Manager must fail closed (default to rejecting trades)

---

## 4. High-Level Architecture

The system is divided into **The Hub (Shared Core)** and **The Spokes (Strategy Modules)**.

### 4.1 The Hub — Core Services

#### 4.1.1 Data Ingestor
- Maintains a single Kraken WebSocket connection
- Normalizes ticks into OHLCV bars
- Publishes market data into Redis Streams

#### 4.1.2 Risk Manager
- Central authority for all trade approval
- Evaluates every trade intent against global risk rules
- Can place the entire system into *Halt Mode*

#### 4.1.3 Execution Engine
- Sole component allowed to:
  - sign orders
  - manage nonces
  - submit, cancel, or modify orders
- Persists execution results to the database

#### 4.1.4 API Gateway
- FastAPI-based interface for:
  - frontend dashboards
  - operational controls (pause, resume, panic)
  - system observability

---

### 4.2 The Spokes — Strategy Modules

- **Runtime:** Containerized Python environment
- **Loading Model:** Strategies are directories under `/strategies/`
- **Authority Model:** No direct access to balances, orders, or execution

#### 4.2.1 Strategy Responsibilities
- Consume normalized market data
- Maintain in-memory indicator state (e.g., rolling windows)
- Emit `TradeIntent` objects

#### 4.2.2 Strategy Prohibitions
Strategies **must not**:
- track positions or account balances
- submit or cancel orders
- persist state across restarts
- bypass the Risk Manager

---

## 5. Risk Model (Authoritative)

### 5.1 Risk Unit Definition

- Risk is measured as **percentage of total account equity**
- Equity includes unrealized PnL at the last snapshot

### 5.2 Risk Evaluation Rules

For every `TradeIntent`, the Risk Manager evaluates:

- current portfolio exposure
- exposure from pending (unfilled) intents
- per-strategy risk limits
- system halt state
- market data freshness

### 5.3 Decision Semantics

- **Approved:** Forward intent to Execution Engine
- **Rejected:** Log rejection with reason
- **Halt Mode:** Reject all intents until cleared

---

## 6. Data Model

### 6.1 In-Memory Store (Redis)

**Streams**
- `market:ohlcv:{symbol}:{interval}`

**Key-Value**
- `portfolio:exposure:total`
- `strategy:{strategy_id}:status`
- `system:halt`

Redis is treated as **ephemeral coordination state**, not a system of record.

---

### 6.2 Persistent Store (PostgreSQL)

| Table         | Purpose |
|--------------|---------|
| `strategies` | Strategy configs, parameters, lifecycle state |
| `signals`    | All generated signals (approved and rejected) |
| `orders`     | Executed orders with fees, slippage, exchange IDs |
| `equity_curve` | Portfolio snapshots every 15 minutes |

PostgreSQL is the **audit log and replay source of truth**.

---

## 7. End-to-End Workflow (Signal → Settle)

1. **Ingestion**  
   Market data arrives via Kraken WS and is published to Redis.

2. **Analysis**  
   Strategies consume data, update indicators, and emit `TradeIntent`.

3. **Validation**  
   Risk Manager evaluates intent against portfolio and system rules.

4. **Execution**  
   Approved intents are executed by the Execution Engine via Kraken REST.

5. **Persistence**  
   Orders and outcomes are written to PostgreSQL.

6. **Propagation**  
   Frontend receives updates via WebSocket subscriptions.

---

## 8. Architectural Decision Records (ADR)

- **ADR-001:** Redis as Message Bus  
  Chosen for sub-millisecond latency and stream semantics.

- **ADR-002:** Strategy Isolation  
  Strategies are directories, not separate repositories, enabling shared utilities while preserving boundaries.

- **ADR-003:** FastAPI  
  Selected for first-class async support and WebSocket handling.

---

## 9. Operations & Runbook

### 9.1 Adding a New Strategy

1. Create `/strategies/<strategy_name>/`
2. Inherit from `BaseStrategy`
3. Implement `generate_signals()`
4. Register strategy configuration in the database

### 9.2 Emergency Procedures

- **Panic Endpoint:**  
  `POST /api/v1/panic`  
  Cancels all open orders and attempts to flatten positions.

- Panic actions are **idempotent** and safe to retry.
- If execution fails, system remains in *Halt Mode*.

---

## 10. Definition of Done (DoD)

A milestone is considered complete only when:

- [ ] Unit test coverage ≥ 80%
- [ ] Integration tests validate Strategy → Risk → Execution flow
- [ ] CPU usage for the module < 15%
- [ ] Documentation updated under `/docs`

---

## 11. Project Milestones

| Milestone | Deliverable | Responsibility |
|---------|-------------|----------------|
| M1: The Hub | Data Ingestor, Redis pipeline, DB schema | Backend |
| M2: The Guard | Risk Manager + Execution API | Backend + Quant |
| M3: The Alpha | Momentum & Mean-Reversion strategies | Quant |
| M4: The Window | Dashboard + Monitoring | Frontend + Ops |

---

## 12. Execution Directive

This document is the **authoritative system blueprint**.

All agents must:
- follow its constraints
- respect its boundaries
- raise questions before deviating

Parallel development is encouraged **only when this document is treated as law**.
