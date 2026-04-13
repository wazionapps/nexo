#!/bin/bash
# NEXO PostToolUse hook — persists tool call outputs to daily JSONL logs
# Fires automatically after every successful or failed tool use.
# Logs survive context compactions.
# Auto-cleanup: deletes logs >= 30 days old.
# Optimized: skips read-only tools (Read, Grep, Glob, LS, Skill, ToolSearch).

# Read full JSON from stdin first
INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

# Extract tool_name early and exit if read-only (avoids overhead on 90%+ of calls)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || true)

case "$TOOL_NAME" in
    Read|Grep|Glob|LS|Skill|ToolSearch) exit 0 ;;
esac

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
LOG_DIR="$NEXO_HOME/operations/tool-logs"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/${TODAY}.jsonl"

# Build and write record with python3 (faster than jq on macOS when cached)
# Security: redact output of credential-related tools to avoid plaintext secrets in logs
echo "$INPUT" | python3 -c "
import json, sys, re
from datetime import datetime
d = json.load(sys.stdin)
tool_name = d.get('tool_name', 'unknown')

tool_input = d.get('tool_input')
tool_response = d.get('tool_response')

# Redact tools that handle credentials/secrets
SENSITIVE_TOOLS = ('credential', 'secret', 'token', 'password', 'apikey', 'api_key')
if any(kw in tool_name.lower() for kw in SENSITIVE_TOOLS):
    tool_response = '[REDACTED]'
    # Also redact input values (keep keys for debuggability)
    if isinstance(tool_input, dict):
        tool_input = {k: '[REDACTED]' if k not in ('servicio', 'service', 'name', 'key') else v for k, v in tool_input.items()}

record = {
    'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'session_id': d.get('session_id', 'unknown'),
    'tool_name': tool_name,
    'hook_event': d.get('hook_event_name', 'unknown'),
    'tool_use_id': d.get('tool_use_id'),
    'tool_input': tool_input,
    'tool_response': tool_response,
    'error': d.get('error')
}
print(json.dumps(record))
" >> "$LOG_FILE" 2>/dev/null

# ── Layer 1: Auto-diary every 10 tool calls (session-scoped) ─────────
# Extract session_id for per-session counters (prevents cross-terminal contamination)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','global'))" 2>/dev/null || echo "global")
COUNTER_DIR="$NEXO_HOME/operations/counters"
mkdir -p "$COUNTER_DIR"
COUNTER_FILE="$COUNTER_DIR/.tool-call-count-${SESSION_ID}"
NEXO_DB="$NEXO_HOME/data/nexo.db"

# Increment counter (atomic: read+write in one step)
COUNT=1
if [ -f "$COUNTER_FILE" ]; then
    COUNT=$(( $(cat "$COUNTER_FILE" 2>/dev/null || echo 0) + 1 ))
fi
echo "$COUNT" > "$COUNTER_FILE"

# Every 10 tool calls, write a mechanical diary draft to SQLite
if [ $(( COUNT % 10 )) -eq 0 ] && [ -f "$NEXO_DB" ]; then
    python3 -c "
import json, sqlite3, os, sys
from datetime import datetime

db_path = '$NEXO_DB'
log_file = '$LOG_FILE'
count = $COUNT

# Read last 10 tool calls from today's log
entries = []
if os.path.isfile(log_file):
    with open(log_file, 'r') as f:
        lines = f.readlines()
        for line in lines[-10:]:
            try:
                e = json.loads(line.strip())
                name = e.get('tool_name', '?')
                inp = e.get('tool_input', {})
                # Brief args: first key's value, truncated
                brief = ''
                if isinstance(inp, dict):
                    for k, v in list(inp.items())[:1]:
                        brief = str(v)[:60]
                entries.append(f'{name}({brief})')
            except Exception:
                pass

if not entries:
    sys.exit(0)

tools_summary = ', '.join(entries[-10:])

# Get session by claude session_id (scoped), fallback to most recent
session_id = '$SESSION_ID'
conn = sqlite3.connect(db_path, timeout=2)
conn.row_factory = sqlite3.Row

# Try to find NEXO SID mapped to this claude session_id
row = None
if session_id and session_id != 'global':
    row = conn.execute(
        'SELECT sid, task FROM sessions WHERE external_session_id = ? OR claude_session_id = ? LIMIT 1',
        (session_id, session_id)
    ).fetchone()

# Fallback: most recent active session
if not row:
    row = conn.execute(
        'SELECT sid, task FROM sessions ORDER BY last_update_epoch DESC LIMIT 1'
    ).fetchone()

if not row:
    conn.close()
    sys.exit(0)

sid = row['sid']
task = row['task'] or 'unknown'

summary = f'[AUTO-{count}] {len(entries)} tool calls: {tools_summary[:250]}. Task: {task[:100]}'

# Write to session_diary_draft (UPSERT)
conn.execute('''
    INSERT INTO session_diary_draft (sid, summary_draft, tasks_seen, change_ids, decision_ids, last_context_hint, heartbeat_count, updated_at)
    VALUES (?, ?, '[]', '[]', '[]', ?, 0, datetime('now'))
    ON CONFLICT(sid) DO UPDATE SET
        summary_draft = excluded.summary_draft,
        last_context_hint = excluded.last_context_hint,
        updated_at = datetime('now')
''', (sid, summary, f'auto-diary at {count} tool calls'))
conn.commit()
conn.close()
" 2>/dev/null &
    # Reset counter after writing
    echo "0" > "$COUNTER_FILE"
fi

# Cleanup: delete logs >= 30 days old (once daily, uses marker file)
CLEANUP_MARKER="$LOG_DIR/.last-cleanup"
if [ ! -f "$CLEANUP_MARKER" ] || [ "$(cat "$CLEANUP_MARKER" 2>/dev/null)" != "$TODAY" ]; then
    find "$LOG_DIR" -name "*.jsonl" -mtime +30 -delete 2>/dev/null || true
    echo "$TODAY" > "$CLEANUP_MARKER"
fi

exit 0
