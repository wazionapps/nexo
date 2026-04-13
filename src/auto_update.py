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
import shutil
import subprocess
import sys
import time
from pathlib import Path

from runtime_home import export_resolved_nexo_home, managed_nexo_home

NEXO_HOME = export_resolved_nexo_home()
DATA_DIR = NEXO_HOME / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Repo root: go up from src/
SRC_DIR = Path(__file__).resolve().parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(SRC_DIR)))
REPO_DIR = SRC_DIR.parent


def _resolve_repo_dir() -> Path:
    """Return the source repo root, falling back to NEXO_HOME for npm installs.

    On git installs SRC_DIR is ``<repo>/src`` so parent is the repo root.
    On npm installs SRC_DIR *is* NEXO_HOME (``~/.nexo``) so parent is ``~``,
    which has no ``templates/`` or ``migrations/``.  In that case we fall back
    to NEXO_HOME itself where the installer already copied the runtime files.
    """
    candidate = SRC_DIR.parent
    if (candidate / "templates").is_dir():
        return candidate
    if (NEXO_HOME / "templates").is_dir():
        return NEXO_HOME
    return candidate


_RESOLVED_REPO_DIR = _resolve_repo_dir()

LAST_CHECK_FILE = DATA_DIR / "auto_update_last_check.json"
MIGRATION_VERSION_FILE = DATA_DIR / "migration_version"
CLAUDE_MD_VERSION_FILE = DATA_DIR / "claude_md_version.txt"
MIGRATIONS_DIR = _RESOLVED_REPO_DIR / "migrations"
TEMPLATE_FILE = _RESOLVED_REPO_DIR / "templates" / "CLAUDE.md.template"

CHECK_COOLDOWN_SECONDS = 3600  # 1 hour
GIT_TIMEOUT_SECONDS = 4  # stay well under the 5s total budget
CRITICAL_BACKUP_TABLES = ("learnings", "session_diary", "guard_checks", "protocol_debt")


def _log(msg: str):
    """Log to stderr with prefix."""
    print(f"[NEXO auto-update] {msg}", file=sys.stderr)


def _critical_table_count(db_path: Path, table: str) -> int | None:
    """Return COUNT(*) for a critical table when it exists, otherwise None."""
    import sqlite3

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _find_primary_db_path() -> Path | None:
    """Return the main nexo.db path if present."""
    for candidate in (DATA_DIR / "nexo.db", NEXO_HOME / "nexo.db", SRC_DIR / "nexo.db"):
        if candidate.is_file():
            return candidate
    return None


def _validate_db_backup(source_db: Path, backup_db: Path) -> dict:
    """Check that a backup preserves non-empty critical tables from the source DB."""
    report = {
        "ok": True,
        "source_db": str(source_db),
        "backup_db": str(backup_db),
        "source_counts": {},
        "backup_counts": {},
        "regressions": [],
        "errors": [],
    }
    if not source_db.is_file():
        report["ok"] = False
        report["errors"].append(f"source db missing: {source_db}")
        return report
    if not backup_db.is_file():
        report["ok"] = False
        report["errors"].append(f"backup db missing: {backup_db}")
        return report

    for table in CRITICAL_BACKUP_TABLES:
        source_count = _critical_table_count(source_db, table)
        backup_count = _critical_table_count(backup_db, table)
        report["source_counts"][table] = source_count
        report["backup_counts"][table] = backup_count

        if source_count is None:
            continue
        if backup_count is None:
            report["regressions"].append({
                "table": table,
                "source": source_count,
                "backup": None,
                "reason": "missing_in_backup",
            })
            continue
        if source_count > 0 and backup_count == 0:
            report["regressions"].append({
                "table": table,
                "source": source_count,
                "backup": backup_count,
                "reason": "critical_rows_lost",
            })

    if report["regressions"] or report["errors"]:
        report["ok"] = False
    return report


def _create_validated_db_backup() -> tuple[str | None, dict | None]:
    """Create a DB backup and validate that critical tables still contain data."""
    backup_dir = _backup_dbs()
    if not backup_dir:
        return None, None

    source_db = _find_primary_db_path()
    if source_db is None:
        return backup_dir, None

    report = _validate_db_backup(source_db, Path(backup_dir) / source_db.name)
    if not report["ok"]:
        details = ", ".join(
            f"{item['table']} {item['source']}->{item['backup']}"
            for item in report["regressions"]
        ) or "; ".join(report["errors"])
        _log(f"DB backup validation failed: {details}")
    return backup_dir, report


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
        f'DEFAULT_NEXO_HOME="{managed_nexo_home()}"\n'
        'RUNTIME_HOME="${NEXO_HOME:-$DEFAULT_NEXO_HOME}"\n'
        'if [ "$RUNTIME_HOME" = "${HOME}/claude" ] && [ -e "$DEFAULT_NEXO_HOME" ]; then\n'
        '  RUNTIME_HOME="$DEFAULT_NEXO_HOME"\n'
        'fi\n'
        'if [ -e "$RUNTIME_HOME" ] && [ -e "$DEFAULT_NEXO_HOME" ]; then\n'
        '  RESOLVED_RUNTIME="$(cd "$RUNTIME_HOME" 2>/dev/null && pwd -P || true)"\n'
        '  RESOLVED_DEFAULT="$(cd "$DEFAULT_NEXO_HOME" 2>/dev/null && pwd -P || true)"\n'
        '  if [ -n "$RESOLVED_RUNTIME" ] && [ "$RESOLVED_RUNTIME" = "$RESOLVED_DEFAULT" ]; then\n'
        '    RUNTIME_HOME="$DEFAULT_NEXO_HOME"\n'
        '  fi\n'
        'fi\n'
        'NEXO_HOME="$RUNTIME_HOME"\n'
        'export NEXO_HOME\n'
        'resolve_code_dir() {\n'
        '  if [ -n "${NEXO_CODE:-}" ] && [ -f "${NEXO_CODE%/}/cli.py" ]; then\n'
        '    printf \'%s\\n\' "${NEXO_CODE%/}"\n'
        '    return 0\n'
        '  fi\n'
        '  if [ -f "$NEXO_HOME/cli.py" ]; then\n'
        '    printf \'%s\\n\' "$NEXO_HOME"\n'
        '    return 0\n'
        '  fi\n'
        '  printf \'%s\\n\' "$NEXO_HOME"\n'
        '}\n'
        'NEXO_CODE="$(resolve_code_dir)"\n'
        'export NEXO_CODE\n'
        'resolve_python() {\n'
        '  local candidates=()\n'
        '  local candidate=""\n'
        '  if [ -n "${NEXO_RUNTIME_PYTHON:-}" ]; then candidates+=("$NEXO_RUNTIME_PYTHON"); fi\n'
        '  if [ -n "${NEXO_PYTHON:-}" ]; then candidates+=("$NEXO_PYTHON"); fi\n'
        '  candidates+=("$NEXO_CODE/.venv/bin/python3" "$NEXO_CODE/.venv/bin/python")\n'
        '  if [ "$NEXO_CODE" != "$NEXO_HOME" ]; then\n'
        '    candidates+=("$NEXO_HOME/.venv/bin/python3" "$NEXO_HOME/.venv/bin/python")\n'
        '  fi\n'
        '  case "$(uname -s)" in\n'
        '    Darwin) candidates+=("/opt/homebrew/bin/python3" "/usr/local/bin/python3") ;;\n'
        '    *) candidates+=("/usr/local/bin/python3" "/usr/bin/python3") ;;\n'
        '  esac\n'
        '  if command -v python3 >/dev/null 2>&1; then candidates+=("$(command -v python3)"); fi\n'
        '  if command -v python >/dev/null 2>&1; then candidates+=("$(command -v python)"); fi\n'
        '  for candidate in "${candidates[@]}"; do\n'
        '    [ -n "$candidate" ] || continue\n'
        '    [ -x "$candidate" ] || continue\n'
        '    if NEXO_HOME="$NEXO_HOME" NEXO_CODE="$NEXO_CODE" "$candidate" -c "import fastmcp" >/dev/null 2>&1; then\n'
        '      printf \'%s\\n\' "$candidate"\n'
        '      return 0\n'
        '    fi\n'
        '  done\n'
        '  for candidate in "${candidates[@]}"; do\n'
        '    [ -n "$candidate" ] || continue\n'
        '    [ -x "$candidate" ] || continue\n'
        '    printf \'%s\\n\' "$candidate"\n'
        '    return 0\n'
        '  done\n'
        '  return 1\n'
        '}\n'
        'PYTHON="$(resolve_python || true)"\n'
        'if [ -z "$PYTHON" ]; then\n'
        '  echo "NEXO runtime Python not found. Run nexo-brain or nexo update to repair the installation." >&2\n'
        '  exit 1\n'
        'fi\n'
        'CLI_PY="$NEXO_CODE/cli.py"\n'
        'if [ ! -f "$CLI_PY" ] && [ -f "$NEXO_HOME/cli.py" ]; then\n'
        '  NEXO_CODE="$NEXO_HOME"\n'
        '  export NEXO_CODE\n'
        '  CLI_PY="$NEXO_HOME/cli.py"\n'
        'fi\n'
        'if [ ! -f "$CLI_PY" ]; then\n'
        '  echo "NEXO CLI not found under $NEXO_HOME. Run nexo-brain or nexo update to repair the installation." >&2\n'
        '  exit 1\n'
        'fi\n'
        'exec "$PYTHON" "$CLI_PY" "$@"\n'
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


def _venv_python_path(runtime_root: Path = NEXO_HOME) -> Path:
    if sys.platform == "win32":
        return runtime_root / ".venv" / "Scripts" / "python.exe"
    return runtime_root / ".venv" / "bin" / "python3"


def _venv_pip_path(runtime_root: Path = NEXO_HOME) -> Path:
    if sys.platform == "win32":
        return runtime_root / ".venv" / "Scripts" / "pip.exe"
    return runtime_root / ".venv" / "bin" / "pip"


def _ensure_runtime_venv(runtime_root: Path = NEXO_HOME) -> Path | None:
    venv_python = _venv_python_path(runtime_root)
    if venv_python.exists():
        return venv_python
    try:
        runtime_root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(runtime_root / ".venv")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and venv_python.exists():
            _log(f"Created managed venv at {runtime_root / '.venv'}")
            return venv_python
        _log(f"venv creation failed (exit {result.returncode}): {result.stderr or result.stdout}")
    except Exception as e:
        _log(f"venv creation failed: {e}")
    return None


def _reinstall_pip_deps() -> bool:
    """Reinstall Python deps from requirements.txt. Returns True on success."""
    req_file = SRC_DIR / "requirements.txt"
    if not req_file.exists():
        return True
    _ensure_runtime_venv(NEXO_HOME)
    venv_pip = _venv_pip_path(NEXO_HOME)
    if not venv_pip.exists() and sys.platform != "win32":
        alt_pip = NEXO_HOME / ".venv" / "bin" / "pip3"
        if alt_pip.exists():
            venv_pip = alt_pip
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
        NEXO_HOME / "scripts" / "heartbeat-enforcement.py",
        NEXO_HOME / "scripts" / "heartbeat-posttool.sh",
        NEXO_HOME / "scripts" / "heartbeat-user-msg.sh",
        NEXO_HOME / "hooks" / "heartbeat-guard.sh",
    ]
    conditional_retired = [
        (NEXO_HOME / "scripts" / "nexo-postcompact.sh", NEXO_HOME / "hooks" / "post-compact.sh"),
        (NEXO_HOME / "scripts" / "nexo-memory-precompact.sh", NEXO_HOME / "hooks" / "pre-compact.sh"),
        (NEXO_HOME / "scripts" / "nexo-memory-stop.sh", NEXO_HOME / "hooks" / "session-stop.sh"),
        (NEXO_HOME / "scripts" / "nexo-session-briefing.sh", NEXO_HOME / "hooks" / "session-start.sh"),
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
    for target, canonical in conditional_retired:
        try:
            if target.exists() and canonical.exists():
                target.unlink()
                _log(f"Removed retired runtime alias: {target.name} (canonical: {canonical.name})")
        except Exception as e:
            _log(f"Retired runtime alias cleanup warning ({target.name}): {e}")


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


def _reload_launch_agents_after_bump() -> dict:
    """Unload+load NEXO LaunchAgents so they pick up the new code on next fire.

    Closes Bloque D of NEXO-AUDIT-2026-04-11 (learning #186 from Fase 1).
    Until this helper, `nexo update` would `git pull` the new code into
    NEXO_CODE but the 40+ LaunchAgents already running held the old
    Python modules in memory until macOS happened to restart them. With
    a single function call we explicitly tell launchd to reload the
    plist files so the next fire reads the fresh code.

    Best-effort throughout — a failure here must NEVER block the update
    that just succeeded. Returns a dict with what was attempted so the
    caller can log a single summary line.

    Returns:
        {
          "scanned": N,        # plists found in ~/Library/LaunchAgents
          "reloaded": N,       # plists where unload+load both succeeded
          "skipped_missing": N, # plist file vanished mid-scan
          "errors": [{plist, stderr}],
        }

    Linux equivalent: systemctl --user daemon-reload + restart of timer
    units. Implemented as a no-op stub on Linux for now (the macOS
    LaunchAgent path is the production target — Linux users running
    `nexo update` get the cron sync but not the per-timer restart yet).
    Captured as a TODO for the next round.
    """
    result: dict = {
        "scanned": 0,
        "reloaded": 0,
        "skipped_missing": 0,
        "errors": [],
        "platform": sys.platform,
    }

    if sys.platform != "darwin":
        # macOS-only for now. systemd path tracked separately.
        return result

    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    if not launch_agents_dir.is_dir():
        return result

    try:
        plists = sorted(launch_agents_dir.glob("com.nexo.*.plist"))
    except Exception as e:
        result["errors"].append({"plist": "*", "stderr": f"glob failed: {e}"})
        return result

    result["scanned"] = len(plists)
    for plist in plists:
        try:
            if not plist.is_file():
                result["skipped_missing"] += 1
                continue
            # launchctl bootout / bootstrap is the modern API but requires
            # the GUI session id ($UID/Background or gui/$UID). The legacy
            # unload + load -w pair still works on every macOS NEXO supports
            # and does not need a session id, so we use it here.
            unload_proc = subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
            # unload returns non-zero if the agent was not loaded — that
            # is fine, we still try to load fresh.
            load_proc = subprocess.run(
                ["launchctl", "load", "-w", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
            if load_proc.returncode == 0:
                result["reloaded"] += 1
            else:
                result["errors"].append({
                    "plist": plist.name,
                    "stderr": (load_proc.stderr or load_proc.stdout or "load failed")[:300],
                })
        except subprocess.TimeoutExpired:
            result["errors"].append({"plist": plist.name, "stderr": "launchctl timeout"})
        except Exception as e:
            result["errors"].append({"plist": plist.name, "stderr": str(e)[:300]})

    return result


AUTO_UPDATE_BACKUP_KEEP = 10
"""Maximum number of auto-update backups to keep per prefix.

Both `pre-autoupdate-*/` (DB snapshots) and `runtime-tree-*/` (code mirrors)
were accumulating indefinitely, growing to tens of GB on long-running
installs. Rotating to the N most recent keeps a meaningful rollback window
without unbounded disk use."""


def _rotate_auto_update_backups(prefix: str, keep: int = AUTO_UPDATE_BACKUP_KEEP) -> int:
    """Delete old auto-update backup directories matching a prefix, keeping `keep` most recent.

    Silent on failures — cleanup must never interrupt the auto-update flow.
    Returns number of entries removed (0 on failure or nothing to prune).
    """
    if keep <= 0:
        return 0
    base = NEXO_HOME / "backups"
    if not base.is_dir():
        return 0
    try:
        candidates = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    except Exception as e:
        _log(f"Backup rotation scan warning ({prefix}): {e}")
        return 0
    if len(candidates) <= keep:
        return 0
    # Newest first by modification time, then delete everything beyond `keep`
    try:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception as e:
        _log(f"Backup rotation sort warning ({prefix}): {e}")
        return 0
    removed = 0
    import shutil as _shutil
    for old in candidates[keep:]:
        try:
            _shutil.rmtree(str(old))
            removed += 1
        except Exception as e:
            _log(f"Backup rotation remove warning ({old.name}): {e}")
    if removed:
        _log(f"Rotated {removed} old {prefix}* backup(s), kept {keep} most recent")
    return removed


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
        src_conn = None
        dst_conn = None
        try:
            src_conn = sqlite3.connect(str(db_file))
            dst_conn = sqlite3.connect(str(backup_dir / db_file.name))
            src_conn.backup(dst_conn)
        except Exception as e:
            _log(f"DB backup warning ({db_file.name}): {e}")
        finally:
            for conn in (dst_conn, src_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
    # Opportunistic rotation: keep only the N most recent pre-autoupdate dirs.
    # Failures here must never bubble up — the caller depends on the backup
    # path string for rollback and should not see spurious exceptions from
    # housekeeping of older entries.
    try:
        _rotate_auto_update_backups("pre-autoupdate-")
    except Exception as e:
        _log(f"Backup rotation warning (pre-autoupdate): {e}")
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
                src_conn = None
                dst_conn = None
                try:
                    src_conn = sqlite3.connect(str(db_backup))
                    dst_conn = sqlite3.connect(str(candidate))
                    src_conn.backup(dst_conn)
                    _log(f"Restored DB: {db_backup.name}")
                except Exception as e:
                    _log(f"DB restore warning ({db_backup.name}): {e}")
                finally:
                    for conn in (dst_conn, src_conn):
                        if conn is not None:
                            try:
                                conn.close()
                            except Exception:
                                pass
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

    db_backup_dir, backup_report = _create_validated_db_backup()
    if backup_report is not None and not backup_report["ok"]:
        _log("Skipping auto-update because the validated pre-update DB backup is not trustworthy.")
        return None

    rc, pull_out, pull_err = _git("pull", "--ff-only")
    if rc != 0:
        _log(f"git pull --ff-only failed: {pull_err}")
        return None  # Don't break anything

    new_version = _read_package_version()
    new_req_hash = _requirements_hash()

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

    # Bloque D / learning #186: when the package version actually
    # changed, reload the LaunchAgents so the 40+ background crons
    # pick up the new code on their next fire instead of holding the
    # old Python modules in memory until macOS happens to restart them.
    # Best-effort — never blocks the update flow.
    if old_version != new_version:
        try:
            reload_summary = _reload_launch_agents_after_bump()
            if reload_summary.get("reloaded"):
                _log(
                    f"Reloaded {reload_summary['reloaded']}/{reload_summary['scanned']} "
                    f"NEXO LaunchAgents after version bump"
                    + (f" ({len(reload_summary['errors'])} errors)" if reload_summary["errors"] else "")
                )
            elif reload_summary.get("scanned"):
                _log(
                    f"LaunchAgent reload after bump: scanned {reload_summary['scanned']}, "
                    f"reloaded 0, errors {len(reload_summary['errors'])}"
                )
        except Exception as e:
            _log(f"LaunchAgent reload after bump failed: {e}")

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

    Migrations are ordered and sequential: if migration N fails, all subsequent
    migrations are skipped so that N is retried on the next startup and no
    migration is permanently skipped by a version-pointer gap.

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
            break  # Stop on first failure so it retries next startup

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
    """Sync the managed Claude bootstrap into ~/.claude/CLAUDE.md."""
    try:
        from bootstrap_docs import sync_client_bootstrap
    except Exception as exc:
        _log(f"CLAUDE.md migration import error: {exc}")
        return None

    result = sync_client_bootstrap(
        "claude_code",
        nexo_home=NEXO_HOME,
        operator_name="",
        user_home=Path.home(),
    )
    if not result.get("ok"):
        _log(f"CLAUDE.md migration failed: {result.get('error', 'unknown error')}")
        return None
    version = result.get("version", "")
    if version:
        _write_installed_claude_md_version(version)
    action = result.get("action", "updated")
    if action == "unchanged":
        return f"CLAUDE.md v{version}: already current"
    msg = f"CLAUDE.md v{version}: {action}"
    _log(msg)
    return msg


def _sync_client_bootstraps(preferences: dict | None = None) -> list[str]:
    try:
        from bootstrap_docs import sync_enabled_bootstraps
    except Exception as exc:
        _log(f"Client bootstrap sync import error: {exc}")
        return []

    results = sync_enabled_bootstraps(
        nexo_home=NEXO_HOME,
        operator_name="",
        user_home=Path.home(),
        preferences=preferences,
    )
    messages: list[str] = []
    for client_key, item in results.items():
        if item.get("skipped"):
            continue
        if not item.get("ok"):
            _log(f"{client_key} bootstrap sync failed: {item.get('error', 'unknown error')}")
            continue
        action = item.get("action", "updated")
        version = item.get("version", "")
        label = "Codex AGENTS.md" if client_key == "codex" else "CLAUDE.md"
        if action == "unchanged":
            messages.append(f"{label} v{version}: already current")
        else:
            messages.append(f"{label} v{version}: {action}")
    return messages


# ── Main entry point ─────────────────────────────────────────────────

_AUTO_UPDATE_LOCK_FILE = NEXO_HOME / "operations" / ".auto_update.lock"
_AUTO_UPDATE_LOCK_STALE_SECONDS = 600  # 10 minutes


def _acquire_auto_update_lock() -> tuple[bool, object | None, str]:
    """Acquire an exclusive non-blocking lock on the auto_update lockfile.

    Closes NF-AUDIT-2026-04-11-UPDATE-LOCK. Two NEXO terminals starting at
    the same moment after a version bump used to race on
    auto_update_check(): they would both run run_migrations(),
    _check_git_updates(), and the file/hooks sync, occasionally tripping
    UNIQUE constraints on schema_migrations or producing torn writes on
    shared files.

    The lock uses fcntl.flock(LOCK_EX | LOCK_NB) so the second caller
    returns instantly with a clean "skipped_reason=locked_by_other_process"
    rather than blocking the server startup. The lock file persists across
    crashes — we treat any lock older than 10 minutes as stale and steal
    it, so a hard kill mid-update never wedges future runs forever.

    Returns:
        (acquired, fh, reason)
        - acquired: True if we now hold the lock, False otherwise.
        - fh: the open file handle (caller MUST close it after release).
        - reason: human-readable explanation when not acquired.
    """
    try:
        _AUTO_UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, None, f"cannot create lock directory: {e}"

    # Steal stale locks: if the lockfile exists and was last modified more
    # than 10 minutes ago, assume the previous holder crashed and reset it.
    try:
        if _AUTO_UPDATE_LOCK_FILE.exists():
            age = time.time() - _AUTO_UPDATE_LOCK_FILE.stat().st_mtime
            if age > _AUTO_UPDATE_LOCK_STALE_SECONDS:
                try:
                    _AUTO_UPDATE_LOCK_FILE.unlink()
                except Exception:
                    pass  # Will fall through to the open below
    except Exception:
        pass

    try:
        fh = open(_AUTO_UPDATE_LOCK_FILE, "a+")
    except Exception as e:
        return False, None, f"cannot open lock file: {e}"

    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        # Non-POSIX platform. Best-effort: write a PID stamp and proceed.
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}:{time.time()}\n")
            fh.flush()
        except Exception:
            pass
        return True, fh, ""
    except (OSError, BlockingIOError):
        try:
            fh.close()
        except Exception:
            pass
        return False, None, "locked_by_other_process"

    # We have the lock. Stamp PID + timestamp so observers can see who.
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}:{time.time()}\n")
        fh.flush()
    except Exception:
        pass
    return True, fh, ""


def _release_auto_update_lock(fh: object | None) -> None:
    """Release the lock acquired by _acquire_auto_update_lock and close the fd."""
    if fh is None:
        return
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        fh.close()  # type: ignore[attr-defined]
    except Exception:
        pass


def auto_update_check() -> dict:
    """Run the full auto-update check at server startup.

    NEVER raises an exception — always returns a dict.

    Phase 1 (local, safe, no network):
        - DB schema migrations
        - File-based migrations
        - managed client bootstrap migration

    Phase 2 (network, wrapped in try/except):
        - git fetch/pull (if git repo)
        - npm version check (if non-git install)

    Concurrency:
        Wrapped in a non-blocking exclusive flock so a second concurrent
        terminal returns instantly with skipped_reason='locked_by_other_process'
        instead of racing on run_migrations / git pull / file sync. Stale
        locks (>10 minutes) are auto-stolen.

    Returns a dict with:
        - checked: bool — whether a network check was actually performed
        - git_update: str|None — git update status message
        - npm_notice: str|None — npm upgrade notice for non-git installs
        - claude_md_update: str|None — CLAUDE.md migration status
        - client_bootstrap_updates: list[str] — Codex/Claude bootstrap sync statuses
        - migrations: list — file-based migration results
        - db_migrations: int — number of DB schema migrations applied
        - skipped_reason: str|None — why the network check was skipped (cooldown, locked, etc.)
        - error: str|None — error message if something failed (informational only)
    """
    acquired, lock_fh, lock_reason = _acquire_auto_update_lock()
    if not acquired:
        return {
            "checked": False,
            "git_update": None,
            "npm_notice": None,
            "claude_md_update": None,
            "client_bootstrap_updates": [],
            "migrations": [],
            "db_migrations": 0,
            "skipped_reason": lock_reason or "locked_by_other_process",
            "error": None,
        }
    try:
        return _auto_update_check_locked()
    finally:
        _release_auto_update_lock(lock_fh)


def _auto_update_check_locked() -> dict:
    """Inner body of auto_update_check, executed while holding the lockfile."""
    result = {
        "checked": False,
        "git_update": None,
        "npm_notice": None,
        "claude_md_update": None,
        "client_bootstrap_updates": [],
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
        for fname in ("cli.py", "script_registry.py", "skills_runtime.py", "cron_recovery.py", "client_preferences.py", "agent_runner.py", "bootstrap_docs.py"):
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

    # Backfill all templates for existing installs (no hardcoded list)
    try:
        templates_src = _RESOLVED_REPO_DIR / "templates"
        templates_dest = NEXO_HOME / "templates"
        templates_dest.mkdir(parents=True, exist_ok=True)
        import shutil
        if templates_src.is_dir():
            for item in templates_src.iterdir():
                if item.name == "__pycache__":
                    continue
                dest_item = templates_dest / item.name
                if item.is_file():
                    if not dest_item.exists() or item.stat().st_mtime > dest_item.stat().st_mtime:
                        shutil.copy2(str(item), str(dest_item))
                elif item.is_dir():
                    dest_item.mkdir(parents=True, exist_ok=True)
                    for sub in item.iterdir():
                        if sub.is_file():
                            dest_sub = dest_item / sub.name
                            if not dest_sub.exists() or sub.stat().st_mtime > dest_sub.stat().st_mtime:
                                shutil.copy2(str(sub), str(dest_sub))
    except Exception as e:
        _log(f"Template backfill error: {e}")

    # Managed client bootstrap migration
    try:
        bootstrap_messages = _sync_client_bootstraps(schedule_data if "schedule_data" in locals() else None)
        result["client_bootstrap_updates"] = bootstrap_messages
        result["claude_md_update"] = next((msg for msg in bootstrap_messages if msg.startswith("CLAUDE.md")), None)
    except Exception as e:
        _log(f"Client bootstrap migration error: {e}")

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
            version_json = _RESOLVED_REPO_DIR / "version.json"
            pkg_json = _RESOLVED_REPO_DIR / "package.json"
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
    ):
        if (NEXO_CODE.parent / "package.json").is_file():
            return NEXO_CODE, NEXO_CODE.parent
        if (NEXO_CODE / "package.json").is_file():
            return NEXO_CODE, NEXO_CODE

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


def _discover_runtime_root_python_modules(base_dir: Path) -> list[str]:
    """Return every top-level runtime `.py` module in the source/runtime root."""
    if not base_dir.is_dir():
        return []
    modules: list[str] = []
    for item in sorted(base_dir.iterdir(), key=lambda path: path.name):
        if not item.is_file() or item.suffix != ".py":
            continue
        if item.name.startswith(".") or item.name == "__init__.py":
            continue
        modules.append(item.name)
    return modules


def _runtime_flat_files(base_dir: Path) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in _discover_runtime_root_python_modules(base_dir) + ["requirements.txt", "package.json", "version.json"]:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _installed_scripts_classification(dest: Path) -> dict[str, str]:
    scripts_dest = dest / "scripts"
    if dest != NEXO_HOME or not scripts_dest.is_dir():
        return {}
    try:
        from script_registry import classify_scripts_dir

        entries = classify_scripts_dir().get("entries", [])
    except Exception as e:
        _log(f"script ownership inspection skipped: {e}")
        return {}

    ownership: dict[str, str] = {}
    for entry in entries:
        path_value = entry.get("path")
        classification = str(entry.get("classification", "") or "")
        if not path_value or not classification:
            continue
        ownership[Path(str(path_value)).name] = classification
    return ownership


def _backup_runtime_tree(dest: Path = NEXO_HOME) -> str:
    timestamp = time.strftime("%Y-%m-%d-%H%M%S")
    backup_dir = NEXO_HOME / "backups" / f"runtime-tree-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    code_dirs = [
        "hooks",
        "plugins",
        "db",
        "cognitive",
        "dashboard",
        "rules",
        "crons",
        "scripts",
        "doctor",
        "skills",
        "skills-core",
        "skills-runtime",
        "templates",
    ]
    flat_files = _runtime_flat_files(dest)
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
    # Opportunistic rotation: runtime-tree snapshots were accumulating forever
    # because nothing ever pruned them. Keep only the N most recent; failures
    # must never block the runtime-tree caller's rollback flow.
    try:
        _rotate_auto_update_backups("runtime-tree-")
    except Exception as e:
        _log(f"Backup rotation warning (runtime-tree): {e}")
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
    flat_files = _runtime_flat_files(src_dir)
    copied_packages = 0
    copied_files = 0
    copied_scripts = 0
    script_conflicts: list[dict[str, str]] = []
    installed_script_classes = _installed_scripts_classification(dest)

    for dirname in ("bin", "skills", "skills-core", "skills-runtime", "templates"):
        (dest / dirname).mkdir(parents=True, exist_ok=True)

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
                existing_class = installed_script_classes.get(item.name, "")
                if dst.exists() and existing_class in {"personal", "non-script"}:
                    script_conflicts.append(
                        {
                            "name": item.name,
                            "path": str(dst),
                            "classification": existing_class,
                            "reason": "existing runtime entry is not core-managed",
                        }
                    )
                    continue
                shutil.copy2(str(item), str(dst))
                if item.suffix == ".sh":
                    dst.chmod(0o755)
                copied_scripts += 1

    if script_conflicts:
        _emit_progress(
            progress_fn,
            f"Preserved {len(script_conflicts)} personal runtime script collision(s); core scripts were not overwritten.",
        )

    _emit_progress(progress_fn, "Copying templates and version metadata...")
    templates_src = repo_dir / "templates"
    templates_dest = dest / "templates"
    if templates_src.is_dir():
        templates_dest.mkdir(parents=True, exist_ok=True)
        for item in templates_src.iterdir():
            if item.name == "__pycache__":
                continue
            if item.is_file():
                shutil.copy2(str(item), str(templates_dest / item.name))
            elif item.is_dir():
                sub_dest = templates_dest / item.name
                sub_dest.mkdir(parents=True, exist_ok=True)
                for sub in item.iterdir():
                    if sub.is_file():
                        shutil.copy2(str(sub), str(sub_dest / sub.name))

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
        "scripts": copied_scripts,
        "script_conflicts": script_conflicts,
        "source": str(src_dir),
        "repo": str(repo_dir),
    }


def _reinstall_runtime_pip_deps(runtime_root: Path = NEXO_HOME) -> bool:
    req_file = runtime_root / "requirements.txt"
    if not req_file.exists():
        return True
    _ensure_runtime_venv(runtime_root)
    venv_pip = _venv_pip_path(runtime_root)
    if not venv_pip.exists() and sys.platform != "win32":
        alt_pip = runtime_root / ".venv" / "bin" / "pip3"
        if alt_pip.exists():
            venv_pip = alt_pip
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
        normalized_preferences = normalize_client_preferences(schedule_payload)
        if normalized_preferences != {
            key: schedule_payload.get(key)
            for key in normalized_preferences
        }:
            merged_schedule = dict(schedule_payload)
            merged_schedule.update(normalized_preferences)
            schedule_path.parent.mkdir(parents=True, exist_ok=True)
            schedule_path.write_text(json.dumps(merged_schedule, indent=2, ensure_ascii=False) + "\n")
        client_sync_result = sync_all_clients(
            nexo_home=dest,
            runtime_root=dest,
            preferences=normalized_preferences,
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
    db_backup_dir, backup_report = _create_validated_db_backup()
    if backup_report is not None and not backup_report["ok"]:
        return {
            "ok": False,
            "mode": "sync",
            "error": "DB backup validation failed before runtime sync.",
            "backup_dir": db_backup_dir,
        }
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
            "scripts": copy_stats.get("scripts", 0),
            "actions": actions,
            "warnings": [],
            "script_conflicts": copy_stats.get("script_conflicts", []),
            "source": copy_stats["source"],
            "repo": copy_stats["repo"],
        })
        if copy_stats.get("script_conflicts"):
            sync_result["actions"].append(f"preserved-personal-scripts:{len(copy_stats['script_conflicts'])}")
            sync_result["warnings"].append(
                f"Preserved {len(copy_stats['script_conflicts'])} personal runtime script collision(s) in NEXO_HOME/scripts"
            )
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
        "client_bootstrap_updates": [],
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
            bootstrap_messages = _sync_client_bootstraps()
            result["client_bootstrap_updates"] = bootstrap_messages
            result["claude_md_update"] = next((msg for msg in bootstrap_messages if msg.startswith("CLAUDE.md")), None)
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
