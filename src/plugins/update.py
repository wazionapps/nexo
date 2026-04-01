"""Update plugin — pull latest code, backup DBs, run migrations, verify."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Repo root: go up from src/plugins/ -> src/ -> repo/
_THIS_DIR = Path(__file__).resolve().parent
REPO_DIR = _THIS_DIR.parent.parent
PACKAGE_JSON = REPO_DIR / "package.json"
SRC_DIR = REPO_DIR / "src"

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DATA_DIR = NEXO_HOME / "data"
BACKUP_BASE = NEXO_HOME / "backups"


def _read_version() -> str:
    """Read version from package.json."""
    try:
        return json.loads(PACKAGE_JSON.read_text()).get("version", "unknown")
    except Exception:
        return "unknown"


def _git(*args, cwd=None) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=cwd or str(REPO_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _check_dirty() -> str | None:
    """Return error message if src/ has uncommitted changes, else None."""
    rc, out, _ = _git("status", "--porcelain", "--", "src/")
    if rc != 0:
        return "Failed to check git status."
    if out:
        return f"Uncommitted changes in src/:\n{out}\nCommit or stash before updating."
    return None


def _backup_databases() -> tuple[str, str | None]:
    """Backup all .db files from NEXO_HOME/data/. Returns (backup_dir, error)."""
    timestamp = time.strftime("%Y-%m-%d-%H%M")
    backup_dir = BACKUP_BASE / f"pre-update-{timestamp}"

    db_files = list(DATA_DIR.glob("*.db")) if DATA_DIR.is_dir() else []
    # Also check NEXO_HOME root for legacy db location
    db_files += [f for f in NEXO_HOME.glob("*.db") if f.is_file()]
    # And check src/ dir for nexo.db (dev mode)
    src_db = SRC_DIR / "nexo.db"
    if src_db.is_file() and src_db not in db_files:
        db_files.append(src_db)

    if not db_files:
        return str(backup_dir), None  # No DBs to backup, not an error

    backup_dir.mkdir(parents=True, exist_ok=True)

    for db_file in db_files:
        dest = backup_dir / db_file.name
        try:
            src_conn = sqlite3.connect(str(db_file))
            dst_conn = sqlite3.connect(str(dest))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except Exception as e:
            return str(backup_dir), f"Failed to backup {db_file.name}: {e}"

    return str(backup_dir), None


def _restore_databases(backup_dir: str):
    """Restore .db files from a backup directory."""
    bdir = Path(backup_dir)
    if not bdir.is_dir():
        return
    for db_backup in bdir.glob("*.db"):
        # Try to find original location
        for candidate in [DATA_DIR / db_backup.name, NEXO_HOME / db_backup.name, SRC_DIR / db_backup.name]:
            if candidate.is_file():
                try:
                    src_conn = sqlite3.connect(str(db_backup))
                    dst_conn = sqlite3.connect(str(candidate))
                    src_conn.backup(dst_conn)
                    dst_conn.close()
                    src_conn.close()
                except Exception:
                    pass
                break


def _run_migrations() -> str | None:
    """Run init_db() to apply pending migrations. Returns error or None."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import db; db.init_db()"],
            cwd=str(SRC_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return f"Migration failed: {result.stderr or result.stdout}"
    except Exception as e:
        return f"Migration error: {e}"
    return None


def _verify_import() -> str | None:
    """Verify server.py can be imported successfully."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(SRC_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return f"Import verification failed: {result.stderr or result.stdout}"
    except Exception as e:
        return f"Import verification error: {e}"
    return None


def handle_update(remote: str = "origin", branch: str = "main") -> str:
    """Pull latest NEXO code, backup databases, run migrations, and verify.

    Full update flow:
    1. Check for uncommitted changes in src/
    2. Backup all .db files
    3. git pull
    4. Run migrations if version changed
    5. Verify server.py imports
    6. Rollback on failure

    Args:
        remote: Git remote name (default: origin)
        branch: Git branch to pull (default: main)
    """
    steps_done = []
    old_commit = None
    backup_dir = None

    try:
        # Step 1: Check dirty
        dirty_err = _check_dirty()
        if dirty_err:
            return f"ABORTED: {dirty_err}"
        steps_done.append("clean-check")

        # Record current state
        old_version = _read_version()
        rc, old_commit, _ = _git("rev-parse", "HEAD")
        if rc != 0:
            return "ABORTED: Not a git repository or git not available."

        # Step 2: Backup databases
        backup_dir, backup_err = _backup_databases()
        if backup_err:
            return f"ABORTED at backup: {backup_err}"
        steps_done.append("backup")

        # Step 3: git pull
        rc, pull_out, pull_err = _git("pull", remote, branch)
        if rc != 0:
            return f"ABORTED at git pull: {pull_err or pull_out}"
        steps_done.append("git-pull")

        # Step 4: Check version change
        new_version = _read_version()
        version_changed = old_version != new_version

        # Step 5: Run migrations if version changed
        if version_changed:
            mig_err = _run_migrations()
            if mig_err:
                raise RuntimeError(f"Migration failed: {mig_err}")
            steps_done.append("migrations")

        # Step 6: Verify import
        verify_err = _verify_import()
        if verify_err:
            raise RuntimeError(f"Verification failed: {verify_err}")
        steps_done.append("verify")

        # Step 7: Sync crons with manifest
        cron_sync_result = ""
        try:
            cron_sync_path = NEXO_CODE / "crons" / "sync.py"
            if cron_sync_path.exists():
                import subprocess as _sp
                r = _sp.run(
                    [sys.executable, str(cron_sync_path)],
                    capture_output=True, text=True, timeout=30,
                    env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_CODE)},
                )
                cron_sync_result = r.stdout.strip()
                steps_done.append("cron-sync")
        except Exception as e:
            cron_sync_result = f"Cron sync warning: {e}"

        # Build result
        if pull_out == "Already up to date.":
            return f"Already up to date (v{old_version}). No changes pulled."

        lines = ["UPDATE SUCCESSFUL"]
        if version_changed:
            lines.append(f"  Version: {old_version} -> {new_version}")
        else:
            lines.append(f"  Version: {old_version} (unchanged)")
        lines.append(f"  Branch: {remote}/{branch}")
        lines.append(f"  Backup: {backup_dir}")
        if version_changed:
            lines.append("  Migrations: applied")
        if "cron-sync" in steps_done:
            lines.append("  Crons: synced with manifest")
        lines.append("")
        lines.append("MCP server restart needed to load new code.")
        return "\n".join(lines)

    except Exception as e:
        # Rollback
        rollback_lines = [f"UPDATE FAILED: {e}", "", "Rolling back..."]

        if old_commit and "git-pull" in steps_done:
            rc, _, err = _git("reset", "--hard", old_commit)
            if rc == 0:
                rollback_lines.append(f"  Git: reset to {old_commit[:8]}")
            else:
                rollback_lines.append(f"  Git rollback FAILED: {err}")

        if backup_dir and "backup" in steps_done:
            _restore_databases(backup_dir)
            rollback_lines.append(f"  DBs: restored from {backup_dir}")

        rollback_lines.append("")
        rollback_lines.append("System restored to previous state.")
        return "\n".join(rollback_lines)


TOOLS = [
    (handle_update, "nexo_update", "Pull latest NEXO code, backup DBs, run migrations, verify. Rolls back on failure."),
]
