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
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/claude}"
FLAG_FILE="$NEXO_HOME/operations/.postmortem-complete"
TODAY=$(date +%Y-%m-%d)
TOOL_LOG="$NEXO_HOME/operations/tool-logs/${TODAY}.jsonl"

# 0. Refresh diary draft with latest changes/decisions (best-effort)
python3 -c "
import sys, json, os
sys.path.insert(0, os.path.expanduser('~/.nexo/nexo-mcp'))
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

# 1. Detect trivial session — count meaningful tool calls from THIS SESSION only
# Uses .session-start-ts written by SessionStart hook
# A session with <5 tool calls (excluding Read/Grep/Glob/Bash) is trivial
SESSION_START_TS="$NEXO_HOME/operations/.session-start-ts"
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
  "reason": "STOP HOOK — POST-MORTEM OBLIGATORIO antes de terminar (NO pidas permiso, NO preguntes):\n\n## 1. AUTOCRÍTICA (OBLIGATORIO — escribir al session diary)\nRespóndete estas preguntas en el campo self_critique del nexo_session_diary_write:\n- ¿Francisco tuvo que pedirme algo que yo debería haber detectado o hecho solo?\n- ¿Esperé a que me dijera algo que podría haber verificado proactivamente?\n- ¿Hay estados/sistemas que puedo chequear en la próxima sesión sin que me lo pidan?\n- ¿Repetí algún error que ya tenía un learning registrado?\n- ¿Qué haría diferente si repitiera esta sesión?\nSi la respuesta a cualquiera es SÍ → escribir la regla concreta que evitaría la repetición.\nSi la sesión fue perfecta, escribir 'Sin autocrítica — sesión limpia.'\n\n## 2. SESSION BUFFER\nSi la sesión NO fue trivial, append UNA línea JSON a ~/.nexo/brain/session_buffer.jsonl:\n{\"ts\":\"YYYY-MM-DDTHH:MM:SS\",\"tasks\":[...],\"decisions\":[...],\"user_patterns\":[...],\"files_modified\":[...],\"errors_resolved\":[...],\"self_critique\":\"resumen corto\",\"mood\":\"focused|impatient|exploratory|frustrated|satisfied|neutral\",\"source\":\"claude\"}\n\n## 3. FOLLOWUPS\nSi hubo deploy/cambios en crons/fixes → nexo_followup_create.\n\n## 4. PROACTIVE SEEDS\n¿Qué puedo dejar preparado para que la próxima sesión arranque haciendo cosas útiles sin que el usuario pida nada?\n\n## 5. MARCAR COMPLETADO\nCuando hayas terminado todo lo anterior, ejecuta:\nbash -c 'mkdir -p ~/.nexo/operations && date +%s > ~/.nexo/operations/.postmortem-complete'\nCuando hayas terminado todo lo anterior, el usuario cerrará de nuevo y esta vez se permitirá.\n\nIMPORTANTE: NO te despidas, NO digas adiós/buenas noches/hasta luego. Solo ejecuta los pasos y marca completado."
}
HOOKEOF
fi

# 3. Session buffer fallback REMOVED (v8)
# The old hook-fallback was 86% noise. Session diary (written by Claude during
# post-mortem) is the only source of truth now. No more buffer writing.
