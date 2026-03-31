#!/bin/bash
# NEXO PreCompact Hook — Save checkpoint + inject preservation instructions
# This runs BEFORE Claude Code compacts. It:
# 1. Enriches the session checkpoint in SQLite with latest diary draft data
# 2. Injects a systemMessage telling the operator to save any WIP via MCP tools
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DB="$NEXO_HOME/data/nexo.db"
mkdir -p "$NEXO_HOME/data"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$NEXO_HOME/operations/tool-logs/${TODAY}.jsonl"
LOG_LINES=0
if [ -f "$LOG_FILE" ]; then
    LOG_LINES=$(wc -l < "$LOG_FILE" | tr -d ' ')
fi

# Enrich checkpoint: copy diary draft context into checkpoint if exists
if [ -f "$NEXO_DB" ]; then
    # Get latest active session's diary draft
    LATEST_SID=$(sqlite3 "$NEXO_DB" "
        SELECT sid FROM sessions ORDER BY last_update_epoch DESC LIMIT 1
    " 2>/dev/null || echo "")

    if [ -n "$LATEST_SID" ]; then
        # Write SID to temp file so PostCompact knows which session compacted
        echo "$LATEST_SID" > /tmp/nexo-compacting-sid
        # Pull diary draft data into checkpoint
        sqlite3 "$NEXO_DB" "
            INSERT INTO session_checkpoints (sid, task, current_goal, updated_at)
            SELECT s.sid, s.task, COALESCE(d.last_context_hint, s.task), datetime('now')
            FROM sessions s
            LEFT JOIN session_diary_draft d ON d.sid = s.sid
            WHERE s.sid = '$LATEST_SID'
            ON CONFLICT(sid) DO UPDATE SET
                task = excluded.task,
                current_goal = CASE
                    WHEN excluded.current_goal != '' THEN excluded.current_goal
                    ELSE session_checkpoints.current_goal
                END,
                updated_at = datetime('now')
        " 2>/dev/null || true
    fi
fi

# ── Layer 2: Emergency auto-diary before compaction ──────────────────
# Write an actual session_diary entry (not draft) with mechanical summary
# This is the parachute — if the LLM never wrote a diary, at least this exists
if [ -f "$NEXO_DB" ]; then
    python3 -c "
import json, sqlite3, os, sys
from datetime import datetime

db_path = '$NEXO_DB'
log_file = '$LOG_FILE'

conn = sqlite3.connect(db_path, timeout=3)
conn.row_factory = sqlite3.Row

# Get latest active session
row = conn.execute(
    'SELECT sid, task FROM sessions ORDER BY last_update_epoch DESC LIMIT 1'
).fetchone()
if not row:
    conn.close()
    sys.exit(0)

sid = row['sid']
task = row['task'] or 'unknown'

# Check if a real diary already exists for this session
has_diary = conn.execute(
    'SELECT id FROM session_diary WHERE session_id = ? LIMIT 1', (sid,)
).fetchone()
if has_diary:
    conn.close()
    sys.exit(0)  # LLM already wrote one, no need for emergency diary

# Find last diary timestamp to know where to start reading logs
last_diary = conn.execute(
    'SELECT created_at FROM session_diary ORDER BY created_at DESC LIMIT 1'
).fetchone()
last_diary_ts = last_diary['created_at'] if last_diary else '1970-01-01T00:00:00Z'

# Read tool log entries since last diary
entries = []
modified_files = []
git_actions = []
if os.path.isfile(log_file):
    with open(log_file, 'r') as f:
        for line in f:
            try:
                e = json.loads(line.strip())
                ts = e.get('timestamp', '')
                if ts < last_diary_ts:
                    continue
                name = e.get('tool_name', '?')
                inp = e.get('tool_input', {}) or {}
                brief = ''
                if isinstance(inp, dict):
                    for k, v in list(inp.items())[:1]:
                        brief = str(v)[:80]
                entries.append(f'{name}({brief})')
                # Extract decisions from tool calls
                if name in ('Edit', 'Write'):
                    fp = inp.get('file_path', inp.get('path', ''))
                    if fp:
                        modified_files.append(fp.split('/')[-1])
                if name == 'Bash':
                    cmd = str(inp.get('command', ''))
                    if 'git commit' in cmd or 'git push' in cmd:
                        git_actions.append(cmd[:80])
            except Exception:
                pass

if not entries:
    conn.close()
    sys.exit(0)

# Build mechanical diary
tools_summary = ', '.join(entries[-30:])[:500]
summary = f'[EMERGENCY PRE-COMPACT] {len(entries)} tool calls since last diary. Tools: {tools_summary}'

decisions = ''
if modified_files:
    decisions = 'Modified: ' + ', '.join(set(modified_files))[:300]
if git_actions:
    decisions += (' | Git: ' + ', '.join(git_actions))[:200]
if not decisions:
    decisions = 'No file modifications detected in tool logs'

pending = f'Current task: {task[:200]}'
context_next = 'COMPACTION HAPPENED. Read this diary to continue. Check session_checkpoints and tool-logs for full context.'

# Write actual session_diary entry
conn.execute('''
    INSERT INTO session_diary
        (session_id, decisions, discarded, pending, context_next,
         mental_state, domain, user_signals, summary, source)
    VALUES (?, ?, '', ?, ?, 'auto-generated', 'auto', '', ?, 'pre-compact-hook')
''', (sid, decisions, pending, context_next, summary))
conn.commit()
conn.close()
" 2>/dev/null || true
fi

cat << HOOKEOF
{
  "systemMessage": "CONTEXT IS ABOUT TO BE COMPRESSED.\n\nOBLIGATORY ACTIONS BEFORE COMPACTION:\n1. Save critical state via MCP: nexo_checkpoint_save with current task, active files, decisions, errors, next step, and reasoning thread.\n2. If there is work in progress without a commit, save data via nexo_entity_create, nexo_preference_set, nexo_learning_add, nexo_followup_create.\n3. PERSISTENT TOOL LOGS: ${NEXO_HOME}/operations/tool-logs/${TODAY}.jsonl has ${LOG_LINES} entries.\n4. After compaction, the PostCompact hook will re-inject a Core Memory Block with the checkpoint.\n5. MCP tools (nexo_*) preserve all state — use them to recover context.\n6. EMERGENCY DIARY: An automatic diary was written by the pre-compact hook. The LLM can still write a better one via nexo_session_diary_write."
}
HOOKEOF
