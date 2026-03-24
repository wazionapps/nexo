#!/bin/bash
# NEXO PreCompact hook — saves context before Claude Code compacts the conversation.
# Compaction loses context silently. This hook ensures the operator writes a checkpoint
# before that happens, and saves the last known state to a recovery file.
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_NAME="${NEXO_NAME:-NEXO}"
CHECKPOINT_FILE="$NEXO_HOME/coordination/pre-compact-checkpoint.json"

mkdir -p "$NEXO_HOME/coordination"

# Save current state to checkpoint file
python3 -c "
import json, os, sys
from datetime import datetime

nexo_home = os.environ.get('NEXO_HOME', os.path.expanduser('~/.nexo'))
db_path = os.path.join(nexo_home, 'nexo.db')

checkpoint = {
    'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
    'active_sessions': [],
    'last_context_hints': [],
}

try:
    import sqlite3
    if os.path.exists(db_path):
        db = sqlite3.connect(db_path, timeout=5)
        db.row_factory = sqlite3.Row
        sessions = db.execute(
            'SELECT sid, task, started FROM sessions WHERE completed=0'
        ).fetchall()
        checkpoint['active_sessions'] = [
            {'sid': s['sid'], 'task': s['task'], 'started': s['started']}
            for s in sessions
        ]
        # Get last diary drafts for context
        try:
            drafts = db.execute(
                'SELECT sid, last_context_hint, tasks_seen FROM session_diary_draft '
                'ORDER BY updated_at DESC LIMIT 3'
            ).fetchall()
            checkpoint['last_context_hints'] = [
                {'sid': d['sid'], 'hint': d['last_context_hint'], 'tasks': d['tasks_seen']}
                for d in drafts
            ]
        except Exception:
            pass
        db.close()
except Exception:
    pass

with open('$CHECKPOINT_FILE', 'w') as f:
    json.dump(checkpoint, f, indent=2)
" 2>/dev/null || true

# Emit hook response with systemMessage
cat << HOOKEOF
{
  "decision": "approve",
  "systemMessage": "PRE-COMPACT HOOK — Context is about to be compressed. BEFORE continuing:\n\n1. **Write a diary draft NOW** — call nexo_session_diary_write with what you've done so far, decisions made, and current mental state. This is your lifeline after compaction.\n2. **Note your current task** — after compaction you may lose the thread. Write it down in the diary.\n3. **Check pending followups** — if you promised to do something, make sure it's recorded before context is lost.\n4. **Read the checkpoint** after compaction: ${NEXO_HOME}/coordination/pre-compact-checkpoint.json\n\nDo NOT skip this. Compaction without a diary = starting from zero."
}
HOOKEOF
