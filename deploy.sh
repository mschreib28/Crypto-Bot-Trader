#!/usr/bin/env bash
# ============================================================================
# Crypto Bot Trading - Server Deployment Script
# ============================================================================
# Usage:
#   ./deploy.sh           # Standard deployment
#   ./deploy.sh --rebuild # Force rebuild all containers
#   ./deploy.sh --stop    # Stop all services
#   ./deploy.sh --status  # Show service status
#   ./deploy.sh --logs    # Tail logs from all services
# ============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

# Change to script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Handle command flags
case "${1:-}" in
    --stop)
        log_info "Stopping all services..."
        docker compose down
        log_info "Services stopped."
        exit 0
        ;;
    --status)
        docker compose ps
        exit 0
        ;;
    --logs)
        docker compose logs -f --tail=100
        exit 0
        ;;
    --help|-h)
        echo "Usage: ./deploy.sh [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  (none)      Standard deployment"
        echo "  --rebuild   Force rebuild all containers"
        echo "  --stop      Stop all services"
        echo "  --status    Show service status"
        echo "  --logs      Tail logs from all services"
        echo "  --help      Show this help message"
        exit 0
        ;;
esac

log_section "Crypto Bot Trading Deployment"

# ============================================================================
# Prerequisites Check
# ============================================================================
log_section "Checking Prerequisites"

# Check Docker
if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed. Please install Docker first."
    log_info "Install: https://docs.docker.com/engine/install/"
    exit 1
fi
log_info "Docker: $(docker --version)"

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    log_error "Docker Compose is not installed or not working."
    log_info "Docker Compose v2 is required (docker compose, not docker-compose)"
    exit 1
fi
log_info "Docker Compose: $(docker compose version --short)"

# Check Docker daemon is running
if ! docker info &> /dev/null; then
    log_error "Docker daemon is not running. Please start Docker."
    exit 1
fi
log_info "Docker daemon: Running"

# ============================================================================
# Environment Configuration
# ============================================================================
log_section "Environment Configuration"

ENV_FILE=".env"
ENV_TEMPLATE=".env.example"

# Create .env template if it doesn't exist
if [[ ! -f "$ENV_FILE" ]]; then
    log_warn ".env file not found. Creating from template..."
    
    cat > "$ENV_FILE" << 'ENVEOF'
# ============================================================================
# Crypto Bot Trading - Environment Configuration
# ============================================================================
# REQUIRED: Set your Kraken API credentials before deployment
# ============================================================================

# Kraken API Credentials (REQUIRED)
# Get these from: https://www.kraken.com/u/security/api
KRAKEN_API_KEY=your_api_key_here
KRAKEN_API_SECRET=your_api_secret_here

# Database Configuration (defaults are fine for single-server deployment)
POSTGRES_USER=omni_bot
POSTGRES_PASSWORD=changeme_in_production
POSTGRES_DB=omni_bot

# Account Settings
ACCOUNT_EQUITY=41.67
RISK_PCT_PER_TRADE=2.0
DAILY_LOSS_LIMIT=10.0

# Confidence Thresholds (%)
CONFIDENCE_THRESHOLD_PCT=90.0

# Screener Settings
SCREENER_INTERVAL_SECONDS=60
ENVEOF

    log_error "=============================================="
    log_error "ACTION REQUIRED: Edit .env file with your Kraken API credentials"
    log_error "Then re-run: ./deploy.sh"
    log_error "=============================================="
    exit 1
fi

# Source and validate .env
set -a
source "$ENV_FILE"
set +a

# Validate required variables
if [[ "${KRAKEN_API_KEY:-}" == "your_api_key_here" ]] || [[ -z "${KRAKEN_API_KEY:-}" ]]; then
    log_error "KRAKEN_API_KEY not configured in .env"
    log_error "Please edit .env with your actual Kraken API key"
    exit 1
fi

if [[ "${KRAKEN_API_SECRET:-}" == "your_api_secret_here" ]] || [[ -z "${KRAKEN_API_SECRET:-}" ]]; then
    log_error "KRAKEN_API_SECRET not configured in .env"
    log_error "Please edit .env with your actual Kraken API secret"
    exit 1
fi

log_info "Environment: Configured"
log_info "Account Equity: \$${ACCOUNT_EQUITY:-41.67}"
log_info "Risk per Trade: ${RISK_PCT_PER_TRADE:-2.0}%"

# ============================================================================
# Build and Start Services
# ============================================================================
log_section "Building and Starting Services"

BUILD_ARGS=""
if [[ "${1:-}" == "--rebuild" ]]; then
    log_info "Forcing rebuild of all containers..."
    BUILD_ARGS="--build --force-recreate"
fi

# Pull latest base images (optional, speeds up builds)
log_info "Starting containers..."
docker compose up -d $BUILD_ARGS

# ============================================================================
# Wait for Health Checks
# ============================================================================
log_section "Waiting for Services to be Healthy"

MAX_WAIT=180  # 3 minutes max
WAITED=0
REQUIRED_HEALTHY=5  # postgres, redis, api, ingestor, runner (frontend has no healthcheck)

while [[ $WAITED -lt $MAX_WAIT ]]; do
    # Count healthy containers
    HEALTHY_COUNT=0
    
    # Check each service (frontend uses nginx default, no healthcheck)
    for service in postgres redis api ingestor runner; do
        STATUS=$(docker compose ps "$service" --format "{{.Health}}" 2>/dev/null | tr -d '[:space:]' || echo "")
        if [[ "$STATUS" == "healthy" ]]; then
            HEALTHY_COUNT=$((HEALTHY_COUNT + 1))
        fi
    done
    
    if [[ $HEALTHY_COUNT -ge $REQUIRED_HEALTHY ]]; then
        echo ""
        log_info "All $REQUIRED_HEALTHY services are healthy!"
        break
    fi
    
    # Show progress
    log_info "Healthy: ${HEALTHY_COUNT}/${REQUIRED_HEALTHY} (waited ${WAITED}s)..."
    sleep 5
    WAITED=$((WAITED + 5))
done

echo ""

if [[ $WAITED -ge $MAX_WAIT ]]; then
    log_error "Services did not become healthy within ${MAX_WAIT}s"
    log_error "Current status:"
    docker compose ps
    log_error ""
    log_error "Check logs with: ./deploy.sh --logs"
    exit 1
fi

# ============================================================================
# Database Migrations & Seeding
# ============================================================================
log_section "Running Database Migrations"

# Wait a moment for API to fully initialize
sleep 5

# Run Alembic migrations (must run from backend directory for relative paths)
log_info "Applying database migrations..."
if docker compose exec -T -w /app/backend api alembic upgrade head 2>&1; then
    log_info "Migrations: Applied successfully"
else
    log_warn "Migrations: May have already been applied or failed - check logs"
fi

# Seed strategies (idempotent - uses ON CONFLICT DO NOTHING)
log_info "Seeding strategies..."
if docker compose exec -T postgres psql -U "${POSTGRES_USER:-omni_bot}" -d "${POSTGRES_DB:-omni_bot}" < backend/db/seeds/strategies.sql 2>/dev/null; then
    log_info "Strategies: Seeded"
else
    log_warn "Strategies: Seeding skipped (may already exist)"
fi

# Patch existing strategies with volume_threshold if missing (ON CONFLICT DO NOTHING won't update existing)
log_info "Patching strategies with volume_threshold..."
docker compose exec -T postgres psql -U "${POSTGRES_USER:-omni_bot}" -d "${POSTGRES_DB:-omni_bot}" -c \
    "UPDATE strategies SET config = config || '{\"volume_threshold\": 1.5}'::jsonb WHERE config->>'volume_threshold' IS NULL;" 2>/dev/null || true

# Patch intervals to match available data (5m/1h instead of 4h)
log_info "Patching strategy intervals..."
docker compose exec -T postgres psql -U "${POSTGRES_USER:-omni_bot}" -d "${POSTGRES_DB:-omni_bot}" -c \
    "UPDATE strategies SET config = config || '{\"interval\": \"1h\"}'::jsonb WHERE name = 'trend_following' AND config->>'interval' = '4h';" 2>/dev/null || true
docker compose exec -T postgres psql -U "${POSTGRES_USER:-omni_bot}" -d "${POSTGRES_DB:-omni_bot}" -c \
    "UPDATE strategies SET config = config || '{\"interval\": \"5m\"}'::jsonb WHERE name = 'mean_reversion' AND config->>'interval' = '4h';" 2>/dev/null || true
log_info "Strategies: Patched"

# ============================================================================
# Verify Deployment
# ============================================================================
log_section "Verifying Deployment"

# Check API health
API_URL="http://localhost:${API_PORT:-8001}"
log_info "Checking API health..."

API_HEALTH=$(curl -sf "${API_URL}/api/v1/health" 2>/dev/null || echo "")
if [[ -z "$API_HEALTH" ]]; then
    log_error "API health check failed"
    log_error "Check logs: docker compose logs api"
    exit 1
fi
log_info "API: Healthy"

# Check Kraken connection
log_info "Checking Kraken API connection..."
BALANCE_RESPONSE=$(curl -sf "${API_URL}/api/v1/balance" 2>/dev/null || echo "")
if echo "$BALANCE_RESPONSE" | grep -q '"total_usd"'; then
    BALANCE=$(echo "$BALANCE_RESPONSE" | grep -o '"total_usd":[0-9.]*' | cut -d: -f2)
    log_info "Kraken API: Connected (Balance: \$${BALANCE})"
else
    log_warn "Kraken API: Could not verify balance"
    log_warn "Check your API credentials in .env"
fi

# Check trading status
TRADING_STATUS=$(curl -sf "${API_URL}/api/v1/trading/status" 2>/dev/null || echo "")
if echo "$TRADING_STATUS" | grep -q '"enabled":false'; then
    log_info "Trading: DISABLED (safe default)"
elif echo "$TRADING_STATUS" | grep -q '"enabled":true'; then
    log_warn "Trading: ENABLED"
else
    log_warn "Trading status: Unknown"
fi

# Check strategies
STRATEGIES=$(curl -sf "${API_URL}/api/v1/strategies" 2>/dev/null || echo "")
STRATEGY_COUNT=$(echo "$STRATEGIES" | grep -o '"name"' | wc -l)
log_info "Strategies loaded: $STRATEGY_COUNT"

# ============================================================================
# Deployment Complete
# ============================================================================
log_section "Deployment Complete"

echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo ""

FRONTEND_URL="http://localhost:${FRONTEND_PORT:-3001}"
log_info "=============================================="
log_info "Dashboard:     ${FRONTEND_URL}"
log_info "API:           ${API_URL}"
log_info "API Health:    ${API_URL}/api/v1/health"
log_info "API Docs:      ${API_URL}/docs"
log_info "=============================================="
echo ""
log_warn "IMPORTANT: Trading is DISABLED by default"
log_warn "Enable via dashboard or API when ready:"
log_warn "  curl -X POST ${API_URL}/api/v1/trading/enabled -H 'Content-Type: application/json' -d '{\"enabled\": true}'"
echo ""
log_warn "EMERGENCY STOP (PANIC):"
log_warn "  curl -X POST ${API_URL}/api/v1/panic"
echo ""
log_info "View logs:  ./deploy.sh --logs"
log_info "Stop:       ./deploy.sh --stop"
log_info "Status:     ./deploy.sh --status"
echo ""
