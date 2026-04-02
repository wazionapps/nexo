from __future__ import annotations
"""NEXO Auto-Update — lightweight startup check for git updates and file-based migrations.

Called once per server startup. Respects a 1-hour cooldown to avoid redundant checks.
Never blocks startup for more than 5 seconds. Logs errors and continues on failure.

This is separate from plugins/update.py which handles MANUAL updates with rollback.
"""

import json
import os
import re
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
CLAUDE_MD_VERSION_FILE = DATA_DIR / "claude_md_version.txt"
MIGRATIONS_DIR = REPO_DIR / "migrations"
TEMPLATE_FILE = REPO_DIR / "templates" / "CLAUDE.md.template"

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


# ── Hook sync ────────────────────────────────────────────────────────

def _sync_hooks():
    """Copy hook scripts from src/hooks/ to NEXO_HOME/hooks/ after a git pull."""
    import shutil
    hooks_src = SRC_DIR / "hooks"
    hooks_dest = NEXO_HOME / "hooks"
    if not hooks_src.is_dir():
        return
    hooks_dest.mkdir(parents=True, exist_ok=True)
    synced = 0
    for f in hooks_src.iterdir():
        if f.is_file() and f.suffix == ".sh":
            dest = hooks_dest / f.name
            shutil.copy2(str(f), str(dest))
            os.chmod(str(dest), 0o755)
            synced += 1
    if synced:
        _log(f"Synced {synced} hook(s) to {hooks_dest}")


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

    # Sync hooks to NEXO_HOME (nexo-brain.js copies them on install,
    # but auto-update via git pull bypasses nexo-brain.js)
    _sync_hooks()

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


# ── CLAUDE.md version-tracked migration ─────────────────────────────


def _read_template_version() -> str | None:
    """Extract version from the <!-- nexo-claude-md-version: X.Y.Z --> comment in the template."""
    if not TEMPLATE_FILE.exists():
        return None
    first_line = TEMPLATE_FILE.read_text().split("\n", 1)[0]
    m = re.search(r"nexo-claude-md-version:\s*([\d.]+)", first_line)
    return m.group(1) if m else None


def _read_installed_claude_md_version() -> str | None:
    """Read the CLAUDE.md version currently installed for this user."""
    try:
        if CLAUDE_MD_VERSION_FILE.exists():
            return CLAUDE_MD_VERSION_FILE.read_text().strip()
    except OSError:
        pass
    return None


def _write_installed_claude_md_version(version: str):
    """Persist the installed CLAUDE.md version."""
    try:
        CLAUDE_MD_VERSION_FILE.write_text(version)
    except Exception as e:
        _log(f"Failed to write CLAUDE.md version file: {e}")


def _find_user_claude_md() -> Path | None:
    """Locate the user's CLAUDE.md (typically ~/.claude/CLAUDE.md)."""
    candidate = Path.home() / ".claude" / "CLAUDE.md"
    if candidate.exists():
        return candidate
    # Fallback: check NEXO_HOME
    candidate2 = NEXO_HOME / "CLAUDE.md"
    if candidate2.exists():
        return candidate2
    return None


def _resolve_placeholders(template_text: str) -> str:
    """Fill {{NAME}} and {{NEXO_HOME}} from the user's existing CLAUDE.md or config."""
    # Try to read operator name from version.json
    name = "NEXO"
    try:
        vf = NEXO_HOME / "version.json"
        if vf.exists():
            data = json.loads(vf.read_text())
            name = data.get("operator_name", name)
    except Exception:
        pass

    return (
        template_text
        .replace("{{NAME}}", name)
        .replace("{{NEXO_HOME}}", str(NEXO_HOME))
    )


def _extract_section(text: str, section_id: str) -> str | None:
    """Extract content between <!-- nexo:start:ID --> and <!-- nexo:end:ID --> markers (inclusive)."""
    pattern = re.compile(
        rf"(<!-- nexo:start:{re.escape(section_id)} -->.*?<!-- nexo:end:{re.escape(section_id)} -->)",
        re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1) if m else None


def _list_section_ids(text: str) -> list[str]:
    """Return all section IDs found in the text, in order."""
    return re.findall(r"<!-- nexo:start:(\w+) -->", text)


def _migrate_claude_md() -> str | None:
    """Compare template version vs installed version. If newer, update core sections in user's CLAUDE.md.

    Returns a status message or None if no migration was needed.
    """
    template_version = _read_template_version()
    if not template_version:
        return None

    installed_version = _read_installed_claude_md_version()
    if installed_version == template_version:
        return None  # Already up to date

    user_md_path = _find_user_claude_md()
    if not user_md_path:
        _log("CLAUDE.md migration: no user CLAUDE.md found, skipping")
        return None

    # Read both files
    user_md = user_md_path.read_text()
    template_raw = TEMPLATE_FILE.read_text()
    template_resolved = _resolve_placeholders(template_raw)

    # Get all section IDs from the template
    section_ids = _list_section_ids(template_resolved)
    if not section_ids:
        _log("CLAUDE.md migration: no section markers in template, skipping")
        _write_installed_claude_md_version(template_version)
        return None

    updated = user_md
    sections_replaced = 0
    sections_added = 0

    for sid in section_ids:
        new_section = _extract_section(template_resolved, sid)
        if not new_section:
            continue

        old_section = _extract_section(updated, sid)
        if old_section:
            if old_section != new_section:
                updated = updated.replace(old_section, new_section)
                sections_replaced += 1
        else:
            # Section doesn't exist in user's file — append before the end
            # (new sections added by template updates)
            updated = updated.rstrip() + "\n\n" + new_section + "\n"
            sections_added += 1

    # Update the version comment if present in user's file
    updated = re.sub(
        r"<!-- nexo-claude-md-version: [\d.]+ -->",
        f"<!-- nexo-claude-md-version: {template_version} -->",
        updated,
    )
    # If no version comment existed, add one at the top
    if "nexo-claude-md-version:" not in updated:
        updated = f"<!-- nexo-claude-md-version: {template_version} -->\n" + updated

    if sections_replaced > 0 or sections_added > 0:
        # Backup before writing
        backup_path = user_md_path.with_suffix(".md.bak")
        try:
            backup_path.write_text(user_md)
        except Exception:
            pass  # Non-critical

        user_md_path.write_text(updated)

    _write_installed_claude_md_version(template_version)

    if sections_replaced == 0 and sections_added == 0:
        return f"CLAUDE.md v{template_version}: already current (version file updated)"

    msg = f"CLAUDE.md migrated to v{template_version}: {sections_replaced} section(s) updated, {sections_added} new section(s) added"
    _log(msg)
    return msg


# ── Main entry point ─────────────────────────────────────────────────

def auto_update_check() -> dict:
    """Run the full auto-update check at server startup.

    NEVER raises an exception — always returns a dict.

    Phase 1 (local, safe, no network):
        - DB schema migrations
        - File-based migrations
        - CLAUDE.md version migration

    Phase 2 (network, wrapped in try/except):
        - git fetch/pull (if git repo)
        - npm version check (if non-git install)

    Returns a dict with:
        - checked: bool — whether a network check was actually performed
        - git_update: str|None — git update status message
        - npm_notice: str|None — npm upgrade notice for non-git installs
        - claude_md_update: str|None — CLAUDE.md migration status
        - migrations: list — file-based migration results
        - db_migrations: int — number of DB schema migrations applied
        - skipped_reason: str|None — why the network check was skipped (cooldown, etc.)
        - error: str|None — error message if something failed (informational only)
    """
    result = {
        "checked": False,
        "git_update": None,
        "npm_notice": None,
        "claude_md_update": None,
        "migrations": [],
        "db_migrations": 0,
        "skipped_reason": None,
        "error": None,
    }

    # ── Read auto_update flag from schedule.json ────────────────────
    auto_update_enabled = True
    try:
        schedule_file = NEXO_HOME / "config" / "schedule.json"
        if schedule_file.exists():
            schedule_data = json.loads(schedule_file.read_text())
            auto_update_enabled = schedule_data.get("auto_update", True)
    except Exception:
        pass  # Default to enabled on any read error

    # ── Phase 1: Local migrations (safe, no network) ────────────────
    # These ALWAYS run, regardless of cooldown, network state, or auto_update flag.

    # DB schema migrations
    try:
        _run_db_migrations()
    except Exception as e:
        _log(f"DB migration error (continuing): {e}")

    # File-based migrations
    try:
        result["migrations"] = run_file_migrations()
    except Exception as e:
        _log(f"File migration runner error: {e}")

    # Backfill evolution-objective.json for existing installs
    try:
        evo_obj_path = NEXO_HOME / "brain" / "evolution-objective.json"
        if not evo_obj_path.exists():
            (NEXO_HOME / "brain").mkdir(parents=True, exist_ok=True)
            default_objective = {
                "objective": "Improve operational excellence and reduce repeated errors",
                "focus_areas": ["error_prevention", "proactivity", "memory_quality"],
                "evolution_enabled": True,
                "evolution_mode": "review",
                "dimensions": {
                    "episodic_memory": {"current": 0, "target": 90},
                    "autonomy": {"current": 0, "target": 80},
                    "proactivity": {"current": 0, "target": 70},
                    "self_improvement": {"current": 0, "target": 60},
                    "agi": {"current": 0, "target": 20},
                },
                "total_evolutions": 0,
                "consecutive_failures": 0,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            evo_obj_path.write_text(json.dumps(default_objective, indent=2))
            _log("Backfilled evolution-objective.json for existing install")
    except Exception as e:
        _log(f"evolution-objective.json backfill error: {e}")

    # Backfill NEXO_HOME/scripts/ for existing installs
    try:
        scripts_dest = NEXO_HOME / "scripts"
        # Deduce NEXO_CODE: env var first, then from __file__ (auto_update.py is in src/)
        nexo_code = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
        scripts_src = nexo_code / "scripts" if (nexo_code / "scripts").is_dir() else None
        if scripts_src and not scripts_dest.is_dir():
            import shutil
            scripts_dest.mkdir(parents=True, exist_ok=True)
            for f in scripts_src.iterdir():
                if f.name.startswith('.') or f.name == '__pycache__':
                    continue
                dest = scripts_dest / f.name
                if f.is_file() and not dest.exists():
                    shutil.copy2(str(f), str(dest))
            _log("Backfilled NEXO_HOME/scripts/ from NEXO_CODE for existing install")
    except Exception as e:
        _log(f"scripts backfill error: {e}")

    # CLAUDE.md version migration
    try:
        result["claude_md_update"] = _migrate_claude_md()
    except Exception as e:
        _log(f"CLAUDE.md migration error: {e}")

    # ── Phase 2: Network operations (wrapped, never fatal) ──────────
    # Skip entirely if auto_update is disabled in schedule.json
    if not auto_update_enabled:
        result["skipped_reason"] = "auto_update disabled in schedule.json"
        _log("Network updates disabled (auto_update: false in schedule.json)")
        return result

    # Check cooldown for git/npm checks
    try:
        last_check = _read_last_check()
        last_ts = last_check.get("timestamp", 0)
        now = time.time()

        if now - last_ts < CHECK_COOLDOWN_SECONDS:
            result["skipped_reason"] = "cooldown"
            return result

        result["checked"] = True

        is_git = _is_git_repo()

        if is_git:
            result["git_update"] = _check_git_updates()
        else:
            # Non-git install — check npm for newer version
            version_json = REPO_DIR / "version.json"
            pkg_json = REPO_DIR / "package.json"
            if version_json.exists() or pkg_json.exists():
                result["npm_notice"] = _check_npm_version()

        # Save timestamp
        _write_last_check({
            "timestamp": now,
            "is_git": is_git,
            "git_update": result["git_update"],
            "npm_notice": result["npm_notice"],
        })

    except Exception as e:
        error_msg = f"Update check failed: {e}. Running current version."
        _log(error_msg)
        result["error"] = error_msg

    return result
