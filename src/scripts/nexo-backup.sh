#!/bin/bash
# NEXO DB hourly backup — crontab: 0 * * * * $NEXO_HOME/core/scripts/nexo-backup.sh
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DIR="$NEXO_HOME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CORE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$NEXO_HOME/runtime/backups"
if [ ! -d "$BACKUP_DIR" ] && [ -d "$NEXO_HOME/backups" ]; then
    BACKUP_DIR="$NEXO_HOME/backups"
fi
WEEKLY_DIR="$BACKUP_DIR/weekly"
DB="$NEXO_HOME/runtime/data/nexo.db"
LOCAL_CONTEXT_DB="$NEXO_HOME/runtime/memory/local-context.db"
LOCK_FILE="$NEXO_HOME/runtime/logs/local-index.lock"
RETENTION_HOURS="${NEXO_BACKUP_RETENTION_HOURS:-24}"
KEEP_LAST="${NEXO_BACKUP_KEEP_LAST:-3}"
FAMILY_KEEP_LAST="${NEXO_BACKUP_FAMILY_KEEP_LAST:-2}"
LOCAL_CONTEXT_RETENTION_HOURS="${NEXO_LOCAL_CONTEXT_BACKUP_RETENTION_HOURS:-24}"
LOCAL_CONTEXT_KEEP_LAST="${NEXO_LOCAL_CONTEXT_BACKUP_KEEP_LAST:-2}"
BUSY_TIMEOUT_MS="${NEXO_BACKUP_BUSY_TIMEOUT_MS:-5000}"
RECENT_BACKUP_HOURS="${NEXO_BACKUP_RECENT_BACKUP_HOURS:-6}"
BACKUP_MAX_BYTES="${NEXO_BACKUP_MAX_BYTES:-53687091200}"
MIN_FREE_BYTES="${NEXO_BACKUP_MIN_FREE_BYTES:-5368709120}"
LOCAL_CONTEXT_MAX_BACKUP_BYTES="${NEXO_LOCAL_CONTEXT_MAX_BACKUP_BYTES:-2147483648}"

mkdir -p "$BACKUP_DIR" "$WEEKLY_DIR"

reconcile_memory_fabric_before_prune() {
    python3 - "$BACKUP_DIR" "$CORE_DIR" <<'PY' >/dev/null 2>&1 || true
from __future__ import annotations

import sys
from pathlib import Path

backup_dir = Path(sys.argv[1])
core_dir = Path(sys.argv[2])
for candidate in (core_dir, core_dir.parent / "src"):
    if candidate.exists():
        sys.path.insert(0, str(candidate))

try:
    import memory_fabric

    memory_fabric.reconcile_backup_diaries(
        backups_root=backup_dir,
        max_backup_files=80,
        limit=10000,
    )
except Exception:
    pass
PY
}

cleanup_backups() {
    reconcile_memory_fabric_before_prune

    PRUNER="$NEXO_HOME/core/scripts/prune_runtime_backups.py"
    if [ ! -f "$PRUNER" ]; then
        PRUNER="$(dirname "$0")/prune_runtime_backups.py"
    fi
    if [ -f "$PRUNER" ]; then
        python3 "$PRUNER" --root "$BACKUP_DIR" --apply --max-bytes "$BACKUP_MAX_BYTES" >/dev/null 2>&1 || true
    fi

    python3 - "$BACKUP_DIR" "$RETENTION_HOURS" "$KEEP_LAST" "$FAMILY_KEEP_LAST" "$LOCAL_CONTEXT_RETENTION_HOURS" "$LOCAL_CONTEXT_KEEP_LAST" <<'PY'
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

base = Path(sys.argv[1])
retention_hours = max(1, int(sys.argv[2]))
keep_last = max(1, int(sys.argv[3]))
family_keep_last = max(1, int(sys.argv[4]))
local_context_retention_hours = max(1, int(sys.argv[5]))
local_context_keep_last = max(1, int(sys.argv[6]))
now = time.time()

def delete_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"NEXO backup cleanup warning: {path}: {exc}", file=sys.stderr)

for tmp in base.glob("*.tmp.*"):
    try:
        if now - tmp.stat().st_mtime > 1800:
            delete_path(tmp)
    except FileNotFoundError:
        pass

hourlies = sorted(base.glob("nexo-*.db"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
for backup in hourlies[keep_last:]:
    try:
        age_hours = (now - backup.stat().st_mtime) / 3600
    except FileNotFoundError:
        continue
    if age_hours > retention_hours:
        delete_path(backup)

local_context_hourlies = sorted(
    base.glob("local-context-*.db"),
    key=lambda p: p.stat().st_mtime if p.exists() else 0,
    reverse=True,
)
for backup in local_context_hourlies[local_context_keep_last:]:
    try:
        age_hours = (now - backup.stat().st_mtime) / 3600
    except FileNotFoundError:
        continue
    if age_hours > local_context_retention_hours:
        delete_path(backup)

for pattern in (
    "pre-backfill-owner-*",
    "pre-update-*",
    "pre-autoupdate-*",
    "pre-restore-*",
    "app-reinstall-*",
    "app-install-*",
    "desktop-local-install-*",
    "code-tree-*",
):
    entries = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for entry in entries[family_keep_last:]:
        delete_path(entry)

weekly = base / "weekly"
if weekly.exists():
    for backup in weekly.glob("weekly-*.db"):
        try:
            if now - backup.stat().st_mtime > 90 * 24 * 3600:
                delete_path(backup)
        except FileNotFoundError:
            pass
PY
}

has_recent_backup() {
    find "$BACKUP_DIR" -maxdepth 1 -name "nexo-*.db" -mmin "-$((RECENT_BACKUP_HOURS * 60))" -print -quit | grep -q .
}

has_recent_local_context_backup() {
    find "$BACKUP_DIR" -maxdepth 1 -name "local-context-*.db" -mmin "-$((RECENT_BACKUP_HOURS * 60))" -print -quit | grep -q .
}

available_backup_bytes() {
    df -Pk "$BACKUP_DIR" 2>/dev/null | awk 'NR==2 { printf "%.0f\n", $4 * 1024 }'
}

file_size_bytes() {
    wc -c < "$1" 2>/dev/null | tr -d ' '
}

ensure_backup_space() {
    cleanup_backups
    avail="$(available_backup_bytes)"
    if [ -n "$avail" ] && [ "$avail" -lt "$MIN_FREE_BYTES" ]; then
        echo "NEXO backup skipped: free disk below safety floor after self-cleanup (${avail}B < ${MIN_FREE_BYTES}B)" >&2
        return 1
    fi
    return 0
}

if ! ensure_backup_space; then
    if has_recent_backup; then
        exit 0
    fi
    exit 1
fi

# Hourly backup
TIMESTAMP=$(date +%Y-%m-%d-%H%M)
BACKUP_FILE="$BACKUP_DIR/nexo-$TIMESTAMP.db"
TMP_BACKUP="$BACKUP_FILE.tmp.$$"
rm -f "$TMP_BACKUP"
if sqlite3 -cmd ".timeout $BUSY_TIMEOUT_MS" "$DB" <<SQL
PRAGMA busy_timeout=$BUSY_TIMEOUT_MS;
.backup '$TMP_BACKUP'
SQL
then
    mv "$TMP_BACKUP" "$BACKUP_FILE"
else
    rm -f "$TMP_BACKUP"
    if has_recent_backup; then
        echo "NEXO backup skipped: database busy and a recent backup exists" >&2
        cleanup_backups
        exit 0
    fi
    echo "NEXO backup failed: database busy or unavailable and no recent backup exists" >&2
    cleanup_backups
    exit 1
fi

# Weekly backup — save one per week (Sundays)
WEEK=$(date +%Y-W%V)
WEEKLY_FILE="$WEEKLY_DIR/weekly-$WEEK.db"
if [ ! -f "$WEEKLY_FILE" ] && [ "$(date +%u)" = "7" ] && [ -f "$BACKUP_FILE" ]; then
    cp "$BACKUP_FILE" "$WEEKLY_FILE"
fi

# Local memory backup: separate and aggressively rotated so the index cannot
# block core DB backups or fill the disk with duplicate multi-GB snapshots.
if [ -f "$LOCAL_CONTEXT_DB" ]; then
    LOCAL_CONTEXT_BACKUP_FILE="$BACKUP_DIR/local-context-$TIMESTAMP.db"
    LOCAL_CONTEXT_TMP_BACKUP="$LOCAL_CONTEXT_BACKUP_FILE.tmp.$$"
    rm -f "$LOCAL_CONTEXT_TMP_BACKUP"
    LOCAL_CONTEXT_SIZE="$(file_size_bytes "$LOCAL_CONTEXT_DB")"
    if [ -n "$LOCAL_CONTEXT_SIZE" ] && [ "$LOCAL_CONTEXT_SIZE" -gt "$LOCAL_CONTEXT_MAX_BACKUP_BYTES" ]; then
        echo "NEXO local memory backup skipped: local-context.db exceeds automatic backup cap (${LOCAL_CONTEXT_SIZE}B > ${LOCAL_CONTEXT_MAX_BACKUP_BYTES}B)"
    elif ! ensure_backup_space; then
        echo "NEXO local memory backup skipped: free disk below safety floor"
    elif [ -f "$LOCK_FILE" ] && find "$LOCK_FILE" -mmin -30 -print -quit | grep -q . && has_recent_local_context_backup; then
        echo "NEXO local memory backup skipped: index is active and a recent local backup exists"
    elif sqlite3 -cmd ".timeout $BUSY_TIMEOUT_MS" "$LOCAL_CONTEXT_DB" <<SQL
PRAGMA busy_timeout=$BUSY_TIMEOUT_MS;
.backup '$LOCAL_CONTEXT_TMP_BACKUP'
SQL
    then
        mv "$LOCAL_CONTEXT_TMP_BACKUP" "$LOCAL_CONTEXT_BACKUP_FILE"
    else
        rm -f "$LOCAL_CONTEXT_TMP_BACKUP"
        echo "NEXO local memory backup warning: local-context database busy or unavailable" >&2
    fi
fi

cleanup_backups
