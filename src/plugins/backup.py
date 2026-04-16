"""Backup plugin — hourly SQLite backups with 7-day retention.

v5.5.6: all three tools are rate-limited in-process so that a runaway MCP
client (tool-use loop in Claude Code, buggy Desktop handler, etc.) cannot
hammer ``sqlite3.Connection.backup()`` hundreds of times in minutes. The
v5.5.4 incident where an external loop caused ~8.5 GB of file-backed writes
in 37 minutes and corrupted nexo.db when the OS finally killed the process
is the exact scenario this limit prevents at the tool boundary — in addition
to the v5.5.5 self-heal that recovers from that class of wipe.
"""
import glob
import os
import shutil
import sqlite3
import threading
import time

from db import get_db

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
DB_PATH = os.path.join(NEXO_HOME, "data", "nexo.db")
BACKUP_DIR = os.path.join(NEXO_HOME, "backups")

RETENTION_DAYS = 7

# ── Rate limits (v5.5.6) ────────────────────────────────────────────
# Minimum seconds between successive calls to each destructive/expensive
# backup tool. Overridable per-tool via env var for tests or deliberate
# recovery scenarios (NEXO_BACKUP_MIN_INTERVAL_SECS, etc.).
BACKUP_NOW_MIN_INTERVAL_SECS = int(
    os.environ.get("NEXO_BACKUP_MIN_INTERVAL_SECS", "30")
)
BACKUP_RESTORE_MIN_INTERVAL_SECS = int(
    os.environ.get("NEXO_BACKUP_RESTORE_MIN_INTERVAL_SECS", "60")
)

_rate_limit_lock = threading.Lock()
_last_call_ts: dict[str, float] = {
    "backup_now": 0.0,
    "backup_restore": 0.0,
}


def _check_rate_limit(tool: str, min_interval: int) -> str | None:
    """Return a rate-limit error string if the tool is called too soon, else None."""
    now = time.time()
    with _rate_limit_lock:
        last = _last_call_ts.get(tool, 0.0)
        elapsed = now - last
        if last > 0 and elapsed < min_interval:
            remaining = int(min_interval - elapsed)
            return (
                f"Rate-limited: {tool} called {int(elapsed)}s ago "
                f"(min {min_interval}s between calls). Wait {remaining}s. "
                "If you are seeing this message repeatedly, a client may be stuck in a "
                "tool-use loop — check NEXO transcripts and kill the runaway session."
            )
        _last_call_ts[tool] = now
    return None


def _reset_rate_limit_state_for_tests() -> None:
    """Test hook: clear all tracked call timestamps."""
    with _rate_limit_lock:
        for key in _last_call_ts:
            _last_call_ts[key] = 0.0


def handle_backup_now() -> str:
    """Create an immediate backup of the NEXO database.

    Rate-limited to one call every BACKUP_NOW_MIN_INTERVAL_SECS (default 30 s).
    """
    err = _check_rate_limit("backup_now", BACKUP_NOW_MIN_INTERVAL_SECS)
    if err is not None:
        return err

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d-%H%M")
    dest = os.path.join(BACKUP_DIR, f"nexo-{timestamp}.db")

    # Use SQLite backup API for consistency
    src_conn = sqlite3.connect(DB_PATH)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    size_kb = os.path.getsize(dest) / 1024
    _cleanup_old()
    return f"Backup created: {os.path.basename(dest)} ({size_kb:.0f} KB)"


def handle_backup_list() -> str:
    """List available backups with dates and sizes."""
    if not os.path.isdir(BACKUP_DIR):
        return "No backups."
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "nexo-*.db")), reverse=True)
    if not files:
        return "No backups."
    lines = [f"BACKUPS ({len(files)}):"]
    total_size = 0
    for f in files:
        size = os.path.getsize(f) / 1024
        total_size += size
        name = os.path.basename(f)
        lines.append(f"  {name} ({size:.0f} KB)")
    lines.append(f"\n  Total: {total_size/1024:.1f} MB")
    return "\n".join(lines)


def handle_backup_restore(filename: str) -> str:
    """Restore database from a backup file. DESTRUCTIVE — replaces current DB.

    Rate-limited to one call every BACKUP_RESTORE_MIN_INTERVAL_SECS (default
    60 s). A client hammering restore in a loop is the exact shape of the
    v5.5.4 incident.

    Args:
        filename: Backup filename (e.g., 'nexo-2026-03-11-1200.db')
    """
    err = _check_rate_limit("backup_restore", BACKUP_RESTORE_MIN_INTERVAL_SECS)
    if err is not None:
        return err

    src = os.path.join(BACKUP_DIR, filename)
    if not os.path.isfile(src):
        return f"Backup not found: {filename}"

    # Create safety backup first
    safety = os.path.join(BACKUP_DIR, f"nexo-pre-restore-{time.strftime('%Y%m%d%H%M%S')}.db")
    src_conn = sqlite3.connect(DB_PATH)
    try:
        dst_conn = sqlite3.connect(safety)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # Restore
    restore_conn = sqlite3.connect(src)
    try:
        target_conn = sqlite3.connect(DB_PATH)
        try:
            restore_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        restore_conn.close()

    # Invalidate shared connection so db.py reconnects to restored data
    import db
    if db._shared_conn is not None:
        try:
            db._shared_conn.close()
        except Exception:
            pass
        db._shared_conn = None

    return f"DB restaurada desde {filename}. Safety backup: {os.path.basename(safety)}"


def _cleanup_old():
    """Remove backups older than RETENTION_DAYS.

    Covers both the hourly `nexo-YYYY-MM-DD-HHMM.db` snapshots and the
    `nexo-pre-restore-*.db` safety snapshots created by handle_backup_restore.
    Failures are swallowed — housekeeping must never interrupt the caller.
    """
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = time.time() - (RETENTION_DAYS * 86400)
    # glob `nexo-*.db` matches both the hourly pattern and pre-restore
    # snapshots, so a single loop prunes both with a single pass.
    for f in glob.glob(os.path.join(BACKUP_DIR, "nexo-*.db")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except OSError:
            # Permission / concurrent removal — skip silently.
            pass


TOOLS = [
    (handle_backup_now, "nexo_backup_now", "Create an immediate backup of the NEXO database"),
    (handle_backup_list, "nexo_backup_list", "List available backups with dates and sizes"),
    (handle_backup_restore, "nexo_backup_restore", "Restore database from a backup (DESTRUCTIVE)"),
]
