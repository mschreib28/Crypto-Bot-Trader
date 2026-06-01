#!/usr/bin/env bash
# ============================================================================
# Complete Deployment and Verification Script (SSH / rsync, no git)
# ============================================================================
# Syncs local project to server via rsync, runs deploy.sh, verifies integrity
# ============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_section() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

# Configuration
SERVER_USER="ark"
SERVER_HOST="corpus"
SERVER_PATH="/home/ark/crypto-bot-trading"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

log_section "Deployment and Verification"

# Build frontend before sync (deploy needs frontend/dist)
log_section "Building Frontend"
if [[ -d "frontend" ]]; then
    log_info "Running npm run build in frontend..."
    if (cd frontend && npm run build); then
        log_info "Frontend build successful"
    else
        log_error "Frontend build failed"
        exit 1
    fi
else
    log_warn "frontend/ not found, skipping build"
fi

# Deploy to server
log_section "Deploying to Server"
log_info "SSH: ${SERVER_USER}@${SERVER_HOST}"
log_info "Path: ${SERVER_PATH}"

# Check SSH access
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "${SERVER_USER}@${SERVER_HOST}" "echo 'SSH OK'" 2>/dev/null; then
    log_error "Cannot SSH to ${SERVER_USER}@${SERVER_HOST}"
    log_error "Please ensure SSH keys are configured"
    exit 1
fi

# Ensure remote directory exists
log_info "Ensuring remote directory exists..."
ssh "${SERVER_USER}@${SERVER_HOST}" "mkdir -p ${SERVER_PATH}" || {
    log_error "Failed to create remote directory"
    exit 1
}

# Sync project via rsync (exclude secrets, caches, git)
log_info "Syncing project to server via rsync..."
rsync -avz --delete \
    --exclude '.env' \
    --exclude '.git' \
    --exclude 'node_modules' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.cursor' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '*.egg-info' \
    --exclude '.pytest_cache' \
    --exclude 'frontend/node_modules' \
    --exclude 'research/__pycache__' \
    ./ \
    "${SERVER_USER}@${SERVER_HOST}:${SERVER_PATH}/" || {
    log_error "rsync failed"
    exit 1
}
log_info "Sync complete"

# Deploy using deploy.sh
log_info "Running deployment script on server..."
ssh "${SERVER_USER}@${SERVER_HOST}" "cd ${SERVER_PATH} && ./deploy.sh --rebuild" || {
    log_error "Deployment failed"
    exit 1
}

# Wait a moment for services to start
log_info "Waiting for services to initialize..."
sleep 10

# Verify deployment
log_section "Verifying Deployment"

# Check service status
log_info "Checking service status..."
ssh "${SERVER_USER}@${SERVER_HOST}" "cd ${SERVER_PATH} && ./deploy.sh --status" || {
    log_warn "Could not check service status"
}

# Verify key files match
log_section "Verifying File Integrity"

KEY_FILES=(
    "backend/config.py"
    "backend/screener/service.py"
    "backend/screener/engine.py"
    "backend/redis/keys.py"
    "frontend/src/components/ScreenerPanel.tsx"
    "frontend/src/components/ActivityLog.tsx"
)

MISMATCHES=0

for file in "${KEY_FILES[@]}"; do
    local_file="${SCRIPT_DIR}/${file}"
    remote_file="${SERVER_PATH}/${file}"
    
    if [[ ! -f "$local_file" ]]; then
        log_warn "Local file not found: $file"
        continue
    fi
    
    log_info "Verifying: $file"
    
    # Check if file exists on server
    if ! ssh "${SERVER_USER}@${SERVER_HOST}" "test -f ${remote_file}" 2>/dev/null; then
        log_error "  ✗ Server file missing"
        MISMATCHES=$((MISMATCHES + 1))
        continue
    fi
    
    # Compare using diff
    if ssh "${SERVER_USER}@${SERVER_HOST}" "diff -q ${remote_file} -" < "$local_file" >/dev/null 2>&1; then
        log_info "  ✓ Match"
    else
        log_error "  ✗ Mismatch detected"
        MISMATCHES=$((MISMATCHES + 1))
        
        # Show diff preview
        log_warn "  Showing differences:"
        ssh "${SERVER_USER}@${SERVER_HOST}" "diff ${remote_file} -" < "$local_file" | head -20 || true
    fi
done

# Final summary
log_section "Deployment Summary"

if [[ $MISMATCHES -eq 0 ]]; then
    log_info "✓ Deployment successful!"
    log_info "✓ All key files match between local and server"
    echo ""
    log_info "Next steps:"
    log_info "  1. Check service logs: ssh ${SERVER_USER}@${SERVER_HOST} 'cd ${SERVER_PATH} && ./deploy.sh --logs'"
    log_info "  2. Verify API health: curl http://${SERVER_HOST}:8001/api/v1/health"
    exit 0
else
    log_error "✗ Deployment completed but found $MISMATCHES file mismatch(es)"
    log_error "Please review the differences above"
    exit 1
fi
