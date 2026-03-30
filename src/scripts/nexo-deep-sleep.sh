#!/bin/bash
# NEXO Deep Sleep — Complete overnight session analysis
# Runs at 4:30 AM via LaunchAgent
# Reads ALL session transcripts from the day, analyzes with Claude CLI,
# and applies findings (learnings, feedbacks, followups, trust adjustments)
#
# Features:
# - Catch-up: if yesterday was missed (Mac off/asleep), runs it first
# - Logs to ~/claude/logs/deep-sleep.log
# - Marks completion in .last-run for watchdog monitoring

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

    # Step 1: Collect transcripts
    log "Step 1: Collecting transcripts for $DATE..."
    python3 "$SCRIPT_DIR/deep-sleep/collect_transcripts.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    # Check if transcripts were found
    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-transcripts.json" ]; then
        log "No transcripts file generated for $DATE. Skipping."
        return 0
    fi

    SESSIONS=$(python3 -c "import json; print(json.load(open('$DEEP_SLEEP_DIR/$DATE-transcripts.json'))['sessions_found'])")
    if [ "$SESSIONS" -eq 0 ]; then
        log "No sessions found for $DATE. Skipping."
        return 0
    fi

    # Step 2: Analyze with Claude CLI
    log "Step 2: Analyzing $SESSIONS sessions with Claude CLI..."
    python3 "$SCRIPT_DIR/deep-sleep/analyze_session.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-analysis.json" ]; then
        log "Analysis failed for $DATE. No output generated."
        return 1
    fi

    # Step 3: Apply findings
    log "Step 3: Applying findings for $DATE..."
    python3 "$SCRIPT_DIR/deep-sleep/apply_findings.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    log "=== Deep Sleep complete for $DATE ==="
    return 0
}

# --- Catch-up: check if the day before yesterday was missed ---
YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d 2>/dev/null)
DAY_BEFORE=$(date -v-2d +%Y-%m-%d 2>/dev/null || date -d "2 days ago" +%Y-%m-%d 2>/dev/null)
LAST_RUN=""
if [ -f "$LAST_RUN_FILE" ]; then
    LAST_RUN=$(cat "$LAST_RUN_FILE")
fi

if [ -n "$DAY_BEFORE" ] && [ "$LAST_RUN" != "$DAY_BEFORE" ] && [ "$LAST_RUN" != "$YESTERDAY" ]; then
    # Day before yesterday wasn't analyzed — catch up
    if [ ! -f "$DEEP_SLEEP_DIR/$DAY_BEFORE-analysis.json" ]; then
        log "*** CATCH-UP: $DAY_BEFORE was missed. Running now. ***"
        run_analysis "$DAY_BEFORE" || log "Catch-up for $DAY_BEFORE failed."
    fi
fi

# --- Run yesterday's analysis (main task — at 4:30 AM, today has no sessions yet) ---
run_analysis "$YESTERDAY"

# Mark completion with yesterday's date (what we actually analyzed)
echo "$YESTERDAY" > "$LAST_RUN_FILE"
