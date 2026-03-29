#!/bin/bash
# NEXO SessionStart hook — generates a comprehensive briefing.
# Reads SQLite directly for reminders, followups, active sessions.
# Caches output for 1 hour to avoid regenerating on rapid successive sessions.
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BRIEFING_FILE="$NEXO_HOME/coordination/session-briefing.txt"
MAX_AGE_SECONDS=3600  # 1 hour cache

mkdir -p "$NEXO_HOME/coordination" "$NEXO_HOME/operations"

# Clean up post-mortem flag from previous session
rm -f "$NEXO_HOME/operations/.postmortem-complete" 2>/dev/null

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
db_path = os.path.join(nexo_home, 'nexo.db')

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
                'SELECT sid, task, started FROM sessions '
                'WHERE completed=0 AND (strftime(\"%s\",\"now\") - last_update) < 900'
            ).fetchall()
            sessions = [{'sid': r['sid'], 'task': r['task'], 'started': r['started'][:16]} for r in rows]
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
        lines.append(f'- [{r[\"id\"]}] {rdate} {desc} — {delta} day(s) overdue')
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

print('\n'.join(lines))
" > "$BRIEFING_FILE" 2>/dev/null

# If generation failed, write minimal briefing
if [ ! -s "$BRIEFING_FILE" ]; then
    echo "## Briefing unavailable — generation error. Use nexo_reminders MCP for fresh data." > "$BRIEFING_FILE"
fi
