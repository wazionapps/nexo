#!/bin/bash
# NEXO SessionStart hook — generates a comprehensive briefing
# Reads SQLite directly for reminders, followups, active sessions.
# Caches output for 1 hour to avoid regenerating on rapid successive sessions.
set -uo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BRIEFING_FILE="$NEXO_HOME/coordination/session-briefing.txt"
MAX_AGE_SECONDS=3600  # 1 hour cache

mkdir -p "$NEXO_HOME/coordination" "$NEXO_HOME/operations"

# Write session start timestamp for session-scoped tool counting
date +%s > "$NEXO_HOME/operations/.session-start-ts"

# Clean up post-mortem flag from previous session
rm -f "$NEXO_HOME/operations/.postmortem-complete" 2>/dev/null

# Capture Claude Code session_id for inter-terminal inbox hook
HOOK_INPUT=$(cat || true)
CLAUDE_SID=""
if [ -n "$HOOK_INPUT" ]; then
    CLAUDE_SID=$(echo "$HOOK_INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || true)
fi
if [ -n "$CLAUDE_SID" ]; then
    echo "$CLAUDE_SID" > "/tmp/nexo-claude-sid-${CLAUDE_SID}"
    # Also write to a predictable location for the startup prompt
    echo "$CLAUDE_SID" > "$NEXO_HOME/coordination/.claude-session-id"
fi

# If briefing exists and is less than 1 hour old, skip regeneration
if [ -f "$BRIEFING_FILE" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        file_age=$(( $(date +%s) - $(stat -f %m "$BRIEFING_FILE") ))
    else
        file_age=$(( $(date +%s) - $(stat -c %Y "$BRIEFING_FILE") ))
    fi
    if [ "$file_age" -lt "$MAX_AGE_SECONDS" ]; then
        exit 0
    fi
fi

TODAY=$(date +%Y-%m-%d)
WEEKDAY=$(date +%A)

# Generate briefing from SQLite
python3 -c "
import json, os, sys
from datetime import date

today_str = '$TODAY'
weekday = '$WEEKDAY'
nexo_home = os.environ.get('NEXO_HOME', os.path.expanduser('~/.nexo'))
db_path = os.path.join(nexo_home, 'data', 'nexo.db')

lines = []
lines.append(f'## Date: {today_str} ({weekday})')
lines.append('')

# Read from SQLite
reminders_rows = []
followups_rows = []
sessions = []
sqlite_ok = True

try:
    import sqlite3
    if not os.path.exists(db_path):
        sqlite_ok = False
    else:
        db = sqlite3.connect(db_path, timeout=10)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=10000')
        db.row_factory = sqlite3.Row

        try:
            reminders_rows = [dict(r) for r in db.execute(
                'SELECT id, date, description, status, category FROM reminders '
                'WHERE status NOT LIKE \"%COMPLET%\" AND status NOT LIKE \"%DELET%\" '
                'AND status NOT LIKE \"%COMPLETED%\" AND status NOT LIKE \"%DELETED%\"'
            ).fetchall()]
        except Exception:
            pass

        try:
            followups_rows = [dict(r) for r in db.execute(
                'SELECT id, date, description, status FROM followups '
                'WHERE status NOT LIKE \"%COMPLET%\" AND status NOT LIKE \"%COMPLETED%\"'
            ).fetchall()]
        except Exception:
            pass

        try:
            rows = db.execute(
                'SELECT sid, task, started_epoch FROM sessions '
                'WHERE (strftime(\"%s\",\"now\") - last_update_epoch) < 900'
            ).fetchall()
            from datetime import datetime as _dt
            sessions = [{'sid': r['sid'], 'task': r['task'], 'started': _dt.fromtimestamp(r['started_epoch']).strftime('%Y-%m-%d %H:%M') if r['started_epoch'] else '?'} for r in rows]
        except Exception:
            pass

        db.close()
except Exception:
    sqlite_ok = False

if not sqlite_ok:
    lines.append('Database not initialized yet. Run nexo_startup to begin.')
    lines.append('')
    print('\n'.join(lines))
    sys.exit(0)

# Overdue reminders
lines.append('## Overdue Reminders')
found = False
for r in reminders_rows:
    rdate = r.get('date', '')
    if rdate and rdate[:10] < today_str:
        try:
            delta = (date.fromisoformat(today_str) - date.fromisoformat(rdate[:10])).days
        except:
            delta = '?'
        desc = (r.get('description', '') or '')[:120]
        lines.append(f'- [{r[\"id\"]}] {rdate} {desc} -- {delta} day(s) overdue')
        found = True
if not found:
    lines.append('NONE')
lines.append('')

# Today's reminders
lines.append('## Reminders Due Today')
found = False
for r in reminders_rows:
    rdate = r.get('date', '')
    if rdate and rdate[:10] == today_str:
        desc = (r.get('description', '') or '')[:120]
        lines.append(f'- [{r[\"id\"]}] {desc}')
        found = True
if not found:
    lines.append('NONE')
lines.append('')

# Pending followups (due today or overdue)
lines.append('## Followups Due Today or Overdue')
found = False
for r in followups_rows:
    fdate = r.get('date', '')
    if fdate and fdate[:10] <= today_str:
        desc = (r.get('description', '') or '')[:100]
        lines.append(f'- [{r[\"id\"]}] {fdate} {desc}')
        found = True
if not found:
    lines.append('NONE')
lines.append('')

# Active sessions
lines.append('## Active Sessions')
if sessions:
    for s in sessions:
        lines.append(f'- [{s[\"sid\"]}] {s[\"task\"]} (since {s[\"started\"]})')
else:
    lines.append('NONE')
lines.append('')

# Last self-audit
audit_file = os.path.join(nexo_home, 'logs', 'self-audit-summary.json')
if os.path.exists(audit_file):
    try:
        audit = json.load(open(audit_file))
        lines.append('## Last Self-Audit')
        lines.append(json.dumps(audit, indent=2)[:500])
        lines.append('')
    except Exception:
        pass

# Evolution status
evolution_file = os.path.join(nexo_home, 'brain', 'evolution-objective.json')
if os.path.exists(evolution_file):
    try:
        evo = json.load(open(evolution_file))
        lines.append('## Evolution')
        lines.append(f\"Enabled: {bool(evo.get('evolution_enabled', True))}\")
        lines.append(f\"Mode: {evo.get('evolution_mode', 'auto')}\")
        lines.append(f\"Last evolution: {evo.get('last_evolution', 'never')}\")
        lines.append(f\"Total evolutions: {evo.get('total_evolutions', 0)}\")
        if evo.get('disabled_reason'):
            lines.append(f\"Disabled reason: {evo.get('disabled_reason')}\")
        lines.append('')
    except Exception:
        pass

print('\n'.join(lines))
" > "$BRIEFING_FILE" 2>/dev/null

# If generation failed, write minimal briefing
if [ ! -s "$BRIEFING_FILE" ]; then
    echo "## Briefing unavailable — generation error. Use nexo_reminders MCP for fresh data." > "$BRIEFING_FILE"
fi

# ─── Semantic Context: recent work sessions ───
# Append recent session summaries for immediate context
CLAUDE_MEM_DB="$NEXO_HOME/claude-mem.db"

if [ -f "$CLAUDE_MEM_DB" ]; then
    RECENT_SESSIONS=$(python3 -c "
import sqlite3, sys
try:
    db = sqlite3.connect('$CLAUDE_MEM_DB')
    rows = db.execute('''
        SELECT created_at, request, learned, completed
        FROM session_summaries
        ORDER BY id DESC LIMIT 5
    ''').fetchall()
    db.close()
    if rows:
        print()
        print('## Last 5 Work Sessions')
        for r in rows:
            date = r[0][:16] if r[0] else '?'
            req = (r[1] or '')[:120]
            learned = (r[2] or '')[:100]
            print(f'- [{date}] {req}')
            if learned:
                print(f'  -> {learned}')
except Exception as e:
    pass
" 2>/dev/null)

    if [ -n "$RECENT_SESSIONS" ]; then
        echo "$RECENT_SESSIONS" >> "$BRIEFING_FILE"
    fi
fi

# ─── Cortex Report: what happened while user was away ───
# Check brain/ (canonical) first, fall back to cortex/ (legacy)
CORTEX_BRIEFING="$NEXO_HOME/brain/last-briefing.json"
if [ ! -f "$CORTEX_BRIEFING" ] && [ -f "$NEXO_HOME/cortex/last-briefing.json" ]; then
    CORTEX_BRIEFING="$NEXO_HOME/cortex/last-briefing.json"
fi
if [ -f "$CORTEX_BRIEFING" ]; then
    CORTEX_SECTION=$(python3 -c "
import json
try:
    data = json.load(open('$CORTEX_BRIEFING'))
    ts = data.get('timestamp', '?')
    actions = data.get('actions_taken', [])
    signals = data.get('signals_active', [])
    recommendations = data.get('recommendations', [])
    pending_q = data.get('pending_questions_unanswered', [])
    dmn_summary = data.get('dmn_summary', '')

    print()
    print('## Cortex Report (last update: ' + str(ts)[:16] + ')')
    if actions:
        print('### Actions Executed')
        for a in actions[-10:]:
            if isinstance(a, dict):
                print(f'- [{a.get(\"type\",\"?\")}] {a.get(\"detail\",\"\")}')
            else:
                print(f'- {a}')
    if signals:
        print('### Active Signals')
        for s in signals[:5]:
            print(f'- {s}')
    if recommendations:
        print('### Recommendations')
        for r in recommendations[:3]:
            print(f'- {r}')
    if pending_q:
        print(f'### Unanswered Questions: {len(pending_q)}')
        for q in pending_q[:3]:
            if isinstance(q, dict):
                print(f'- {q.get(\"question\",\"?\")}')
            else:
                print(f'- {q}')
    if dmn_summary:
        print(f'### Last DMN: {str(dmn_summary)[:200]}')
except Exception as e:
    print(f'## Cortex Report: error reading briefing ({e})')
" 2>/dev/null)

    if [ -n "$CORTEX_SECTION" ]; then
        echo "$CORTEX_SECTION" >> "$BRIEFING_FILE"
    fi
fi
