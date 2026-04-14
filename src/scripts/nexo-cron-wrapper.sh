#!/bin/bash
# NEXO Cron Wrapper — Records execution in cron_runs table.
# Usage: nexo-cron-wrapper.sh <cron_id> <command...>
# Example: nexo-cron-wrapper.sh deep-sleep bash nexo-deep-sleep.sh
#
# Wraps any cron command to automatically record start/end/exit_code/summary.
# Used by sync.py when generating LaunchAgents from manifest.json.

set -uo pipefail

CRON_ID="${1:?Usage: nexo-cron-wrapper.sh <cron_id> <command...>}"
shift

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
DB="$NEXO_HOME/data/nexo.db"
SPOOL_DIR="$NEXO_HOME/operations/cron-spool"

# Unlock macOS Keychain so headless Claude Code can read auth tokens.
# Claude Code stores its API key in the login keychain which auto-locks.
KEYCHAIN_PASS_FILE="$NEXO_HOME/config/.keychain-pass"
if [ -f "$KEYCHAIN_PASS_FILE" ] && [ "$(uname)" = "Darwin" ]; then
    security unlock-keychain -p "$(cat "$KEYCHAIN_PASS_FILE")" ~/Library/Keychains/login.keychain-db 2>/dev/null || true
fi

START_EPOCH=$(python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
)
STARTED_AT=$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
PY
)

# Run the actual command, capture output
OUTPUT_FILE=$(mktemp)
trap 'rm -f "$OUTPUT_FILE"' EXIT
"$@" > "$OUTPUT_FILE" 2>&1
EXIT_CODE=$?
ENDED_AT=$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
PY
)
DURATION_SECS=$(python3 - <<PY
start = float("$START_EPOCH")
import time
print(round(time.time() - start, 1))
PY
)

# Extract summary (last meaningful line, max 500 chars)
SUMMARY=$(tail -5 "$OUTPUT_FILE" | grep -v "^$" | tail -1 | head -c 500)

# Extract error if failed
ERROR=""
if [ $EXIT_CODE -ne 0 ]; then
    ERROR=$(grep -i "error\|exception\|fail\|traceback" "$OUTPUT_FILE" | tail -1 | head -c 500)
fi

if ! python3 - "$DB" "$CRON_ID" "$STARTED_AT" "$ENDED_AT" "$EXIT_CODE" "$SUMMARY" "$ERROR" "$DURATION_SECS" <<'PY'
from __future__ import annotations

import sqlite3
import sys

db_path, cron_id, started_at, ended_at, exit_code, summary, error, duration_secs = sys.argv[1:]
conn = sqlite3.connect(db_path)
try:
    conn.execute(
        """
        INSERT INTO cron_runs (
            cron_id, started_at, ended_at, exit_code, summary, error, duration_secs
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cron_id,
            started_at,
            ended_at,
            int(exit_code),
            summary,
            error,
            float(duration_secs),
        ),
    )
    conn.commit()
finally:
    conn.close()
PY
then
    mkdir -p "$SPOOL_DIR"
    SPOOL_FILE="$SPOOL_DIR/${CRON_ID}-$(date +%Y%m%d-%H%M%S)-$$.json"
    python3 - "$SPOOL_FILE" "$CRON_ID" "$STARTED_AT" "$ENDED_AT" "$EXIT_CODE" "$SUMMARY" "$ERROR" "$DURATION_SECS" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

spool_file, cron_id, started_at, ended_at, exit_code, summary, error, duration_secs = sys.argv[1:]
payload = {
    "cron_id": cron_id,
    "started_at": started_at,
    "ended_at": ended_at,
    "exit_code": int(exit_code),
    "summary": summary,
    "error": error,
    "duration_secs": float(duration_secs),
}
Path(spool_file).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
    echo "[nexo-cron-wrapper] DB write failed; spooled run to $SPOOL_FILE" >&2
fi

exit $EXIT_CODE
