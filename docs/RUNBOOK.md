# Operations Runbook

Operational procedures for the Omni-Bot platform.

---

## Daily Operations

### Starting the System

```bash
# Start all services
make up

# Verify health
make health

# Check logs for errors
make logs
```

### Stopping the System

```bash
# Graceful shutdown
make down

# Or stop individual services
docker compose stop api
docker compose stop ingestor
```

### Viewing Logs

```bash
# All services
make logs

# API only
make logs-api

# Ingestor only
make logs-ingestor

# Last 100 lines
docker compose logs --tail=100
```

---

## Monitoring

### System Status

```bash
curl http://localhost:8000/api/v1/status | jq '.'
```

Response fields:
- `halted`: System is in emergency halt mode
- `portfolio_exposure`: Current risk exposure %
- `active_strategies`: Number of enabled strategies
- `redis_connected`: Redis health
- `db_connected`: PostgreSQL health

### Service Health

```bash
make health
```

### Verification

```bash
# Full system check
make verify-complete
```

---

## Emergency Procedures

### Emergency Stop (Panic)

**When to use:** Market crash, system malfunction, unexpected behavior.

```bash
# Via API
curl -X POST http://localhost:8000/api/v1/panic

# Or via dashboard PANIC button
```

**What it does:**
1. Sets system to HALT mode
2. Cancels all open orders on Kraken
3. Rejects all new trade intents

**Recovery:**
- System remains halted until manually cleared
- Review logs before resuming

### Manual Halt Mode

```bash
# Set halt mode via Redis
docker compose exec redis redis-cli SET system:halt 1

# Clear halt mode
docker compose exec redis redis-cli SET system:halt 0
```

### Database Recovery

```bash
# Check migration status
make migrate

# Connect to database directly
docker compose exec postgres psql -U omni_bot -d omni_bot

# View recent orders
SELECT * FROM orders ORDER BY created_at DESC LIMIT 20;

# View recent signals
SELECT * FROM signals ORDER BY created_at DESC LIMIT 20;
```

---

## Maintenance

### Updating Code

```bash
# Pull latest changes
git pull

# Rebuild containers
docker compose build

# Restart with new code
docker compose up -d
```

### Database Migrations

```bash
# Apply pending migrations
make migrate

# Check current migration
docker compose exec api sh -c "cd /app/backend && alembic current"
```

### Clearing Data (Development Only)

```bash
# ⚠️ DESTRUCTIVE - removes all data
make clean-all
```

---

## Troubleshooting

### API Not Responding

```bash
# Check if container is running
docker compose ps api

# Check logs
docker compose logs api --tail=50

# Restart API
docker compose restart api
```

### Redis Connection Issues

```bash
# Test Redis
docker compose exec redis redis-cli ping

# Check Redis logs
docker compose logs redis
```

### Database Connection Issues

```bash
# Test PostgreSQL
docker compose exec postgres pg_isready -U omni_bot

# Check database logs
docker compose logs postgres
```

### Ingestor Not Receiving Data

```bash
# Check ingestor logs
docker compose logs ingestor --tail=100

# Verify Kraken WebSocket connection
# Look for "Connected" or "Reconnecting" in logs
```

---

## Contacts

- System Design: `docs/MSSD.md`
- API Reference: http://localhost:8000/docs
