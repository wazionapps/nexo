"""Backup plugin — hourly SQLite backups with 7-day retention."""
import os
import shutil
import time
import glob
from db import get_db

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
DB_PATH = os.path.join(NEXO_HOME, "data", "nexo.db")
BACKUP_DIR = os.path.join(NEXO_HOME, "backups")

RETENTION_DAYS = 7


def handle_backup_now() -> str:
    """Create an immediate backup of the NEXO database."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d-%H%M")
    dest = os.path.join(BACKUP_DIR, f"nexo-{timestamp}.db")

    # Use SQLite backup API for consistency
    import sqlite3
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
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

    Args:
        filename: Backup filename (e.g., 'nexo-2026-03-11-1200.db')
    """
    src = os.path.join(BACKUP_DIR, filename)
    if not os.path.isfile(src):
        return f"Backup not found: {filename}"

    # Create safety backup first
    safety = os.path.join(BACKUP_DIR, f"nexo-pre-restore-{time.strftime('%Y%m%d%H%M%S')}.db")
    import sqlite3
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(safety)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # Restore
    restore_conn = sqlite3.connect(src)
    target_conn = sqlite3.connect(DB_PATH)
    restore_conn.backup(target_conn)
    target_conn.close()
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
    """Remove backups older than RETENTION_DAYS."""
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = time.time() - (RETENTION_DAYS * 86400)
    for f in glob.glob(os.path.join(BACKUP_DIR, "nexo-*.db")):
        if os.path.getmtime(f) < cutoff:
            os.remove(f)


TOOLS = [
    (handle_backup_now, "nexo_backup_now", "Create an immediate backup of the NEXO database"),
    (handle_backup_list, "nexo_backup_list", "List available backups with dates and sizes"),
    (handle_backup_restore, "nexo_backup_restore", "Restore database from a backup (DESTRUCTIVE)"),
]
