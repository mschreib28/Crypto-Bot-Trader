# 🤖 Omni-Bot Trading Platform

A Hub-and-Spoke cryptocurrency trading system designed for efficient strategy multiplexing on constrained hardware.

## Overview

Omni-Bot centralizes market data ingestion, risk management, and order execution in a shared **Hub**, while lightweight **Strategy Modules** express trading intent without direct execution authority. This architecture enables:

- **Efficient scaling** on limited hardware (Intel i5-7500T / 16GB RAM)
- **Global risk enforcement** across all strategies
- **Consistent execution** via single exchange egress point
- **Real-time monitoring** via web dashboard

## Features

- 📊 **Market Data Ingestion** — Kraken WebSocket with OHLCV normalization
- 🛡️ **Risk Manager** — Portfolio-level exposure control, fail-closed design
- ⚡ **Execution Engine** — Serialized order submission, nonce management
- 📈 **Strategy Framework** — BaseStrategy class for custom strategies
- 🖥️ **Dashboard** — Real-time monitoring and panic controls
- 🔌 **WebSocket API** — Live updates for frontend integration

## Quick Start

### Prerequisites

- Docker & Docker Compose
- (Optional) Kraken API credentials for live trading

### 1. Clone and Start

```bash
git clone <repo-url>
cd omni-bot

# Start all services
make up

# Wait for services to be healthy
make health
```

### 2. Apply Database Migrations

```bash
make migrate
```

### 3. Seed Default Strategies

```bash
make seed-strategies
```

### 4. Verify System

```bash
make verify-complete
```

### 5. Access Dashboard

Open **http://localhost:8000** for the API, or serve the frontend:

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

## Usage Guide

### Viewing System Status

```bash
# API health check
curl http://localhost:8000/api/v1/health

# Full system status
curl http://localhost:8000/api/v1/status

# List registered strategies
curl http://localhost:8000/api/v1/strategies
```

### Monitoring Signals & Orders

```bash
# Recent signals (approved and rejected)
curl http://localhost:8000/api/v1/signals?limit=20

# Recent executed orders
curl http://localhost:8000/api/v1/orders?limit=20
```

### Emergency Stop (Panic)

**⚠️ Use with caution** — This cancels all open orders and halts the system.

```bash
curl -X POST http://localhost:8000/api/v1/panic
```

The panic action is **idempotent** and safe to retry.

### WebSocket Real-Time Updates

Connect to `ws://localhost:8000/api/v1/ws` for live events:

```json
{"type": "signal_created", "data": {...}, "timestamp": "..."}
{"type": "order_executed", "data": {...}, "timestamp": "..."}
{"type": "system_status", "data": {"halted": true}, "timestamp": "..."}
```

## Configuration

Environment variables (set in `.env` or docker-compose):

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `omni_bot` | Database user |
| `POSTGRES_PASSWORD` | `changeme` | Database password |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `KRAKEN_API_KEY` | (empty) | Kraken API key |
| `KRAKEN_API_SECRET` | (empty) | Kraken API secret |
| `INGESTOR_SYMBOLS` | `BTC/USD,ETH/USD` | Symbols to ingest |
| `INGESTOR_INTERVALS` | `4h,1d` | Bar intervals |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make up` | Start all services |
| `make down` | Stop all services |
| `make logs` | Tail all service logs |
| `make health` | Check service health |
| `make migrate` | Run database migrations |
| `make seed-strategies` | Seed default strategies |
| `make test` | Run all tests |
| `make verify-complete` | Full system verification |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      THE HUB                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   Ingestor  │  │    Risk     │  │  Execution  │     │
│  │  (Kraken WS)│  │   Manager   │  │   Engine    │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │                │                │             │
│         ▼                ▼                ▼             │
│  ┌─────────────────────────────────────────────────┐   │
│  │                 Redis Streams                    │   │
│  └─────────────────────────────────────────────────┘   │
│         │                                              │
│         ▼                                              │
│  ┌─────────────┐                    ┌─────────────┐   │
│  │  PostgreSQL │                    │ API Gateway │   │
│  │  (Audit Log)│                    │  (FastAPI)  │   │
│  └─────────────┘                    └─────────────┘   │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    THE SPOKES                           │
│  ┌─────────────┐              ┌─────────────┐          │
│  │  Momentum   │              │ Mean-Revert │          │
│  │  Strategy   │              │  Strategy   │          │
│  └─────────────┘              └─────────────┘          │
└─────────────────────────────────────────────────────────┘
```

## Included Strategies

| Strategy | Symbol | Description |
|----------|--------|-------------|
| `momentum_btc` | BTC/USD | N-bar breakout momentum |
| `meanrev_eth` | ETH/USD | Bollinger Band mean reversion |

## Adding a New Strategy

1. Create strategy directory:
   ```bash
   mkdir -p research/strategies/my_strategy
   ```

2. Implement strategy class:
   ```python
   # research/strategies/my_strategy/strategy.py
   from research.strategies.base import BaseStrategy
   from research.strategies.types import MarketDataEvent, TradeIntent

   class MyStrategy(BaseStrategy):
       def generate_signals(self, bar: MarketDataEvent) -> TradeIntent | None:
           # Your logic here
           return None
   ```

3. Register in database:
   ```sql
   INSERT INTO strategies (name, config, status)
   VALUES ('my_strategy', '{"symbol": "BTC/USD"}', 'active');
   ```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/status` | GET | System status |
| `/api/v1/strategies` | GET | List strategies |
| `/api/v1/signals` | GET | Recent signals |
| `/api/v1/orders` | GET | Recent orders |
| `/api/v1/panic` | POST | Emergency stop |
| `/api/v1/ws` | WS | Real-time events |

Full API documentation: http://localhost:8000/docs

## Documentation

- **System Design**: `docs/MSSD.md` — Authoritative architecture and constraints
- **API Contracts**: `contracts/openapi.yaml` — OpenAPI specification
- **Operations**: `docs/RUNBOOK.md` — Operational procedures

## Development

```bash
# Run tests
make test

# Run strategy tests with coverage
make test-strategies

# Rebuild containers after code changes
docker compose build
docker compose up -d
```

## License

Private — All rights reserved.
