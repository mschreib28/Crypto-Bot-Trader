#!/usr/bin/env bash
# ============================================================================
# Deployment Verification Script
# ============================================================================
# Compares local files with server files to ensure deployment matches
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
LOCAL_PATH="$(cd "$(dirname "$0")" && pwd)"

# Key files to verify (Strategy Arbiter implementation)
KEY_FILES=(
    "backend/config.py"
    "backend/screener/service.py"
    "frontend/src/components/ScreenerPanel.tsx"
)

log_section "Deployment Verification"

# Check SSH access
log_info "Checking SSH access to server..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "${SERVER_USER}@${SERVER_HOST}" "echo 'SSH OK'" 2>/dev/null; then
    log_error "Cannot SSH to ${SERVER_USER}@${SERVER_HOST}"
    log_error "Please ensure SSH keys are configured"
    exit 1
fi
log_info "SSH access: OK"

# Verify each key file
log_section "Verifying Key Files"
MISMATCHES=0

for file in "${KEY_FILES[@]}"; do
    local_file="${LOCAL_PATH}/${file}"
    remote_file="${SERVER_PATH}/${file}"
    
    if [[ ! -f "$local_file" ]]; then
        log_warn "Local file not found: $file"
        continue
    fi
    
    log_info "Checking: $file"
    
    # Check if file exists on server
    if ! ssh "${SERVER_USER}@${SERVER_HOST}" "test -f ${remote_file}" 2>/dev/null; then
        log_error "  Server file missing: $file"
        MISMATCHES=$((MISMATCHES + 1))
        continue
    fi
    
    # Compare file checksums
    local_hash=$(md5sum "$local_file" 2>/dev/null | cut -d' ' -f1 || sha256sum "$local_file" 2>/dev/null | cut -d' ' -f1 || echo "")
    remote_hash=$(ssh "${SERVER_USER}@${SERVER_HOST}" "md5sum ${remote_file} 2>/dev/null | cut -d' ' -f1 || sha256sum ${remote_file} 2>/dev/null | cut -d' ' -f1 || echo ''")
    
    if [[ -z "$local_hash" ]] || [[ -z "$remote_hash" ]]; then
        log_warn "  Could not compute checksum (using diff instead)"
        # Fallback to diff
        if ssh "${SERVER_USER}@${SERVER_HOST}" "diff -q ${remote_file} -" < "$local_file" >/dev/null 2>&1; then
            log_info "  ✓ Match"
        else
            log_error "  ✗ Mismatch"
            MISMATCHES=$((MISMATCHES + 1))
        fi
    elif [[ "$local_hash" == "$remote_hash" ]]; then
        log_info "  ✓ Match (checksum: ${local_hash:0:8}...)"
    else
        log_error "  ✗ Mismatch"
        log_error "    Local:  ${local_hash:0:16}..."
        log_error "    Remote: ${remote_hash:0:16}..."
        MISMATCHES=$((MISMATCHES + 1))
    fi
done

# Summary
log_section "Verification Summary"

if [[ $MISMATCHES -eq 0 ]]; then
    log_info "✓ All key files match!"
    exit 0
else
    log_error "✗ Found $MISMATCHES file(s) with mismatches"
    log_error "Files may need to be redeployed"
    exit 1
fi
