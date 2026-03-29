#!/bin/bash
# NEXO Deep Sleep — Complete overnight session transcript analysis
# Reads ALL Claude Code session transcripts from the day, analyzes with
# Claude CLI (bare mode), and applies findings as feedback memories.
#
# Features:
# - Catch-up: if yesterday was missed (Mac off/asleep), runs it first
# - Uses --bare mode to avoid loading NEXO hooks during analysis
# - Requires ANTHROPIC_API_KEY env var or ~/.claude/anthropic-api-key.txt
#
# Install: Add as LaunchAgent for daily execution (recommended: 4:30 AM)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$HOME/claude/logs"
DEEP_SLEEP_DIR="$HOME/claude/operations/deep-sleep"
LAST_RUN_FILE="$DEEP_SLEEP_DIR/.last-run"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR" "$DEEP_SLEEP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/deep-sleep.log"; }

run_analysis() {
    local DATE="$1"
    log "=== Deep Sleep starting for $DATE ==="

    log "Step 1: Collecting transcripts for $DATE..."
    python3 "$SCRIPT_DIR/deep-sleep/collect_transcripts.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-transcripts.json" ]; then
        log "No transcripts file generated for $DATE. Skipping."
        return 0
    fi

    SESSIONS=$(python3 -c "import json; print(json.load(open('$DEEP_SLEEP_DIR/$DATE-transcripts.json'))['sessions_found'])")
    if [ "$SESSIONS" -eq 0 ]; then
        log "No sessions found for $DATE. Skipping."
        return 0
    fi

    log "Step 2: Analyzing $SESSIONS sessions with Claude CLI..."
    python3 "$SCRIPT_DIR/deep-sleep/analyze_session.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-analysis.json" ]; then
        log "Analysis failed for $DATE. No output generated."
        return 1
    fi

    log "Step 3: Applying findings for $DATE..."
    python3 "$SCRIPT_DIR/deep-sleep/apply_findings.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    log "=== Deep Sleep complete for $DATE ==="
    return 0
}

# --- Catch-up: check if yesterday was missed ---
YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d 2>/dev/null)
LAST_RUN=""
if [ -f "$LAST_RUN_FILE" ]; then
    LAST_RUN=$(cat "$LAST_RUN_FILE")
fi

if [ -n "$YESTERDAY" ] && [ "$LAST_RUN" != "$YESTERDAY" ] && [ "$LAST_RUN" != "$TODAY" ]; then
    if [ ! -f "$DEEP_SLEEP_DIR/$YESTERDAY-analysis.json" ]; then
        log "*** CATCH-UP: $YESTERDAY was missed. Running now. ***"
        run_analysis "$YESTERDAY" || log "Catch-up for $YESTERDAY failed."
    fi
fi

# --- Run today's analysis ---
run_analysis "$TODAY"

# Mark completion
echo "$TODAY" > "$LAST_RUN_FILE"
