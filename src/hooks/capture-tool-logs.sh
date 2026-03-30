#!/bin/bash
# NEXO PostToolUse hook — persists tool call outputs to daily JSONL logs
# Fires automatically after every successful or failed tool use.
# Logs survive context compactions.
# Auto-cleanup: deletes logs >= 30 days old.
# Optimized: skips read-only tools (Read, Grep, Glob, LS, Skill, ToolSearch).

# Read full JSON from stdin first
INPUT=$(cat)

# Extract tool_name early and exit if read-only (avoids overhead on 90%+ of calls)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)

case "$TOOL_NAME" in
    Read|Grep|Glob|LS|Skill|ToolSearch) exit 0 ;;
esac

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
LOG_DIR="$NEXO_HOME/operations/tool-logs"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/${TODAY}.jsonl"

# Build and write record with python3 (faster than jq on macOS when cached)
echo "$INPUT" | python3 -c "
import json, sys
from datetime import datetime
d = json.load(sys.stdin)
record = {
    'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'session_id': d.get('session_id', 'unknown'),
    'tool_name': d.get('tool_name', 'unknown'),
    'hook_event': d.get('hook_event_name', 'unknown'),
    'tool_use_id': d.get('tool_use_id'),
    'tool_input': d.get('tool_input'),
    'tool_response': d.get('tool_response'),
    'error': d.get('error')
}
print(json.dumps(record))
" >> "$LOG_FILE" 2>/dev/null

# Cleanup: delete logs >= 30 days old (once daily, uses marker file)
CLEANUP_MARKER="$LOG_DIR/.last-cleanup"
if [ ! -f "$CLEANUP_MARKER" ] || [ "$(cat "$CLEANUP_MARKER" 2>/dev/null)" != "$TODAY" ]; then
    find "$LOG_DIR" -name "*.jsonl" -mtime +30 -delete 2>/dev/null || true
    echo "$TODAY" > "$CLEANUP_MARKER"
fi

exit 0
