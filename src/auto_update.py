"""NEXO Auto-Update — lightweight startup check for git updates and file-based migrations.

Called once per server startup. Respects a 1-hour cooldown to avoid redundant checks.
Never blocks startup for more than 5 seconds. Logs errors and continues on failure.

This is separate from plugins/update.py which handles MANUAL updates with rollback.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DATA_DIR = NEXO_HOME / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Repo root: go up from src/
SRC_DIR = Path(__file__).resolve().parent
REPO_DIR = SRC_DIR.parent

LAST_CHECK_FILE = DATA_DIR / "auto_update_last_check.json"
MIGRATION_VERSION_FILE = DATA_DIR / "migration_version"
MIGRATIONS_DIR = REPO_DIR / "migrations"

CHECK_COOLDOWN_SECONDS = 3600  # 1 hour
GIT_TIMEOUT_SECONDS = 4  # stay well under the 5s total budget


def _log(msg: str):
    """Log to stderr with prefix."""
    print(f"[NEXO auto-update] {msg}", file=sys.stderr)


def _read_last_check() -> dict:
    """Read last check state from disk."""
    try:
        if LAST_CHECK_FILE.exists():
            return json.loads(LAST_CHECK_FILE.read_text())
    except Exception:
        pass
    return {}


def _write_last_check(data: dict):
    """Persist last check state."""
    try:
        LAST_CHECK_FILE.write_text(json.dumps(data))
    except Exception as e:
        _log(f"Failed to write last-check file: {e}")


def _is_git_repo() -> bool:
    """Check if REPO_DIR is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def _git(*args) -> tuple[int, str, str]:
    """Run a git command in REPO_DIR. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _read_package_version() -> str:
    """Read version from package.json."""
    try:
        pkg = REPO_DIR / "package.json"
        if pkg.exists():
            return json.loads(pkg.read_text()).get("version", "unknown")
    except Exception:
        pass
    return "unknown"


# ── Git-based auto-update ────────────────────────────────────────────

def _check_git_updates() -> str | None:
    """Fetch remote, compare HEAD, pull if behind. Returns status message or None."""
    # Fetch (allow it to fail silently on network issues)
    rc, _, fetch_err = _git("fetch", "--quiet")
    if rc != 0:
        _log(f"git fetch failed (network?): {fetch_err}")
        return None  # Can't check, skip silently

    # Compare local HEAD vs remote tracking branch
    rc, local_head, _ = _git("rev-parse", "HEAD")
    if rc != 0:
        return None
    rc, remote_head, _ = _git("rev-parse", "@{u}")
    if rc != 0:
        # No upstream configured — skip
        return None

    if local_head == remote_head:
        return None  # Already up to date

    # Check if we're behind (remote has commits we don't)
    rc, merge_base, _ = _git("merge-base", "HEAD", "@{u}")
    if rc != 0:
        return None

    if merge_base == remote_head:
        # Local is AHEAD — don't pull
        return None
    if merge_base != local_head and merge_base != remote_head:
        # Diverged — don't auto-pull, too risky
        _log("Local and remote have diverged. Skipping auto-pull.")
        return "diverged"

    # We're behind — safe to fast-forward pull
    old_version = _read_package_version()
    rc, pull_out, pull_err = _git("pull", "--ff-only")
    if rc != 0:
        _log(f"git pull --ff-only failed: {pull_err}")
        return None  # Don't break anything

    new_version = _read_package_version()

    # Run DB migrations after pull
    _run_db_migrations()

    msg = f"Auto-updated: {old_version} -> {new_version}" if old_version != new_version else f"Auto-updated (v{new_version}, new commits)"
    _log(msg)
    return msg


def _run_db_migrations():
    """Run NEXO's DB schema migrations (from db._schema) after a pull."""
    try:
        from db._schema import run_migrations
        from db._core import get_db
        conn = get_db()
        applied = run_migrations(conn)
        if applied > 0:
            _log(f"Applied {applied} DB migration(s)")
    except Exception as e:
        _log(f"DB migration error (continuing): {e}")


# ── npm version check (notify only) ─────────────────────────────────

def _check_npm_version() -> str | None:
    """For non-git installs: check npm registry for a newer version. Returns notification or None."""
    current = _read_package_version()
    if current == "unknown":
        return None

    pkg_name = "nexo-brain"
    try:
        pkg = REPO_DIR / "package.json"
        if pkg.exists():
            data = json.loads(pkg.read_text())
            pkg_name = data.get("name", pkg_name)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["npm", "view", pkg_name, "version"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        latest = result.stdout.strip()
        if not latest:
            return None
        if latest != current and not current.endswith(latest):
            return f"NEXO update available: {current} -> {latest}. Run: npm update -g {pkg_name}"
    except Exception:
        pass
    return None


# ── File-based migrations (migrations/ directory) ────────────────────

def _get_applied_migration_version() -> int:
    """Read the last applied file-migration version from disk."""
    try:
        if MIGRATION_VERSION_FILE.exists():
            return int(MIGRATION_VERSION_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def _set_migration_version(version: int):
    """Write the current file-migration version to disk."""
    try:
        MIGRATION_VERSION_FILE.write_text(str(version))
    except Exception as e:
        _log(f"Failed to write migration version: {e}")


def _discover_migrations() -> list[tuple[int, Path]]:
    """Find numbered migration files in migrations/ directory.

    Expected naming: NNN_description.ext where ext is .sql, .py, or .sh
    Example: 001_add_index.sql, 002_backfill_data.py, 003_cleanup.sh
    """
    if not MIGRATIONS_DIR.is_dir():
        return []

    migrations = []
    for f in MIGRATIONS_DIR.iterdir():
        if f.is_file() and f.suffix in (".sql", ".py", ".sh"):
            # Extract leading number from filename
            parts = f.stem.split("_", 1)
            if parts and parts[0].isdigit():
                migrations.append((int(parts[0]), f))

    migrations.sort(key=lambda x: x[0])
    return migrations


def _run_file_migration(path: Path) -> tuple[bool, str]:
    """Execute a single migration file. Returns (success, message)."""
    ext = path.suffix

    try:
        if ext == ".sql":
            sql = path.read_text()
            from db._core import get_db
            conn = get_db()
            conn.executescript(sql)
            conn.commit()
            return True, "OK"

        elif ext == ".py":
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(SRC_DIR),
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "NEXO_HOME": str(NEXO_HOME)},
            )
            if result.returncode != 0:
                return False, result.stderr or result.stdout or "non-zero exit"
            return True, "OK"

        elif ext == ".sh":
            result = subprocess.run(
                ["bash", str(path)],
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "NEXO_HOME": str(NEXO_HOME)},
            )
            if result.returncode != 0:
                return False, result.stderr or result.stdout or "non-zero exit"
            return True, "OK"

        else:
            return False, f"unknown extension: {ext}"

    except Exception as e:
        return False, str(e)


def run_file_migrations() -> list[dict]:
    """Run any pending file-based migrations from the migrations/ directory.

    Returns list of results: [{"version": N, "file": "...", "status": "ok"|"failed", "message": "..."}]
    """
    current_version = _get_applied_migration_version()
    migrations = _discover_migrations()
    results = []

    for version, path in migrations:
        if version <= current_version:
            continue

        success, message = _run_file_migration(path)

        if success:
            _set_migration_version(version)
            results.append({
                "version": version,
                "file": path.name,
                "status": "ok",
                "message": message,
            })
            _log(f"Migration {path.name}: OK")
        else:
            results.append({
                "version": version,
                "file": path.name,
                "status": "failed",
                "message": message,
            })
            _log(f"Migration {path.name}: FAILED — {message}")
            # Don't advance version past a failure, but continue trying others
            # so independent migrations still run. Version stays at last success.

    return results


# ── Main entry point ─────────────────────────────────────────────────

def auto_update_check() -> dict:
    """Run the full auto-update check at server startup.

    Returns a dict with:
        - checked: bool — whether a check was actually performed
        - git_update: str|None — git update status message
        - npm_notice: str|None — npm upgrade notice for non-git installs
        - migrations: list — file-based migration results
        - skipped_reason: str|None — why the check was skipped (cooldown, etc.)
    """
    result = {
        "checked": False,
        "git_update": None,
        "npm_notice": None,
        "migrations": [],
        "skipped_reason": None,
    }

    # Always run pending file-based migrations regardless of cooldown
    try:
        result["migrations"] = run_file_migrations()
    except Exception as e:
        _log(f"File migration runner error: {e}")

    # Check cooldown for git/npm checks
    last_check = _read_last_check()
    last_ts = last_check.get("timestamp", 0)
    now = time.time()

    if now - last_ts < CHECK_COOLDOWN_SECONDS:
        result["skipped_reason"] = "cooldown"
        return result

    result["checked"] = True

    is_git = _is_git_repo()

    if is_git:
        try:
            result["git_update"] = _check_git_updates()
        except Exception as e:
            _log(f"Git update check error: {e}")
    else:
        # Non-git install — check npm for newer version
        version_json = REPO_DIR / "version.json"
        pkg_json = REPO_DIR / "package.json"
        if version_json.exists() or pkg_json.exists():
            try:
                result["npm_notice"] = _check_npm_version()
            except Exception as e:
                _log(f"npm version check error: {e}")

    # Save timestamp
    _write_last_check({
        "timestamp": now,
        "is_git": is_git,
        "git_update": result["git_update"],
        "npm_notice": result["npm_notice"],
    })

    return result
