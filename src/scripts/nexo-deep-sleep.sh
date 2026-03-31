#!/bin/bash
# NEXO Deep Sleep — Complete overnight session analysis
# Runs at 4:30 AM via LaunchAgent
# Reads ALL session transcripts from the day, analyzes with Claude CLI,
# and applies findings (learnings, feedbacks, followups, trust adjustments)
#
# Features:
# - Catch-up: if yesterday was missed (Mac off/asleep), runs it first
# - Logs to $NEXO_HOME/logs/deep-sleep.log
# - Marks completion in .last-run for watchdog monitoring

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
LOG_DIR="$NEXO_HOME/logs"
DEEP_SLEEP_DIR="$NEXO_HOME/operations/deep-sleep"
LAST_RUN_FILE="$DEEP_SLEEP_DIR/.last-run"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR" "$DEEP_SLEEP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/deep-sleep.log"; }

run_analysis() {
    local DATE="$1"
    log "=== Deep Sleep v2 starting for $DATE ==="

    # Phase 1: Collect all context (Python, no LLM)
    log "Phase 1: Collecting context for $DATE..."
    python3 "$SCRIPT_DIR/deep-sleep/collect.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-context.txt" ]; then
        log "No context file generated for $DATE. Skipping."
        return 0
    fi

    # Check meta for session count
    SESSIONS=0
    if [ -f "$DEEP_SLEEP_DIR/$DATE-meta.json" ]; then
        SESSIONS=$(python3 -c "import json; print(json.load(open('$DEEP_SLEEP_DIR/$DATE-meta.json'))['sessions_found'])")
    elif [ -f "$DEEP_SLEEP_DIR/$DATE-index.json" ]; then
        SESSIONS=$(python3 -c "import json; print(json.load(open('$DEEP_SLEEP_DIR/$DATE-index.json'))['sessions_found'])")
    fi
    if [ "$SESSIONS" -eq 0 ]; then
        log "No sessions found for $DATE. Skipping."
        return 0
    fi

    # Phase 2: Extract findings per session (Claude Opus)
    log "Phase 2: Extracting findings from $SESSIONS sessions..."
    python3 "$SCRIPT_DIR/deep-sleep/extract.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-extractions.json" ]; then
        log "Extraction failed for $DATE. No output."
        return 1
    fi

    # Phase 3: Cross-session synthesis (Claude Opus, one call)
    log "Phase 3: Synthesizing cross-session findings..."
    python3 "$SCRIPT_DIR/deep-sleep/synthesize.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    if [ ! -f "$DEEP_SLEEP_DIR/$DATE-synthesis.json" ]; then
        log "Synthesis failed for $DATE. Falling back to extractions only."
        # Fall back: apply extractions directly
        cp "$DEEP_SLEEP_DIR/$DATE-extractions.json" "$DEEP_SLEEP_DIR/$DATE-synthesis.json"
    fi

    # Phase 4: Apply findings
    log "Phase 4: Applying findings..."
    python3 "$SCRIPT_DIR/deep-sleep/apply_findings.py" "$DATE" 2>&1 | tee -a "$LOG_DIR/deep-sleep.log"

    log "=== Deep Sleep v2 complete for $DATE ==="
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
