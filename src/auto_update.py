from __future__ import annotations
"""NEXO Auto-Update — lightweight startup check for git updates and file-based migrations.

Called once per server startup. Respects a 1-hour cooldown to avoid redundant checks.
Never blocks startup for more than 5 seconds. Logs errors and continues on failure.

This is separate from plugins/update.py which handles MANUAL updates with rollback.
"""

import json
import hashlib
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
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(SRC_DIR)))
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


def _sync_watchdog_hash_registry():
    """Keep the immutable-hash registry aligned with the installed watchdog script."""
    try:
        watchdog_file = NEXO_HOME / "scripts" / "nexo-watchdog.sh"
        if not watchdog_file.exists():
            return
        registry_file = NEXO_HOME / "scripts" / ".watchdog-hashes"
        entries: dict[str, str] = {}
        if registry_file.exists():
            for line in registry_file.read_text().splitlines():
                if "|" not in line:
                    continue
                filepath, expected = line.split("|", 1)
                if filepath:
                    entries[filepath] = expected
        actual_hash = hashlib.sha256(watchdog_file.read_bytes()).hexdigest()
        entries[str(watchdog_file)] = actual_hash
        registry_file.write_text(
            "\n".join(f"{filepath}|{digest}" for filepath, digest in sorted(entries.items())) + "\n"
        )
    except Exception as e:
        _log(f"watchdog hash registry sync error: {e}")


def _warn_protected_runtime_location():
    """Log a targeted macOS TCC warning for risky NEXO_HOME locations."""
    if sys.platform != "darwin":
        return
    try:
        home = Path.home()
        resolved = NEXO_HOME.resolve(strict=False)
        protected_roots = (
            home / "Documents",
            home / "Desktop",
            home / "Downloads",
            home / "Library" / "Mobile Documents",
        )
        if any(resolved == root or root in resolved.parents for root in protected_roots):
            _log(
                "NEXO_HOME is inside a macOS protected folder. Background jobs may need Full Disk Access "
                "for /bin/bash and the NEXO Python runtime, or NEXO_HOME should be moved outside "
                "Documents/Desktop/Downloads/iCloud Drive."
            )
    except Exception as e:
        _log(f"protected runtime warning skipped: {e}")


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


def _runtime_cli_wrapper_text() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f'NEXO_HOME="{NEXO_HOME}"\n'
        'PYTHON="$NEXO_HOME/.venv/bin/python3"\n'
        'if [ ! -x "$PYTHON" ]; then\n'
        '  if command -v python3 >/dev/null 2>&1; then\n'
        '    PYTHON="python3"\n'
        "  else\n"
        '    PYTHON="python"\n'
        "  fi\n"
        "fi\n"
        'export NEXO_HOME\n'
        'export NEXO_CODE="$NEXO_HOME"\n'
        'exec "$PYTHON" "$NEXO_HOME/cli.py" "$@"\n'
    )


def _shell_rc_files() -> list[Path]:
    shell = os.environ.get("SHELL", "/bin/bash")
    home_dir = Path.home()
    if "zsh" in shell:
        return [home_dir / ".zshrc"]
    return [home_dir / ".bash_profile", home_dir / ".bashrc"]


def _ensure_runtime_cli_in_shell():
    path_line = f'export PATH="{NEXO_HOME / "bin"}:$PATH"'
    comment = "# NEXO runtime CLI"
    for rc_file in _shell_rc_files():
        try:
            content = rc_file.read_text() if rc_file.exists() else ""
            if path_line not in content:
                with rc_file.open("a") as fh:
                    fh.write(f"\n{comment}\n{path_line}\n")
                _log(f"Backfilled runtime CLI PATH in {rc_file.name}")
        except Exception as e:
            _log(f"Shell PATH backfill error for {rc_file.name}: {e}")


def _ensure_runtime_cli_wrapper():
    try:
        bin_dir = NEXO_HOME / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / "nexo"
        content = _runtime_cli_wrapper_text()
        if not wrapper.exists() or wrapper.read_text() != content:
            wrapper.write_text(content)
            wrapper.chmod(0o755)
            _log("Backfilled runtime CLI wrapper")
    except Exception as e:
        _log(f"Runtime CLI wrapper backfill error: {e}")


# ── Hook sync ────────────────────────────────────────────────────────

def _requirements_hash() -> str:
    """Return a content hash of requirements.txt, or empty string if missing."""
    import hashlib
    req_file = SRC_DIR / "requirements.txt"
    if req_file.exists():
        return hashlib.sha256(req_file.read_bytes()).hexdigest()
    return ""


def _reinstall_pip_deps() -> bool:
    """Reinstall Python deps from requirements.txt. Returns True on success."""
    req_file = SRC_DIR / "requirements.txt"
    if not req_file.exists():
        return True
    venv_pip = NEXO_HOME / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        venv_pip = NEXO_HOME / ".venv" / "bin" / "pip3"
    try:
        if venv_pip.exists():
            result = subprocess.run(
                [str(venv_pip), "install", "--quiet", "-r", str(req_file)],
                capture_output=True, text=True, timeout=120,
            )
        else:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req_file), "--break-system-packages"],
                capture_output=True, text=True, timeout=120,
            )
        if result.returncode != 0:
            _log(f"pip install failed (exit {result.returncode}): {result.stderr or result.stdout}")
            return False
        _log("Reinstalled Python dependencies after update")
        return True
    except Exception as e:
        _log(f"pip reinstall failed: {e}")
        return False


def _refresh_installed_manifest():
    """Copy source crons/ to NEXO_HOME/crons/ so catchup & watchdog stay current."""
    try:
        import shutil
        src_crons = SRC_DIR / "crons"
        dst_crons = NEXO_HOME / "crons"
        if src_crons.exists():
            dst_crons.mkdir(parents=True, exist_ok=True)
            for f in src_crons.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(dst_crons / f.name))
            _log("Refreshed installed crons manifest")
    except Exception as e:
        _log(f"Manifest refresh warning: {e}")


def _cleanup_retired_runtime_files():
    """Remove retired core files that should not survive updates."""
    retired = [
        NEXO_HOME / "scripts" / "nexo-day-orchestrator.sh",
    ]
    for target in retired:
        try:
            if target.exists():
                if target.is_dir():
                    import shutil
                    shutil.rmtree(target)
                else:
                    target.unlink()
                _log(f"Removed retired runtime file: {target.name}")
        except Exception as e:
            _log(f"Retired runtime cleanup warning ({target.name}): {e}")


def _sync_crons():
    """Sync cron definitions with manifest after a git pull."""
    try:
        cron_sync_path = SRC_DIR / "crons" / "sync.py"
        if cron_sync_path.exists():
            result = subprocess.run(
                [sys.executable, str(cron_sync_path)],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(SRC_DIR)},
            )
            if result.returncode != 0:
                _log(f"Cron sync failed (exit {result.returncode}): {result.stderr or result.stdout}")
                return  # Don't refresh manifest if timers weren't actually updated
            _log("Synced cron definitions with manifest")
        _cleanup_retired_runtime_files()
        # Refresh the installed manifest only after successful sync
        _refresh_installed_manifest()
    except Exception as e:
        _log(f"Cron sync warning: {e}")


def _backup_dbs() -> str | None:
    """Snapshot all .db files before migration. Returns backup dir or None."""
    import sqlite3
    import time as _time
    timestamp = _time.strftime("%Y-%m-%d-%H%M%S")
    backup_dir = NEXO_HOME / "backups" / f"pre-autoupdate-{timestamp}"

    db_files = list(DATA_DIR.glob("*.db")) if DATA_DIR.is_dir() else []
    db_files += [f for f in NEXO_HOME.glob("*.db") if f.is_file()]
    src_db = SRC_DIR / "nexo.db"
    if src_db.is_file() and src_db not in db_files:
        db_files.append(src_db)

    if not db_files:
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    for db_file in db_files:
        try:
            src_conn = sqlite3.connect(str(db_file))
            dst_conn = sqlite3.connect(str(backup_dir / db_file.name))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()
        except Exception as e:
            _log(f"DB backup warning ({db_file.name}): {e}")
    return str(backup_dir)


def _restore_dbs(backup_dir: str):
    """Restore .db files from a backup directory."""
    import sqlite3
    bdir = Path(backup_dir)
    if not bdir.is_dir():
        return
    for db_backup in bdir.glob("*.db"):
        for candidate in [DATA_DIR / db_backup.name, NEXO_HOME / db_backup.name, SRC_DIR / db_backup.name]:
            if candidate.is_file():
                try:
                    src_conn = sqlite3.connect(str(db_backup))
                    dst_conn = sqlite3.connect(str(candidate))
                    src_conn.backup(dst_conn)
                    dst_conn.close()
                    src_conn.close()
                    _log(f"Restored DB: {db_backup.name}")
                except Exception as e:
                    _log(f"DB restore warning ({db_backup.name}): {e}")
                break


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
    old_req_hash = _requirements_hash()

    # Save old HEAD for rollback
    rc, old_head, _ = _git("rev-parse", "HEAD")
    if rc != 0:
        return None

    rc, pull_out, pull_err = _git("pull", "--ff-only")
    if rc != 0:
        _log(f"git pull --ff-only failed: {pull_err}")
        return None  # Don't break anything

    new_version = _read_package_version()
    new_req_hash = _requirements_hash()

    # Backup databases before any changes that might run migrations
    db_backup_dir = _backup_dbs()

    # Reinstall pip deps if requirements.txt content changed (not just version)
    if old_req_hash != new_req_hash:
        if not _reinstall_pip_deps():
            # pip failed — rollback git + DBs to old HEAD
            _log("pip install failed after pull, rolling back git...")
            _git("reset", "--hard", old_head)
            _reinstall_pip_deps()  # restore old deps (best-effort)
            if db_backup_dir:
                _restore_dbs(db_backup_dir)
            return None

    # Verify the new code can be imported before proceeding
    if not _verify_import():
        _log("Import verification failed after pull, rolling back git...")
        _git("reset", "--hard", old_head)
        if old_req_hash != new_req_hash:
            _reinstall_pip_deps()  # restore old deps (best-effort)
        if db_backup_dir:
            _restore_dbs(db_backup_dir)
        return None

    # Run DB migrations after pull — rollback if they fail
    if not _run_db_migrations():
        _log("DB migration failed after pull, rolling back git + DB...")
        _git("reset", "--hard", old_head)
        if old_req_hash != new_req_hash:
            _reinstall_pip_deps()
        if db_backup_dir:
            _restore_dbs(db_backup_dir)
        return None

    # Sync hooks to NEXO_HOME (nexo-brain.js copies them on install,
    # but auto-update via git pull bypasses nexo-brain.js)
    _sync_hooks()

    # Sync cron definitions with manifest
    _sync_crons()

    msg = f"Auto-updated: {old_version} -> {new_version}" if old_version != new_version else f"Auto-updated (v{new_version}, new commits)"
    _log(msg)
    return msg


def _verify_import() -> bool:
    """Verify that the new code can be imported. Returns True on success."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(SRC_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            _log(f"Import verification failed: {result.stderr or result.stdout}")
            return False
        return True
    except Exception as e:
        _log(f"Import verification error: {e}")
        return False


def _run_db_migrations() -> bool:
    """Run NEXO's DB schema migrations (from db._schema) after a pull.
    Returns True on success, False on failure."""
    try:
        from db._schema import run_migrations
        from db._core import get_db
        conn = get_db()
        applied = run_migrations(conn)
        if applied > 0:
            _log(f"Applied {applied} DB migration(s)")
        return True
    except Exception as e:
        _log(f"DB migration error: {e}")
        return False


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
            try:
                from user_context import get_context
                _name = get_context().assistant_name
            except Exception:
                _name = "NEXO"
            return f"{_name} update available: {current} -> {latest}. Run: npm update -g {pkg_name}"
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
    # Read operator name from calibration/version
    try:
        from user_context import get_context
        name = get_context().assistant_name
    except Exception:
        name = "NEXO"

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
        from evolution_cycle import normalize_objective
        if not evo_obj_path.exists():
            (NEXO_HOME / "brain").mkdir(parents=True, exist_ok=True)
            default_objective = {
                "objective": "Improve operational excellence and reduce repeated errors",
                "focus_areas": ["error_prevention", "proactivity", "memory_quality"],
                "evolution_enabled": True,
                "evolution_mode": "auto",
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
        else:
            raw_objective = json.loads(evo_obj_path.read_text())
            normalized = normalize_objective(raw_objective)
            if normalized != raw_objective:
                evo_obj_path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False))
                _log("Normalized legacy evolution-objective.json")
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

    _sync_watchdog_hash_registry()
    _warn_protected_runtime_location()

    # Backfill runtime CLI modules for existing installs
    try:
        for fname in ("cli.py", "script_registry.py", "skills_runtime.py", "cron_recovery.py", "client_preferences.py", "agent_runner.py"):
            src_file = SRC_DIR / fname
            dest_file = NEXO_HOME / fname
            if src_file.is_file() and (not dest_file.exists() or src_file.stat().st_mtime > dest_file.stat().st_mtime):
                import shutil
                shutil.copy2(str(src_file), str(dest_file))
                _log(f"Backfilled {fname}")
    except Exception as e:
        _log(f"CLI backfill error: {e}")

    _ensure_runtime_cli_wrapper()
    _ensure_runtime_cli_in_shell()

    # Backfill doctor package for existing installs
    try:
        doctor_src = SRC_DIR / "doctor"
        doctor_dest = NEXO_HOME / "doctor"
        if doctor_src.is_dir():
            import shutil
            if not doctor_dest.is_dir():
                shutil.copytree(str(doctor_src), str(doctor_dest), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                _log("Backfilled doctor package")
            else:
                # Update existing files
                for root, dirs, files in os.walk(str(doctor_src)):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    rel = os.path.relpath(root, str(doctor_src))
                    dest_dir = doctor_dest / rel
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        if f.endswith(".pyc"):
                            continue
                        src_f = Path(root) / f
                        dst_f = dest_dir / f
                        if not dst_f.exists() or src_f.stat().st_mtime > dst_f.stat().st_mtime:
                            shutil.copy2(str(src_f), str(dst_f))
    except Exception as e:
        _log(f"Doctor backfill error: {e}")

    # Backfill packaged core skills to a dedicated directory.
    try:
        skills_src = SRC_DIR / "skills"
        skills_dest = NEXO_HOME / "skills-core"
        if skills_src.is_dir():
            import shutil
            if not skills_dest.is_dir():
                shutil.copytree(str(skills_src), str(skills_dest), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                _log("Backfilled skills-core")
            else:
                for root, dirs, files in os.walk(str(skills_src)):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    rel = os.path.relpath(root, str(skills_src))
                    dest_dir = skills_dest / rel
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        if f.endswith(".pyc"):
                            continue
                        src_f = Path(root) / f
                        dst_f = dest_dir / f
                        if not dst_f.exists() or src_f.stat().st_mtime > dst_f.stat().st_mtime:
                            shutil.copy2(str(src_f), str(dst_f))
    except Exception as e:
        _log(f"Skills backfill error: {e}")

    # Backfill MCP doctor plugin so existing installs expose nexo_doctor.
    try:
        plugin_src = SRC_DIR / "plugins" / "doctor.py"
        plugin_dest = NEXO_HOME / "plugins" / "doctor.py"
        plugin_dest.parent.mkdir(parents=True, exist_ok=True)
        if plugin_src.is_file() and (not plugin_dest.exists() or plugin_src.stat().st_mtime > plugin_dest.stat().st_mtime):
            import shutil
            shutil.copy2(str(plugin_src), str(plugin_dest))
            _log("Backfilled doctor plugin")
    except Exception as e:
        _log(f"Doctor plugin backfill error: {e}")

    # Backfill script/skill templates for existing installs
    try:
        templates_src = REPO_DIR / "templates"
        templates_dest = NEXO_HOME / "templates"
        templates_dest.mkdir(parents=True, exist_ok=True)
        for fname in ("script-template.py", "nexo_helper.py", "skill-template.md", "skill-script-template.py"):
            src_file = templates_src / fname
            dest_file = templates_dest / fname
            if src_file.is_file() and (not dest_file.exists() or src_file.stat().st_mtime > dest_file.stat().st_mtime):
                import shutil
                shutil.copy2(str(src_file), str(dest_file))
                _log(f"Backfilled template {fname}")
    except Exception as e:
        _log(f"Template backfill error: {e}")

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


UPDATE_SUMMARY_FILE = NEXO_HOME / "logs" / "update-last-summary.json"
UPDATE_HISTORY_FILE = NEXO_HOME / "logs" / "update-history.jsonl"


def _resolve_sync_source() -> tuple[Path | None, Path | None]:
    dest = NEXO_HOME

    def _runtime_version_source() -> Path | None:
        version_file = NEXO_HOME / "version.json"
        if not version_file.is_file():
            return None
        try:
            data = json.loads(version_file.read_text())
        except Exception:
            return None
        source = str(data.get("source", "")).strip()
        if not source:
            return None
        candidate = Path(source).expanduser()
        if (candidate / "src").is_dir() and (candidate / "package.json").is_file():
            return candidate
        return None

    try:
        same_as_runtime = NEXO_CODE.resolve() == dest.resolve()
    except Exception:
        same_as_runtime = NEXO_CODE == dest

    if (
        not same_as_runtime
        and (NEXO_CODE / "db").is_dir()
        and (NEXO_CODE.parent / "package.json").is_file()
    ):
        return NEXO_CODE, NEXO_CODE.parent

    version_source = _runtime_version_source()
    if version_source:
        return version_source / "src", version_source
    return None, None


def _git_in_repo(repo_dir: Path, *args, timeout: int = 10) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _source_repo_status(repo_dir: Path) -> dict:
    if not (repo_dir / ".git").exists() and not (repo_dir / ".git").is_file():
        return {"is_git": False, "dirty": False, "behind": False, "diverged": False, "ahead": False}

    rc, dirty_out, dirty_err = _git_in_repo(repo_dir, "status", "--porcelain")
    dirty = rc == 0 and bool(dirty_out.strip())
    if rc != 0:
        return {
            "is_git": True,
            "dirty": True,
            "behind": False,
            "diverged": False,
            "ahead": False,
            "error": dirty_err or "git status failed",
        }

    rc, _, fetch_err = _git_in_repo(repo_dir, "fetch", "--quiet")
    if rc != 0:
        return {
            "is_git": True,
            "dirty": dirty,
            "behind": False,
            "diverged": False,
            "ahead": False,
            "error": fetch_err or "git fetch failed",
        }

    rc, local_head, _ = _git_in_repo(repo_dir, "rev-parse", "HEAD")
    rc2, remote_head, remote_err = _git_in_repo(repo_dir, "rev-parse", "@{u}")
    if rc != 0 or rc2 != 0:
        return {
            "is_git": True,
            "dirty": dirty,
            "behind": False,
            "diverged": False,
            "ahead": False,
            "error": remote_err or "no upstream configured",
        }
    rc, merge_base, merge_err = _git_in_repo(repo_dir, "merge-base", "HEAD", "@{u}")
    if rc != 0:
        return {
            "is_git": True,
            "dirty": dirty,
            "behind": False,
            "diverged": False,
            "ahead": False,
            "error": merge_err or "merge-base failed",
        }
    return {
        "is_git": True,
        "dirty": dirty,
        "behind": local_head != remote_head and merge_base == local_head,
        "ahead": local_head != remote_head and merge_base == remote_head,
        "diverged": merge_base not in {local_head, remote_head},
        "local_head": local_head,
        "remote_head": remote_head,
    }


def _backup_runtime_tree(dest: Path = NEXO_HOME) -> str:
    timestamp = time.strftime("%Y-%m-%d-%H%M%S")
    backup_dir = NEXO_HOME / "backups" / f"runtime-tree-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    code_dirs = ["hooks", "plugins", "db", "cognitive", "dashboard", "rules", "crons", "scripts", "doctor", "skills-core"]
    flat_files = [
        "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
        "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
        "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
        "client_sync.py",
        "client_preferences.py", "agent_runner.py",
        "auto_update.py", "tools_sessions.py", "tools_coordination.py",
        "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
        "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
        "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
        "public_contribution.py",
        "cron_recovery.py", "runtime_power.py", "requirements.txt", "package.json", "version.json",
    ]
    for name in code_dirs:
        src = dest / name
        if src.is_dir():
            import shutil
            shutil.copytree(str(src), str(backup_dir / name), dirs_exist_ok=True)
    for name in flat_files:
        src = dest / name
        if src.is_file():
            import shutil
            shutil.copy2(str(src), str(backup_dir / name))
    if (dest / "bin").is_dir():
        import shutil
        shutil.copytree(str(dest / "bin"), str(backup_dir / "bin"), dirs_exist_ok=True)
    return str(backup_dir)


def _restore_runtime_tree(backup_dir: str, dest: Path = NEXO_HOME) -> None:
    import shutil

    bdir = Path(backup_dir)
    if not bdir.is_dir():
        return
    for item in bdir.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(str(item), str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))


def _copy_runtime_from_source(src_dir: Path, repo_dir: Path, dest: Path = NEXO_HOME, progress_fn=None) -> dict:
    import shutil

    packages = ["db", "cognitive", "doctor", "dashboard", "rules", "crons", "hooks"]
    flat_files = [
        "server.py", "plugin_loader.py", "knowledge_graph.py", "kg_populate.py",
        "maintenance.py", "storage_router.py", "claim_graph.py", "hnsw_index.py",
        "evolution_cycle.py", "migrate_embeddings.py", "auto_close_sessions.py",
        "client_sync.py",
        "client_preferences.py", "agent_runner.py",
        "auto_update.py", "tools_sessions.py", "tools_coordination.py",
        "tools_reminders.py", "tools_reminders_crud.py", "tools_learnings.py",
        "tools_credentials.py", "tools_task_history.py", "tools_menu.py",
        "cli.py", "script_registry.py", "skills_runtime.py", "user_context.py",
        "public_contribution.py",
        "cron_recovery.py", "runtime_power.py", "requirements.txt",
    ]
    copied_packages = 0
    copied_files = 0

    _emit_progress(progress_fn, "Copying core packages...")
    for pkg in packages:
        pkg_src = src_dir / pkg
        pkg_dest = dest / pkg
        if pkg_src.is_dir():
            if pkg_dest.exists():
                shutil.rmtree(str(pkg_dest), ignore_errors=True)
            shutil.copytree(
                str(pkg_src),
                str(pkg_dest),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.db"),
            )
            copied_packages += 1

    _emit_progress(progress_fn, "Copying core modules...")
    for name in flat_files:
        src_file = src_dir / name
        if src_file.is_file():
            shutil.copy2(str(src_file), str(dest / name))
            copied_files += 1

    _emit_progress(progress_fn, "Copying plugin modules...")
    plugins_src = src_dir / "plugins"
    plugins_dest = dest / "plugins"
    if plugins_src.is_dir():
        plugins_dest.mkdir(parents=True, exist_ok=True)
        for item in plugins_src.iterdir():
            if item.is_file() and item.suffix == ".py":
                shutil.copy2(str(item), str(plugins_dest / item.name))

    _emit_progress(progress_fn, "Copying scripts...")
    scripts_src = src_dir / "scripts"
    scripts_dest = dest / "scripts"
    if scripts_src.is_dir():
        scripts_dest.mkdir(parents=True, exist_ok=True)
        for item in scripts_src.iterdir():
            if item.name == "__pycache__" or item.name.startswith("."):
                continue
            dst = scripts_dest / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                shutil.copytree(str(item), str(dst), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif item.is_file():
                shutil.copy2(str(item), str(dst))
                if item.suffix == ".sh":
                    dst.chmod(0o755)

    _emit_progress(progress_fn, "Copying templates and version metadata...")
    templates_src = repo_dir / "templates"
    templates_dest = dest / "templates"
    if templates_src.is_dir():
        templates_dest.mkdir(parents=True, exist_ok=True)
        for item in templates_src.iterdir():
            if item.is_file():
                shutil.copy2(str(item), str(templates_dest / item.name))

    package_json = repo_dir / "package.json"
    if package_json.is_file():
        shutil.copy2(str(package_json), str(dest / "package.json"))
        try:
            pkg = json.loads(package_json.read_text())
            (dest / "version.json").write_text(json.dumps({
                "version": pkg.get("version", "?"),
                "source": str(repo_dir),
            }, indent=2))
        except Exception:
            pass

    _emit_progress(progress_fn, "Copying core skills and runtime wrapper...")
    skills_src = src_dir / "skills"
    skills_dest = dest / "skills-core"
    if skills_src.is_dir():
        if skills_dest.exists():
            shutil.rmtree(str(skills_dest), ignore_errors=True)
        shutil.copytree(str(skills_src), str(skills_dest), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    bin_dir = dest / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "nexo"
    wrapper.write_text(_runtime_cli_wrapper_text())
    wrapper.chmod(0o755)

    return {
        "packages": copied_packages,
        "files": copied_files,
        "source": str(src_dir),
        "repo": str(repo_dir),
    }


def _reinstall_runtime_pip_deps(runtime_root: Path = NEXO_HOME) -> bool:
    req_file = runtime_root / "requirements.txt"
    if not req_file.exists():
        return True
    venv_pip = runtime_root / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        venv_pip = runtime_root / ".venv" / "bin" / "pip3"
    try:
        if venv_pip.exists():
            result = subprocess.run(
                [str(venv_pip), "install", "--quiet", "-r", str(req_file)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        else:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req_file), "--break-system-packages"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        return result.returncode == 0
    except Exception:
        return False


def _run_runtime_post_sync(dest: Path = NEXO_HOME, progress_fn=None) -> tuple[bool, list[str]]:
    actions: list[str] = []
    env = {**os.environ, "NEXO_HOME": str(dest), "NEXO_CODE": str(dest)}
    try:
        _emit_progress(progress_fn, "Initializing database and reconciling personal schedules...")
        init_result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "import db; "
                    "init_db = getattr(db, 'init_db', None); "
                    "init_db() if callable(init_db) else None; "
                    "import script_registry; "
                    "reconcile_scripts = getattr(script_registry, 'reconcile_personal_scripts', None); "
                    "result = reconcile_scripts(dry_run=False) if callable(reconcile_scripts) else {}; "
                    "print(json.dumps(result))"
                ),
            ],
            cwd=str(dest),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if init_result.returncode != 0:
            return False, [init_result.stderr.strip() or init_result.stdout.strip() or "runtime init failed"]
        actions.append("db+personal-sync")
        reconcile_payload = _parse_runtime_init_payload(init_result.stdout or "")
        extra_actions, reconcile_message = _personal_schedule_reconcile_summary(reconcile_payload)
        actions.extend(extra_actions)
        if reconcile_message:
            _emit_progress(progress_fn, reconcile_message)
    except Exception as e:
        return False, [f"runtime init error: {e}"]

    _emit_progress(progress_fn, "Reconciling Python dependencies...")
    if _reinstall_runtime_pip_deps(dest):
        actions.append("pip-deps")
    else:
        actions.append("pip-deps-warning")

    sync_path = dest / "crons" / "sync.py"
    if sync_path.is_file():
        try:
            _emit_progress(progress_fn, "Syncing core cron definitions...")
            sync_result = subprocess.run(
                [sys.executable, str(sync_path)],
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            if sync_result.returncode != 0:
                return False, [sync_result.stderr.strip() or sync_result.stdout.strip() or "cron sync failed"]
            actions.append("cron-sync")
        except Exception as e:
            return False, [f"cron sync error: {e}"]

    from runtime_power import apply_power_policy

    _emit_progress(progress_fn, "Refreshing runtime power helper...")
    power_result = apply_power_policy()
    if power_result.get("ok"):
        actions.append(f"power:{power_result.get('action')}")

    _emit_progress(progress_fn, "Refreshing shared client configs...")
    try:
        from client_sync import sync_all_clients
        from client_preferences import normalize_client_preferences

        schedule_path = dest / "config" / "schedule.json"
        schedule_payload = json.loads(schedule_path.read_text()) if schedule_path.exists() else {}
        client_sync_result = sync_all_clients(
            nexo_home=dest,
            runtime_root=dest,
            preferences=normalize_client_preferences(schedule_payload),
        )
        if client_sync_result.get("ok"):
            actions.append("client-sync")
        else:
            actions.append("client-sync-warning")
    except Exception as e:
        actions.append(f"client-sync-warning:{e}")

    _emit_progress(progress_fn, "Verifying runtime imports...")
    verify = subprocess.run(
        [sys.executable, "-c", "import server"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    if verify.returncode != 0:
        return False, [verify.stderr.strip() or verify.stdout.strip() or "import verify failed"]
    actions.append("verify")
    return True, actions


def _runtime_busy_reason() -> str | None:
    try:
        from db import get_active_sessions
        active = get_active_sessions()
    except Exception:
        return None
    if active:
        return f"active sessions: {len(active)}"
    return None


def _write_update_summary(summary: dict):
    try:
        logs_dir = NEXO_HOME / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(summary)
        payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
        UPDATE_SUMMARY_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        with UPDATE_HISTORY_FILE.open("a") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        _log(f"Failed to write update summary: {e}")


def _emit_progress(progress_fn, message: str) -> None:
    if callable(progress_fn):
        try:
            progress_fn(message)
        except Exception:
            pass


def _parse_runtime_init_payload(stdout: str) -> dict:
    """Extract the JSON payload emitted by the runtime init helper."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _personal_schedule_reconcile_summary(reconcile_result: dict) -> tuple[list[str], str | None]:
    """Turn reconcile_personal_scripts() output into stable update actions."""
    if not isinstance(reconcile_result, dict):
        return [], None

    ensured = reconcile_result.get("ensure_schedules", {})
    if not isinstance(ensured, dict):
        return [], None

    created = len(ensured.get("created", []) or [])
    repaired = len(ensured.get("repaired", []) or [])
    invalid = len(ensured.get("invalid", []) or [])

    actions: list[str] = []
    parts: list[str] = []
    if created or repaired:
        actions.append(f"personal-schedules-healed:{created + repaired}")
        parts.append(f"{created} created")
        parts.append(f"{repaired} repaired")
    if invalid:
        actions.append(f"personal-schedules-invalid:{invalid}")
        parts.append(f"{invalid} invalid")
    if not parts:
        return [], None
    return actions, "Personal schedules: " + ", ".join(parts) + "."


def manual_sync_update(*, interactive: bool = False, allow_source_pull: bool = True, progress_fn=None) -> dict:
    src_dir, repo_dir = _resolve_sync_source()
    if src_dir is None or repo_dir is None:
        return {"ok": False, "mode": "sync", "error": "No source repo recorded for this runtime."}

    _emit_progress(progress_fn, "Checking recorded source repository...")
    source_status = _source_repo_status(repo_dir)
    pulled = False
    old_head = source_status.get("local_head")
    if allow_source_pull and source_status.get("is_git"):
        if source_status.get("dirty"):
            _log("Source repo has local changes; syncing local tree without remote pull.")
        elif source_status.get("diverged"):
            _log("Source repo diverged; syncing local tree without remote pull.")
        elif source_status.get("behind"):
            _emit_progress(progress_fn, "Pulling latest source changes...")
            rc, _, pull_err = _git_in_repo(repo_dir, "pull", "--ff-only", timeout=60)
            if rc != 0:
                return {"ok": False, "mode": "sync", "error": pull_err or "git pull failed"}
            pulled = True

    _emit_progress(progress_fn, "Creating runtime backups...")
    db_backup_dir = _backup_dbs()
    tree_backup_dir = _backup_runtime_tree(NEXO_HOME)
    sync_result = {"ok": False, "mode": "sync", "pulled_source": pulled, "backup_dir": db_backup_dir, "tree_backup": tree_backup_dir}
    try:
        _emit_progress(progress_fn, "Syncing runtime files...")
        copy_stats = _copy_runtime_from_source(src_dir, repo_dir, NEXO_HOME, progress_fn=progress_fn)
        _emit_progress(progress_fn, "Reconciling runtime state...")
        ok, actions = _run_runtime_post_sync(NEXO_HOME, progress_fn=progress_fn)
        if not ok:
            raise RuntimeError("; ".join(actions))
        sync_result.update({
            "ok": True,
            "updated": True,
            "packages": copy_stats["packages"],
            "files": copy_stats["files"],
            "actions": actions,
            "source": copy_stats["source"],
            "repo": copy_stats["repo"],
        })
        _emit_progress(progress_fn, "Runtime update completed.")
    except Exception as e:
        _emit_progress(progress_fn, "Update failed; restoring previous runtime state...")
        _restore_runtime_tree(tree_backup_dir, NEXO_HOME)
        if db_backup_dir:
            _restore_dbs(db_backup_dir)
        _reinstall_runtime_pip_deps(NEXO_HOME)
        if pulled and old_head:
            _git_in_repo(repo_dir, "reset", "--hard", old_head, timeout=60)
        sync_result.update({"error": str(e), "rolled_back": True})
    _write_update_summary(sync_result)
    return sync_result


def startup_preflight(*, entrypoint: str, interactive: bool = False) -> dict:
    result = {
        "entrypoint": entrypoint,
        "checked": False,
        "updated": False,
        "actions": [],
        "skipped_reason": None,
        "deferred_reason": None,
        "git_update": None,
        "npm_notice": None,
        "claude_md_update": None,
        "migrations": [],
        "power_policy": None,
        "power_message": None,
        "full_disk_access_status": None,
        "full_disk_access_message": None,
        "error": None,
    }

    from runtime_power import (
        apply_power_policy,
        ensure_power_policy_choice,
        get_power_policy,
        ensure_full_disk_access_choice,
        get_full_disk_access_status,
    )

    choice = ensure_power_policy_choice(interactive=interactive, reason=entrypoint)
    power_result = apply_power_policy(choice.get("policy"))
    fda_choice = ensure_full_disk_access_choice(interactive=interactive, reason=entrypoint)
    result["power_policy"] = choice.get("policy") or get_power_policy()
    result["power_message"] = power_result.get("message")
    result["full_disk_access_status"] = fda_choice.get("status") or get_full_disk_access_status()
    result["full_disk_access_message"] = fda_choice.get("message")
    if power_result.get("ok"):
        result["actions"].append(f"power:{power_result.get('action')}")

    src_dir, repo_dir = _resolve_sync_source()
    if src_dir is not None and repo_dir is not None:
        try:
            from db import init_db
            from script_registry import reconcile_personal_scripts

            _run_db_migrations()
            result["migrations"] = run_file_migrations()
            result["claude_md_update"] = _migrate_claude_md()
            _sync_watchdog_hash_registry()
            _warn_protected_runtime_location()
            _ensure_runtime_cli_wrapper()
            _ensure_runtime_cli_in_shell()
            init_db()
            reconcile_result = reconcile_personal_scripts(dry_run=False)
            result["actions"].append("db+personal-sync")
            extra_actions, reconcile_message = _personal_schedule_reconcile_summary(reconcile_result)
            result["actions"].extend(extra_actions)
            if reconcile_message:
                _log(reconcile_message)
        except Exception as e:
            result["error"] = str(e)
            _write_update_summary(result)
            return result

        try:
            last_check = _read_last_check()
            now = time.time()
            schedule_data = json.loads((NEXO_HOME / "config" / "schedule.json").read_text()) if (NEXO_HOME / "config" / "schedule.json").exists() else {}
            if not schedule_data.get("auto_update", True):
                result["skipped_reason"] = "auto_update disabled in schedule.json"
                _write_update_summary(result)
                return result
            if now - float(last_check.get("timestamp", 0) or 0) < CHECK_COOLDOWN_SECONDS:
                result["skipped_reason"] = "cooldown"
                _write_update_summary(result)
                return result
            busy_reason = _runtime_busy_reason()
            if busy_reason:
                result["deferred_reason"] = busy_reason
                _write_last_check({"timestamp": now, "mode": "sync", "deferred_reason": busy_reason})
                _write_update_summary(result)
                return result

            source_status = _source_repo_status(repo_dir)
            if source_status.get("dirty"):
                result["deferred_reason"] = "source repo has local changes"
            elif source_status.get("diverged"):
                result["deferred_reason"] = "source repo diverged from upstream"
            elif source_status.get("behind"):
                result["checked"] = True
                sync_result = manual_sync_update(interactive=False, allow_source_pull=True)
                result["updated"] = bool(sync_result.get("ok") and sync_result.get("updated"))
                result["actions"].extend(sync_result.get("actions", []))
                if sync_result.get("error"):
                    result["error"] = sync_result["error"]
            else:
                result["checked"] = True

            _write_last_check({
                "timestamp": now,
                "mode": "sync",
                "updated": result["updated"],
                "deferred_reason": result["deferred_reason"],
            })
        except Exception as e:
            result["error"] = f"sync startup preflight failed: {e}"
        _write_update_summary(result)
        return result

    result = auto_update_check()
    result["entrypoint"] = entrypoint
    result["power_policy"] = choice.get("policy") or get_power_policy()
    result["power_message"] = power_result.get("message")
    result["full_disk_access_status"] = fda_choice.get("status") or get_full_disk_access_status()
    result["full_disk_access_message"] = fda_choice.get("message")
    if power_result.get("ok"):
        actions = result.setdefault("actions", [])
        actions.append(f"power:{power_result.get('action')}")
    result["updated"] = bool(result.get("git_update"))
    _write_update_summary(result)
    return result
