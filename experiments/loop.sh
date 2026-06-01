#!/bin/bash
# Autonomous Experiment Loop Orchestrator
#
# Runs in a tmux session. Alternates between:
#   1. Executing queued experiments (run_experiments.py)
#   2. Invoking Cursor CLI to propose new experiments
#
# Halts gracefully if:
#   - STATUS.md contains "LOOP_HALTED"
#   - STATUS.md contains "STATUS: CONVERGED"
#   - STATUS.md contains "STATUS: PAUSED_BY_HUMAN"
#
# Usage:
#   chmod +x experiments/loop.sh
#   tmux new -d -s improver 'cd "/home/kevin/Documents/Projects/Personal/Crypto Bot Trading" && ./experiments/loop.sh'
#   tmux attach -t improver
#
# Stop with: tmux kill-session -t improver

set -uo pipefail

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STATUS_FILE="$SCRIPT_DIR/STATUS.md"
LOOP_LOG="$SCRIPT_DIR/loop.log"
GENERATIONS_DIR="$SCRIPT_DIR/generations"

mkdir -p "$GENERATIONS_DIR"

# Sleep between cycles (5 min default; adjust as needed)
SLEEP_BETWEEN_CYCLES=300

# Activate venv if present
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# Verify required tools
if ! command -v cursor-agent &> /dev/null; then
    echo "ERROR: cursor-agent not found in PATH" >&2
    echo "Install Cursor CLI: https://docs.cursor.com/cli" >&2
    exit 1
fi

log() {
    local ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[$ts] $1" | tee -a "$LOOP_LOG"
}

check_halt() {
    if [ -f "$STATUS_FILE" ]; then
        if grep -qE "LOOP_HALTED|STATUS: CONVERGED|STATUS: PAUSED_BY_HUMAN" "$STATUS_FILE"; then
            log "HALT condition detected in STATUS.md:"
            grep -E "LOOP_HALTED|STATUS:" "$STATUS_FILE" | tee -a "$LOOP_LOG"
            return 0
        fi
    fi
    return 1
}

cd "$PROJECT_ROOT"

log "═════════════════════════════════════════════════"
log "Experiment loop starting in $PROJECT_ROOT"
log "═════════════════════════════════════════════════"

CYCLE=0
while true; do
    CYCLE=$((CYCLE + 1))
    log ""
    log "──────────── Cycle $CYCLE ────────────"

    # Check halt condition BEFORE running anything
    if check_halt; then
        log "Loop halted. Exiting."
        break
    fi

    # Step 1: Run queued experiments
    log "Running experiments..."
    python "$SCRIPT_DIR/run_experiments.py" 2>&1 | tee -a "$LOOP_LOG"
    EXPERIMENT_EXIT=$?
    if [ $EXPERIMENT_EXIT -ne 0 ]; then
        log "WARNING: run_experiments.py exit code $EXPERIMENT_EXIT"
    fi

    # Snapshot the current experiments.yaml for history
    GEN_NUM=$(printf '%03d' $CYCLE)
    cp "$SCRIPT_DIR/experiments.yaml" "$GENERATIONS_DIR/gen_${GEN_NUM}.yaml" 2>/dev/null

    # Step 2: Invoke Cursor CLI agent for proposal step
    log "Invoking Cursor agent for proposal step..."
    
    # The prompt points the agent to its task file
    AGENT_PROMPT="You are an autonomous trading strategy researcher. Follow the procedure in experiments/ITERATION_TASK.md exactly. Read experiments/AGENT_CONTEXT.md first for your role and rules. Do not modify any code outside experiments/."

    cursor-agent --print --force \
        "$AGENT_PROMPT" 2>&1 | tee -a "$LOOP_LOG"
    AGENT_EXIT=$?
    if [ $AGENT_EXIT -ne 0 ]; then
        log "WARNING: cursor-agent exit code $AGENT_EXIT"
    fi

    # Step 3: Check halt condition AFTER agent runs
    if check_halt; then
        log "Loop halted by agent. Exiting."
        break
    fi

    # Step 4: Sleep before next cycle
    log "Cycle $CYCLE complete. Sleeping ${SLEEP_BETWEEN_CYCLES}s..."
    sleep "$SLEEP_BETWEEN_CYCLES"
done

log "═════════════════════════════════════════════════"
log "Loop terminated after $CYCLE cycles"
log "═════════════════════════════════════════════════"