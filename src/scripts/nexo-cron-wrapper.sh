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

# Unlock macOS Keychain so headless Claude Code can read auth tokens.
# Claude Code stores its API key in the login keychain which auto-locks.
KEYCHAIN_PASS_FILE="$NEXO_HOME/config/.keychain-pass"
if [ -f "$KEYCHAIN_PASS_FILE" ] && [ "$(uname)" = "Darwin" ]; then
    security unlock-keychain -p "$(cat "$KEYCHAIN_PASS_FILE")" ~/Library/Keychains/login.keychain-db 2>/dev/null || true
fi

# Record start
RUN_ID=$(sqlite3 "$DB" "INSERT INTO cron_runs (cron_id) VALUES ('$CRON_ID'); SELECT last_insert_rowid();" 2>/dev/null)

if [ -z "$RUN_ID" ]; then
    # DB not ready — run without tracking
    exec "$@"
fi

# Run the actual command, capture output
OUTPUT_FILE=$(mktemp)
"$@" > "$OUTPUT_FILE" 2>&1
EXIT_CODE=$?

# Extract summary (last meaningful line, max 500 chars)
SUMMARY=$(tail -5 "$OUTPUT_FILE" | grep -v "^$" | tail -1 | head -c 500 | sed "s/'/''/g")

# Extract error if failed
ERROR=""
if [ $EXIT_CODE -ne 0 ]; then
    ERROR=$(grep -i "error\|exception\|fail\|traceback" "$OUTPUT_FILE" | tail -1 | head -c 500 | sed "s/'/''/g")
fi

# Record end
sqlite3 "$DB" "
    UPDATE cron_runs SET
        ended_at = datetime('now'),
        exit_code = $EXIT_CODE,
        summary = '$SUMMARY',
        error = '$ERROR',
        duration_secs = ROUND((julianday(datetime('now')) - julianday(started_at)) * 86400, 1)
    WHERE id = $RUN_ID;
" 2>/dev/null

# Clean output
rm -f "$OUTPUT_FILE"

exit $EXIT_CODE
