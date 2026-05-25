#!/bin/bash
# NEXO Cron Wrapper — Records execution in cron_runs table.
# Usage: nexo-cron-wrapper.sh <cron_id> <command...>
# Example: nexo-cron-wrapper.sh deep-sleep bash nexo-deep-sleep.sh
#
# Wraps any cron command to automatically record start/end/exit_code/summary.
# Used by sync.py when generating LaunchAgents from manifest.json.
#
# Two-phase recording (start → end):
# 1. INSERT cron_runs row at start with ended_at=NULL so the watchdog can
#    distinguish "currently running" from "missed / stuck". Without this,
#    any job that exceeds the next watchdog tick (interval_seconds=1800 by
#    default) looks stale and the watchdog may kickstart -k over it — which
#    is exactly the loop that broke deep-sleep between 2026-04-14 and 2026-04-17.
# 2. UPDATE the row at end with ended_at + exit_code + summary.
# 3. Trap SIGTERM / SIGINT so wrappers killed mid-flight still close their
#    row (exit_code=143 or 130) instead of leaving it NULL forever.

set -uo pipefail

CRON_ID="${1:?Usage: nexo-cron-wrapper.sh <cron_id> <command...>}"
shift

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
DB="$NEXO_HOME/runtime/data/nexo.db"
SPOOL_DIR="$NEXO_HOME/runtime/operations/cron-spool"

# Unlock macOS Keychain so headless Claude Code can read auth tokens.
# Claude Code stores its API key in the login keychain which auto-locks.
KEYCHAIN_PASS_FILE="$NEXO_HOME/personal/config/.keychain-pass"
if [ ! -f "$KEYCHAIN_PASS_FILE" ] && [ -f "$NEXO_HOME/config/.keychain-pass" ]; then
    KEYCHAIN_PASS_FILE="$NEXO_HOME/config/.keychain-pass"
fi
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
RUNTIME_META=$(python3 - "$NEXO_HOME" <<'PY' 2>/dev/null || true
from __future__ import annotations
import json
import sys
from pathlib import Path

nexo_home = Path(sys.argv[1]).expanduser()
schedule = {}
for candidate in (
    nexo_home / "personal" / "config" / "schedule.json",
    nexo_home / "config" / "schedule.json",
):
    try:
        schedule = json.loads(candidate.read_text(encoding="utf-8"))
        break
    except Exception:
        continue

for import_root in (
    nexo_home / "core",
    nexo_home / "core" / "src",
    nexo_home / "src",
):
    if import_root.exists():
        sys.path.insert(0, str(import_root))
try:
    from client_preferences import normalize_client_preferences  # type: ignore
    prefs = normalize_client_preferences(schedule)
    provider_runtime = prefs.get("provider_runtime") if isinstance(prefs.get("provider_runtime"), dict) else {}
    provider = str(provider_runtime.get("automation_provider") or "none").strip().lower()
    backend = str(prefs.get("automation_backend") or provider_runtime.get("automation_backend") or "none").strip().lower()
except Exception:
    provider_runtime = schedule.get("provider_runtime") if isinstance(schedule.get("provider_runtime"), dict) else {}
    selected = str(provider_runtime.get("selected_chat_provider") or "").strip().lower()
    backend_raw = str(provider_runtime.get("automation_backend") or schedule.get("automation_backend") or "claude_code").strip().lower()
    provider_raw = str(provider_runtime.get("automation_provider") or "").strip().lower()
    automation_enabled = schedule.get("automation_enabled", True) is not False
    backend_map = {"claude_code": "anthropic", "codex": "openai", "none": "none"}
    provider_map = {"anthropic": "anthropic", "openai": "openai", "none": "none"}
    if (not automation_enabled) or backend_raw in {"none", "off", "disabled", "false", "0"} or provider_raw in {"none", "off", "disabled", "false", "0"}:
        provider = "none"
        backend = "none"
    else:
        provider = provider_map.get(selected) or provider_map.get(provider_raw) or backend_map.get(backend_raw, "anthropic")
        backend = {"anthropic": "claude_code", "openai": "codex", "none": "none"}.get(provider, "claude_code")
snapshot = {
    "selected_chat_provider": provider_runtime.get("selected_chat_provider") or "",
    "automation_provider": provider,
    "automation_backend": backend,
    "fallback_policy": provider_runtime.get("fallback_policy") or {"automation": "fail_closed"},
}
print(provider + "\t" + backend + "\t" + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
PY
)
if [ -n "$RUNTIME_META" ]; then
    IFS=$'\t' read -r CRON_PROVIDER CRON_BACKEND RUNTIME_SNAPSHOT <<< "$RUNTIME_META"
else
    CRON_PROVIDER=""
    CRON_BACKEND=""
    RUNTIME_SNAPSHOT="{}"
fi

# Phase 1: INSERT row at start (ended_at NULL = "running").
# ROW_ID empty on DB failure; spool-fallback at the end handles that.
ROW_ID=""
ROW_ID=$(python3 - "$DB" "$CRON_ID" "$STARTED_AT" "$CRON_PROVIDER" "$CRON_BACKEND" "$RUNTIME_SNAPSHOT" <<'PY' 2>/dev/null
from __future__ import annotations
import sqlite3
import sys
db_path, cron_id, started_at, provider, backend, runtime_snapshot = sys.argv[1:]
conn = sqlite3.connect(db_path)
try:
    try:
        cur = conn.execute(
            """
            INSERT INTO cron_runs (cron_id, started_at, ended_at, provider, backend, runtime_snapshot)
            VALUES (?, ?, NULL, ?, ?, ?)
            """,
            (cron_id, started_at, provider, backend, runtime_snapshot or "{}"),
        )
    except sqlite3.OperationalError as exc:
        if "provider" not in str(exc) and "backend" not in str(exc) and "runtime_snapshot" not in str(exc):
            raise
        cur = conn.execute(
            "INSERT INTO cron_runs (cron_id, started_at, ended_at) VALUES (?, ?, NULL)",
            (cron_id, started_at),
        )
    conn.commit()
    print(cur.lastrowid)
finally:
    conn.close()
PY
)

OUTPUT_FILE=$(mktemp)
EXIT_CODE=0
SIGNAL_NAME=""

# finalize_row DB writer — also used by signal traps.
# Reads $EXIT_CODE / $SIGNAL_NAME / $OUTPUT_FILE from the outer scope.
finalize_row() {
    local ended_at duration summary error
    ended_at=$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
PY
)
    duration=$(python3 - <<PY
start = float("$START_EPOCH")
import time
print(round(time.time() - start, 1))
PY
)
    summary=$(tail -5 "$OUTPUT_FILE" 2>/dev/null | grep -v "^$" | tail -1 | head -c 500)
    error=""
    if [ "$EXIT_CODE" -ne 0 ]; then
        if [ -n "$SIGNAL_NAME" ]; then
            error="Killed by $SIGNAL_NAME (exit $EXIT_CODE)"
        else
            error=$(grep -i "error\|exception\|fail\|traceback" "$OUTPUT_FILE" 2>/dev/null | tail -1 | head -c 500)
        fi
    fi

    # Update the row we inserted at start — or INSERT fresh if the start write failed.
    if ! python3 - "$DB" "$ROW_ID" "$CRON_ID" "$STARTED_AT" "$ended_at" "$EXIT_CODE" "$summary" "$error" "$duration" "$CRON_PROVIDER" "$CRON_BACKEND" "$RUNTIME_SNAPSHOT" <<'PY' 2>/dev/null
from __future__ import annotations
import sqlite3
import sys
db_path, row_id, cron_id, started_at, ended_at, exit_code, summary, error, duration_secs, provider, backend, runtime_snapshot = sys.argv[1:]
conn = sqlite3.connect(db_path)
try:
    if row_id:
        try:
            conn.execute(
                """
                UPDATE cron_runs
                   SET ended_at=?, exit_code=?, summary=?, error=?, duration_secs=?,
                       provider=COALESCE(NULLIF(provider, ''), ?),
                       backend=COALESCE(NULLIF(backend, ''), ?),
                       runtime_snapshot=CASE
                           WHEN runtime_snapshot IS NULL OR runtime_snapshot = '' OR runtime_snapshot = '{}'
                           THEN ?
                           ELSE runtime_snapshot
                       END
                 WHERE id=?
                """,
                (ended_at, int(exit_code), summary, error, float(duration_secs), provider, backend, runtime_snapshot or "{}", int(row_id)),
            )
        except sqlite3.OperationalError as exc:
            if "provider" not in str(exc) and "backend" not in str(exc) and "runtime_snapshot" not in str(exc):
                raise
            conn.execute(
                """
                UPDATE cron_runs
                   SET ended_at=?, exit_code=?, summary=?, error=?, duration_secs=?
                 WHERE id=?
                """,
                (ended_at, int(exit_code), summary, error, float(duration_secs), int(row_id)),
            )
    else:
        try:
            conn.execute(
                """
                INSERT INTO cron_runs (cron_id, started_at, ended_at, exit_code, summary, error, duration_secs, provider, backend, runtime_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cron_id, started_at, ended_at, int(exit_code), summary, error, float(duration_secs), provider, backend, runtime_snapshot or "{}"),
            )
        except sqlite3.OperationalError as exc:
            if "provider" not in str(exc) and "backend" not in str(exc) and "runtime_snapshot" not in str(exc):
                raise
            conn.execute(
                """
                INSERT INTO cron_runs (cron_id, started_at, ended_at, exit_code, summary, error, duration_secs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cron_id, started_at, ended_at, int(exit_code), summary, error, float(duration_secs)),
            )
    conn.commit()
finally:
    conn.close()
PY
    then
        mkdir -p "$SPOOL_DIR"
        local spool_file="$SPOOL_DIR/${CRON_ID}-$(date +%Y%m%d-%H%M%S)-$$.json"
        python3 - "$spool_file" "$CRON_ID" "$STARTED_AT" "$ended_at" "$EXIT_CODE" "$summary" "$error" "$duration" "$CRON_PROVIDER" "$CRON_BACKEND" "$RUNTIME_SNAPSHOT" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path
spool_file, cron_id, started_at, ended_at, exit_code, summary, error, duration_secs, provider, backend, runtime_snapshot = sys.argv[1:]
Path(spool_file).write_text(
    json.dumps({
        "cron_id": cron_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": int(exit_code),
        "summary": summary,
        "error": error,
        "duration_secs": float(duration_secs),
        "provider": provider,
        "backend": backend,
        "runtime_snapshot": runtime_snapshot or "{}",
    }, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY
        echo "[nexo-cron-wrapper] DB write failed; spooled run to $spool_file" >&2
    fi
}

cleanup() {
    rm -f "$OUTPUT_FILE"
}

CHILD_PID=""

on_signal() {
    local sig="$1"
    local code="$2"
    SIGNAL_NAME="$sig"
    EXIT_CODE="$code"
    # Forward the signal to the child. Bash traps run AFTER the foreground
    # command completes, which is why we launch the command in background
    # and wait on its PID — otherwise a SIGTERM to the wrapper would be
    # delivered only when the child finishes naturally, defeating the
    # purpose of closing the cron_runs row on kill.
    if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null
        # Brief grace period before escalating to SIGKILL so the child gets
        # a chance to clean up on its own.
        local waited=0
        while [ $waited -lt 5 ] && kill -0 "$CHILD_PID" 2>/dev/null; do
            sleep 1
            waited=$((waited + 1))
        done
        kill -KILL "$CHILD_PID" 2>/dev/null
    fi
    finalize_row
    cleanup
    exit "$code"
}

trap cleanup EXIT
trap 'on_signal SIGTERM 143' TERM
trap 'on_signal SIGINT 130' INT
trap 'on_signal SIGHUP 129' HUP


# Plan F0.2.4 — disabled-script gate. Personal_scripts table holds an
# `enabled` flag flipped via `nexo scripts enable|disable <name>`. When
# the operator disables a cron the LaunchAgent stays loaded (no
# launchctl churn) but this wrapper short-circuits to a clean exit 0
# with summary='[disabled]'. The corresponding cron_runs row gets an
# explicit "skipped (disabled)" note so the daily audit can tell apart
# "didn't run because off" from "ran with exit 0".
DISABLED_GATE_OUTPUT=$(python3 - "$DB" "$CRON_ID" <<'PYGATE' 2>/dev/null || true
from __future__ import annotations
import sqlite3
import sys
db_path, cron_id = sys.argv[1:]
try:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT enabled FROM personal_scripts WHERE name = ? LIMIT 1",
            (cron_id,),
        ).fetchone()
    finally:
        conn.close()
except Exception:
    sys.exit(0)
if row is not None and not int(row[0] or 0):
    print("disabled")
PYGATE
)
if [ "$DISABLED_GATE_OUTPUT" = "disabled" ]; then
    EXIT_CODE=0
    SIGNAL_NAME=""
    : > "$OUTPUT_FILE"
    echo "[disabled] $CRON_ID skipped - re-enable with: nexo scripts enable $CRON_ID" > "$OUTPUT_FILE"
    finalize_row
    cleanup
    exit 0
fi

"$@" > "$OUTPUT_FILE" 2>&1 &
CHILD_PID=$!

# `wait` is interruptible by signals — when the trap fires, wait returns
# immediately and on_signal() takes over. When the child finishes
# normally, wait yields its exit code and we fall through to finalize_row
# for the happy path.
wait "$CHILD_PID"
EXIT_CODE=$?
CHILD_PID=""

finalize_row

exit "$EXIT_CODE"
