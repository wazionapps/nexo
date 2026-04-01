#!/bin/bash
# NEXO Memory Stop Hook (v8 — non-blocking, approve always)
#
# v5: used "approve" + systemMessage — AI never processed post-mortem.
# v6: used "block" — but blocked ALL sessions including trivial ones.
# v7: detects trivial sessions (<5 tool calls) and approves immediately.
# v8: NEVER blocks. The Stop hook fires after EVERY Claude response (not just
#     session close), so blocking causes mid-conversation interruptions.
#     Post-mortem is now handled by:
#       1. Claude detecting closing intent (any language) → diary inline
#       2. auto_close_sessions.py → promotes draft for orphan sessions
#
# This hook only refreshes the diary draft with latest data (best-effort).
set -uo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"

# Refresh diary draft with latest changes/decisions (best-effort)
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

# Always approve — never interrupt the conversation
cat << 'HOOKEOF'
{
  "decision": "approve"
}
HOOKEOF
