#!/bin/bash
# NEXO Stop hook — Full post-mortem and session closure.
# Injects a systemMessage with mandatory self-critique instructions.
# After emitting the hook response, writes a fallback buffer entry
# and triggers intra-day reflection if enough sessions have accumulated.
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_NAME="${NEXO_NAME:-NEXO}"

# 0. Refresh diary draft with latest changes/decisions (best-effort)
python3 -c "
import sys, json, os
sys.path.insert(0, os.environ.get('NEXO_HOME', os.path.expanduser('~/.nexo')))
os.environ['NEXO_SKIP_FS_INDEX'] = '1'
try:
    from db import init_db, get_db, get_active_sessions, upsert_diary_draft, get_diary_draft
    init_db()
    conn = get_db()
    sessions = get_active_sessions()
    for s in sessions:
        sid = s['sid']
        draft = get_diary_draft(sid)
        if not draft:
            continue
        change_ids = [r[0] for r in conn.execute('SELECT id FROM change_log WHERE session_id = ?', (sid,)).fetchall()]
        decision_ids = [r[0] for r in conn.execute('SELECT id FROM decisions WHERE session_id = ?', (sid,)).fetchall()]
        upsert_diary_draft(
            sid=sid,
            tasks_seen=draft['tasks_seen'],
            change_ids=json.dumps(change_ids),
            decision_ids=json.dumps(decision_ids),
            last_context_hint=draft['last_context_hint'],
            heartbeat_count=draft['heartbeat_count'],
            summary_draft=draft['summary_draft'],
        )
except Exception:
    pass
" 2>/dev/null || true

# 1. Emit hook response (must be first output — Claude Code reads this)
cat << HOOKEOF
{
  "decision": "approve",
  "systemMessage": "STOP HOOK — MANDATORY POST-MORTEM before ending (do NOT ask permission, do NOT skip):\n\n## 1. SELF-CRITIQUE (MANDATORY — write to session diary)\nAnswer these questions in the self_critique field of nexo_session_diary_write:\n- Did the user have to ask me for something I should have detected or done on my own?\n- Did I wait for the user to tell me something I could have verified proactively?\n- Are there systems/states I can check next session without being asked?\n- Did I repeat an error that already had a registered learning?\n- What would I do differently if I repeated this session?\nIf any answer is YES — write the specific rule that would prevent repetition.\nIf the session was flawless, write 'No self-critique — clean session.'\n\n## 2. SESSION BUFFER\nIf the session was NOT trivial, append ONE JSON line to ${NEXO_HOME}/brain/session_buffer.jsonl:\n{\"ts\":\"YYYY-MM-DDTHH:MM:SS\",\"tasks\":[...],\"decisions\":[...],\"user_patterns\":[...],\"files_modified\":[...],\"errors_resolved\":[...],\"self_critique\":\"short summary of what I should have done better\",\"mood\":\"focused|impatient|exploratory|frustrated|satisfied|neutral\",\"source\":\"claude\"}\n\n## 3. FOLLOWUPS\nIf there were deploys/cron changes/fixes — nexo_followup_create with verification date.\n\n## 4. PROACTIVE SEEDS\nBefore closing, think: what can I leave prepared so the next session starts doing useful work without the user asking? Create followups with date=tomorrow for proactive verifications.\n\nEntities, preferences, learnings — only if they appeared during the session."
}
HOOKEOF

# 2. Direct session buffer fallback (Claude's MCP write is better but not guaranteed)
BUFFER="$NEXO_HOME/brain/session_buffer.jsonl"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S")

# Check if Claude already wrote to the buffer in the last 60 seconds
SKIP_FALLBACK=false
if [ -f "$BUFFER" ]; then
    LAST_SOURCE=$(python3 -c "
import json, sys
from datetime import datetime, timedelta
try:
    lines = open('$BUFFER').readlines()
    if lines:
        d = json.loads(lines[-1])
        ts = d.get('ts','')
        src = d.get('source','')
        entry_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S')
        if datetime.utcnow() - entry_dt < timedelta(seconds=60) and src == 'claude':
            print('skip')
        else:
            print('write')
    else:
        print('write')
except:
    print('write')
" 2>/dev/null || echo "write")
    if [ "$LAST_SOURCE" = "skip" ]; then
        SKIP_FALLBACK=true
    fi
fi

if [ "$SKIP_FALLBACK" = false ]; then
    mkdir -p "$(dirname "$BUFFER")"
    # Read current adaptive mode for the buffer entry
    ADAPTIVE_MODE="unknown"
    ADAPTIVE_FILE="$NEXO_HOME/brain/adaptive_state.json"
    if [ -f "$ADAPTIVE_FILE" ]; then
        ADAPTIVE_MODE=$(python3 -c "
import json
try:
    d = json.load(open('$ADAPTIVE_FILE'))
    print(d.get('current_mode', 'unknown').lower())
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
    fi
    echo "{\"ts\":\"$TIMESTAMP\",\"tasks\":[\"session ended\"],\"decisions\":[],\"user_patterns\":[],\"files_modified\":[],\"errors_resolved\":[],\"self_critique\":\"hook-fallback, no self-critique captured\",\"mood\":\"unknown\",\"session_end_mode\":\"$ADAPTIVE_MODE\",\"source\":\"hook-fallback\"}" >> "$BUFFER" 2>/dev/null
fi

# 3. Intra-day reflection trigger
# Check if buffer has >=3 sessions AND last reflection was >4h ago
REFLECTION_SCRIPT="$NEXO_HOME/scripts/nexo-reflection.py"
REFLECTION_STATE="$NEXO_HOME/coordination/reflection-log.json"
TRIGGER_THRESHOLD=3

if [ -f "$BUFFER" ] && [ -f "$REFLECTION_SCRIPT" ]; then
    LINE_COUNT=$(wc -l < "$BUFFER" | tr -d ' ')

    if [ "$LINE_COUNT" -ge "$TRIGGER_THRESHOLD" ]; then
        SHOULD_REFLECT=true
        if [ -f "$REFLECTION_STATE" ]; then
            LAST_TS=$(python3 -c "
import json
from datetime import datetime, timedelta
try:
    log = json.load(open('$REFLECTION_STATE'))
    if log:
        last = log[-1]['timestamp']
        last_dt = datetime.strptime(last, '%Y-%m-%d %H:%M')
        if datetime.now() - last_dt < timedelta(hours=4):
            print('too_recent')
        else:
            print('ok')
    else:
        print('ok')
except:
    print('ok')
" 2>/dev/null)
            if [ "$LAST_TS" = "too_recent" ]; then
                SHOULD_REFLECT=false
            fi
        fi

        if [ "$SHOULD_REFLECT" = true ]; then
            # Find Python — prefer the one used by NEXO
            PYTHON=$(which python3 2>/dev/null || echo "/usr/bin/python3")
            nohup "$PYTHON" "$REFLECTION_SCRIPT" \
                >> "$NEXO_HOME/logs/reflection-stdout.log" \
                2>> "$NEXO_HOME/logs/reflection-stderr.log" &
        fi
    fi
fi
