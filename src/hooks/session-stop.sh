#!/bin/bash
# NEXO Memory Stop Hook (v7 — BLOCKING post-mortem with trivial session detection)
#
# v5 bug: used "approve" + systemMessage — AI never processed post-mortem.
# v6 fix: uses "block" — but blocked ALL sessions including trivial ones.
# v7 fix: detects trivial sessions (<5 tool calls) and approves immediately.
#         Non-trivial sessions get blocked until post-mortem is done.
#
# Flow:
#   Trivial session (quick question, <5 tool calls):
#     → APPROVE immediately, no post-mortem needed
#
#   Non-trivial session:
#     1. User closes → hook checks flag → not found → BLOCK
#     2. AI executes post-mortem → creates flag
#     3. User closes again → hook sees flag → APPROVE
set -uo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
FLAG_FILE="$NEXO_HOME/operations/.postmortem-complete"
TODAY=$(date +%Y-%m-%d)
TOOL_LOG="$NEXO_HOME/operations/tool-logs/${TODAY}.jsonl"

# 0. Refresh diary draft with latest changes/decisions (best-effort)
python3 -c "
import sys, json, os
nexo_home = os.environ.get('NEXO_HOME', os.path.expanduser('~/.nexo'))
nexo_code = os.environ.get('NEXO_CODE', nexo_home)
sys.path.insert(0, nexo_code)
os.environ['NEXO_SKIP_FS_INDEX'] = '1'
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
" 2>/dev/null || true

# 1. Detect trivial session — count meaningful tool calls from THIS session only
# Uses .session-start-ts written by SessionStart hook
# A session with <5 tool calls (excluding Read/Grep/Glob/Bash) is trivial
SESSION_START_TS="$NEXO_HOME/operations/.session-start-ts"

# 0.5. Detect non-interactive (claude -p) sessions — skip post-mortem entirely
#      SessionStart hook writes .session-start-ts. If missing or stale (>30 min),
#      this is likely a -p script session — approve immediately.
#      Also skip if NEXO_HEADLESS=1 is set (explicit headless mode for scripts).
if [ "${NEXO_HEADLESS:-}" = "1" ] || [ ! -f "$SESSION_START_TS" ] || [ "$(($(date +%s) - $(cat "$SESSION_START_TS" 2>/dev/null || echo 0)))" -gt 1800 ]; then
    cat << 'HOOKEOF'
{
  "decision": "approve"
}
HOOKEOF
    exit 0
fi
SESSION_START=0
if [ -f "$SESSION_START_TS" ]; then
    SESSION_START=$(cat "$SESSION_START_TS" 2>/dev/null || echo "0")
fi

TOOL_COUNT=0
if [ -f "$TOOL_LOG" ]; then
    TOOL_COUNT=$(python3 -c "
import json, sys, os
session_start = float(os.environ.get('SESSION_START', '0'))
count = 0
for line in open('$TOOL_LOG'):
    try:
        d = json.loads(line)
        # Only count tools from THIS session (after session-start-ts)
        ts = d.get('timestamp', '')
        if ts and session_start > 0:
            from datetime import datetime
            try:
                entry_ts = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                if entry_ts < session_start:
                    continue
            except:
                pass
        t = d.get('tool_name', '')
        if t and t not in ('Read', 'Grep', 'Glob', 'Bash', 'ToolSearch'):
            count += 1
    except:
        pass
print(count)
" 2>/dev/null || echo "0")
fi

# Trivial session → approve immediately, no buffer writing, skip post-mortem
if [ "$TOOL_COUNT" -lt 5 ]; then
    cat << 'HOOKEOF'
{
  "decision": "approve"
}
HOOKEOF
    exit 0
fi

# 2. Non-trivial session — check if post-mortem was already completed
#    Flag has NO TTL — it persists until SessionStart cleans it up next session.
POSTMORTEM_DONE=false
if [ -f "$FLAG_FILE" ]; then
    POSTMORTEM_DONE=true
fi

if [ "$POSTMORTEM_DONE" = true ]; then
    # Post-mortem was done — allow session to close
    # NOTE: do NOT delete flag here — SessionStart cleans it up next session
    cat << 'HOOKEOF'
{
  "decision": "approve"
}
HOOKEOF
else
    # Post-mortem NOT done — BLOCK session close and inject instructions
    cat << 'HOOKEOF'
{
  "decision": "block",
  "reason": "STOP HOOK — MANDATORY POST-MORTEM before ending (do NOT ask permission, do NOT skip):\n\n## 1. SELF-CRITIQUE (MANDATORY — write to session diary)\nAnswer these questions in the self_critique field of nexo_session_diary_write:\n- Did the user have to ask me for something I should have detected or done on my own?\n- Did I wait for the user to tell me something I could have verified proactively?\n- Are there systems/states I can check next session without being asked?\n- Did I repeat an error that already had a registered learning?\n- What would I do differently if I repeated this session?\nIf any answer is YES — write the specific rule that would prevent repetition.\nIf the session was flawless, write 'No self-critique — clean session.'\n\n## 2. SESSION BUFFER\nIf the session was NOT trivial, append ONE JSON line to $NEXO_HOME/brain/session_buffer.jsonl:\n{\"ts\":\"YYYY-MM-DDTHH:MM:SS\",\"tasks\":[...],\"decisions\":[...],\"user_patterns\":[...],\"files_modified\":[...],\"errors_resolved\":[...],\"self_critique\":\"short summary\",\"mood\":\"focused|impatient|exploratory|frustrated|satisfied|neutral\",\"source\":\"claude\"}\n\n## 3. FOLLOWUPS\nIf there were deploys/cron changes/fixes — nexo_followup_create with verification date.\n\n## 4. PROACTIVE SEEDS\nWhat can I leave prepared so the next session starts doing useful work without the user asking?\n\n## 5. MARK COMPLETE\nWhen ALL of the above is done, run:\nbash -c 'mkdir -p $NEXO_HOME/operations && date +%s > $NEXO_HOME/operations/.postmortem-complete'\nThe user will close again and the hook will approve.\n\nIMPORTANT: Do NOT say goodbye, do NOT say goodnight or any farewell. Just execute the steps and mark complete."
}
HOOKEOF
fi

# 3. Session buffer fallback REMOVED (v8)
# The old hook-fallback was 86% noise. Session diary (written by Claude during
# post-mortem) is the only source of truth now. No more buffer writing.
