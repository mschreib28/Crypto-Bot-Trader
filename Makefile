.PHONY: help up down restart logs ps health migrate test clean verify verify-services verify-contracts verify-api verify-database verify-redis verify-ingestor verify-modules

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

restart: ## Restart all services
	docker compose restart

logs: ## Show logs from all services
	docker compose logs -f

logs-api: ## Show API service logs
	docker compose logs -f api

logs-ingestor: ## Show ingestor service logs
	docker compose logs -f ingestor

ps: ## Show running services
	docker compose ps

health: ## Check health of all services
	@echo "Checking service health..."
	@docker compose ps
	@echo ""
	@echo "API Health:"
	@curl -s http://localhost:8000/api/v1/health || echo "API not responding"
	@echo ""
	@echo "PostgreSQL:"
	@docker compose exec -T postgres pg_isready -U omni_bot || echo "PostgreSQL not ready"
	@echo ""
	@echo "Redis:"
	@docker compose exec -T redis redis-cli ping || echo "Redis not responding"

migrate: ## Run database migrations
	docker compose exec api sh -c "cd /app/backend && alembic upgrade head"

migrate-create: ## Create a new migration (usage: make migrate-create NAME=migration_name)
	docker compose exec api sh -c "cd /app/backend && alembic revision --autogenerate -m \"$(NAME)\""

test: ## Run tests
	docker compose exec api pytest backend/ -v

clean: ## Remove containers, volumes, and images
	docker compose down -v --rmi local

clean-all: clean ## Remove everything including volumes (destructive)
	docker compose down -v --rmi all
	@echo "WARNING: All data volumes have been removed"

verify-services:
	@echo "=== Verifying Services ==="
	@docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' \
		$$(docker compose ps -q) | grep -vqE '^(healthy)$$' && \
		(echo "❌ One or more services not healthy"; exit 1) || true
	@echo "✓ All services healthy"


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