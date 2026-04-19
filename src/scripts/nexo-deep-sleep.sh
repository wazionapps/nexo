#!/bin/bash
# NEXO Deep Sleep — Overnight session analysis with watermark tracking
# Runs at 4:30 AM via LaunchAgent
#
# Watermark approach: tracks the last processed timestamp so nothing is missed.
# Sessions from late-night/early-morning work are included in the next run.
#
# Logs to $NEXO_HOME/runtime/logs/deep-sleep.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
LOG_DIR="$NEXO_HOME/logs"
DEEP_SLEEP_DIR="$NEXO_HOME/runtime/operations/deep-sleep"
WATERMARK_FILE="$DEEP_SLEEP_DIR/.watermark"
RUN_ID=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR" "$DEEP_SLEEP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/deep-sleep.log"; }

# Read watermark (last processed timestamp)
SINCE=""
if [ -f "$WATERMARK_FILE" ]; then
    SINCE=$(cat "$WATERMARK_FILE")
    log "Watermark: processing sessions since $SINCE"
else
    # First run ever: process last 48h
    SINCE=$(date -v-2d '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || date -d "2 days ago" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null)
    log "No watermark found. First run, collecting since $SINCE"
fi

UNTIL=$(date '+%Y-%m-%dT%H:%M:%S')

log "=== Deep Sleep v2 starting (run_id=$RUN_ID) ==="

# Phase 1: Collect all context (Python, no LLM)
log "Phase 1: Collecting context since $SINCE until $UNTIL..."
python3 "$SCRIPT_DIR/deep-sleep/collect.py" "$RUN_ID" "$SINCE" "$UNTIL" >> "$LOG_DIR/deep-sleep.log" 2>&1

if [ ! -f "$DEEP_SLEEP_DIR/$RUN_ID-context.txt" ]; then
    log "No context file generated. Skipping."
    echo "$UNTIL" > "$WATERMARK_FILE"
    log "Watermark updated to $UNTIL (no sessions to process)"
    exit 0
fi

# Check meta for session count
SESSIONS=0
if [ -f "$DEEP_SLEEP_DIR/$RUN_ID-meta.json" ]; then
    SESSIONS=$(python3 -c "import json; print(json.load(open('$DEEP_SLEEP_DIR/$RUN_ID-meta.json'))['sessions_found'])")
fi
if [ "$SESSIONS" -eq 0 ]; then
    log "No sessions found. Skipping."
    echo "$UNTIL" > "$WATERMARK_FILE"
    log "Watermark updated to $UNTIL (no sessions)"
    exit 0
fi

# Phase 2: Extract findings per session (configured automation backend)
log "Phase 2: Extracting findings from $SESSIONS sessions..."
python3 "$SCRIPT_DIR/deep-sleep/extract.py" "$RUN_ID" >> "$LOG_DIR/deep-sleep.log" 2>&1

if [ ! -f "$DEEP_SLEEP_DIR/$RUN_ID-extractions.json" ]; then
    log "Extraction failed. Watermark NOT updated (will retry next run)."
    exit 1
fi

# Phase 3: Cross-session synthesis (configured automation backend, one call)
log "Phase 3: Synthesizing cross-session findings..."
python3 "$SCRIPT_DIR/deep-sleep/synthesize.py" "$RUN_ID" >> "$LOG_DIR/deep-sleep.log" 2>&1

if [ ! -f "$DEEP_SLEEP_DIR/$RUN_ID-synthesis.json" ]; then
    log "Synthesis failed. Falling back to extractions only."
    cp "$DEEP_SLEEP_DIR/$RUN_ID-extractions.json" "$DEEP_SLEEP_DIR/$RUN_ID-synthesis.json"
fi

# Phase 4: Apply findings
log "Phase 4: Applying findings..."
python3 "$SCRIPT_DIR/deep-sleep/apply_findings.py" "$RUN_ID" >> "$LOG_DIR/deep-sleep.log" 2>&1

# Update watermark on success
echo "$UNTIL" > "$WATERMARK_FILE"
log "Watermark updated to $UNTIL"
log "=== Deep Sleep v2 complete (run_id=$RUN_ID) ==="
