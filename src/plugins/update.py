from __future__ import annotations
"""Update plugin — pull latest code, backup DBs, run migrations, verify."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from runtime_home import export_resolved_nexo_home
from tree_hygiene import is_duplicate_artifact_name

# Code root is the parent of plugins/:
# - source checkout: <repo>/src
# - packaged runtime: <NEXO_HOME>
_THIS_DIR = Path(__file__).resolve().parent
CODE_ROOT = _THIS_DIR.parent
_REPO_CANDIDATE = CODE_ROOT.parent

NEXO_HOME = export_resolved_nexo_home()
DATA_DIR = NEXO_HOME / "data"
BACKUP_BASE = NEXO_HOME / "backups"

# In packaged installs, update.py lives at <NEXO_HOME>/plugins/update.py.
_PACKAGED_INSTALL = not (_REPO_CANDIDATE / ".git").exists() and not (_REPO_CANDIDATE / ".git").is_file()
REPO_DIR = CODE_ROOT if _PACKAGED_INSTALL else _REPO_CANDIDATE
SRC_DIR = CODE_ROOT
PACKAGE_JSON = REPO_DIR / "package.json"


def _venv_python_path(runtime_root: Path = NEXO_HOME) -> Path:
    if sys.platform == "win32":
        return runtime_root / ".venv" / "Scripts" / "python.exe"
    return runtime_root / ".venv" / "bin" / "python3"


def _venv_pip_path(runtime_root: Path = NEXO_HOME) -> Path:
    if sys.platform == "win32":
        return runtime_root / ".venv" / "Scripts" / "pip.exe"
    return runtime_root / ".venv" / "bin" / "pip"


def _ensure_managed_venv(runtime_root: Path = NEXO_HOME) -> str | None:
    venv_python = _venv_python_path(runtime_root)
    if venv_python.exists():
        return None
    try:
        runtime_root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(runtime_root / ".venv")],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        return f"venv creation error: {e}"
    if result.returncode != 0 or not venv_python.exists():
        return f"venv creation failed: {result.stderr or result.stdout}"
    return None


def _find_npm_pkg_src() -> Path | None:
    """Locate the nexo-brain npm package's src/ directory for requirements.txt."""
    try:
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            npm_src = Path(result.stdout.strip()) / "nexo-brain" / "src"
            if npm_src.is_dir():
                return npm_src
    except Exception:
        pass
    return None


def _core_artifact_source_dir() -> Path | None:
    """Return the canonical source directory for packaged core artifacts."""
    if _PACKAGED_INSTALL:
        return _find_npm_pkg_src()
    return SRC_DIR


def _is_git_repo() -> bool:
    """Check if REPO_DIR is a valid git repository."""
    return (REPO_DIR / ".git").exists() or (REPO_DIR / ".git").is_file()


def _refresh_installed_manifest():
    """Refresh packaged crons and persist the runtime core-artifacts manifest."""
    try:
        artifact_src = _core_artifact_source_dir()
        if artifact_src is None:
            return

        src_crons = artifact_src / "crons"
        dst_crons = NEXO_HOME / "crons"
        if src_crons.exists():
            dst_crons.mkdir(parents=True, exist_ok=True)
            for f in src_crons.iterdir():
                if f.is_file() and not is_duplicate_artifact_name(f):
                    dest = dst_crons / f.name
                    if _paths_match(f, dest):
                        continue
                    shutil.copy2(str(f), str(dest))
        config_dir = NEXO_HOME / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "script_names": sorted(
                f.name for f in (artifact_src / "scripts").iterdir()
                if f.is_file() and not is_duplicate_artifact_name(f)
            ) if (artifact_src / "scripts").is_dir() else [],
            "hook_names": sorted(
                f.name for f in (artifact_src / "hooks").iterdir()
                if f.is_file() and not is_duplicate_artifact_name(f)
            ) if (artifact_src / "hooks").is_dir() else [],
        }
        (config_dir / "runtime-core-artifacts.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
    except Exception:
        pass


def _cleanup_retired_runtime_files() -> list[str]:
    removed: list[str] = []
    retired_paths = [
        NEXO_HOME / "scripts" / "heartbeat-enforcement.py",
        NEXO_HOME / "scripts" / "heartbeat-posttool.sh",
        NEXO_HOME / "scripts" / "heartbeat-user-msg.sh",
        NEXO_HOME / "hooks" / "heartbeat-guard.sh",
    ]
    for path in retired_paths:
        if not path.exists():
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except Exception:
            continue
    return removed


def _read_version() -> str:
    """Read the installed/runtime version."""
    if _PACKAGED_INSTALL:
        # version.json is the runtime truth for packaged installs.
        try:
            version_file = NEXO_HOME / "version.json"
            if version_file.exists():
                return json.loads(version_file.read_text()).get("version", "unknown")
        except Exception:
            pass
        try:
            package_file = NEXO_HOME / "package.json"
            if package_file.exists():
                return json.loads(package_file.read_text()).get("version", "unknown")
        except Exception:
            pass

    try:
        if PACKAGE_JSON.exists():
            return json.loads(PACKAGE_JSON.read_text()).get("version", "unknown")
    except Exception:
        pass
    try:
        version_file = NEXO_HOME / "version.json"
        if version_file.exists():
            return json.loads(version_file.read_text()).get("version", "unknown")
    except Exception:
        pass
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


def _requirements_hash() -> str:
    """Return a content hash of requirements.txt, or empty string if missing."""
    import hashlib
    req_file = SRC_DIR / "requirements.txt"
    if not req_file.exists() and _PACKAGED_INSTALL:
        npm_src = _find_npm_pkg_src()
        if npm_src:
            req_file = npm_src / "requirements.txt"
    if req_file.exists():
        return hashlib.sha256(req_file.read_bytes()).hexdigest()
    return ""


def _check_dirty() -> str | None:
    """Return error message if worktree has uncommitted changes, else None."""
    if not _is_git_repo():
        return None  # Not a git repo, skip dirty check
    rc, out, _ = _git("status", "--porcelain")
    if rc != 0:
        return "Failed to check git status."
    if out:
        return f"Uncommitted changes:\n{out}\nCommit or stash before updating."
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
        src_conn = None
        dst_conn = None
        try:
            src_conn = sqlite3.connect(str(db_file))
            dst_conn = sqlite3.connect(str(dest))
            src_conn.backup(dst_conn)
        except Exception as e:
            return str(backup_dir), f"Failed to backup {db_file.name}: {e}"
        finally:
            for conn in (dst_conn, src_conn):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

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
                src_conn = None
                dst_conn = None
                try:
                    src_conn = sqlite3.connect(str(db_backup))
                    dst_conn = sqlite3.connect(str(candidate))
                    src_conn.backup(dst_conn)
                except Exception:
                    pass
                finally:
                    for conn in (dst_conn, src_conn):
                        if conn is not None:
                            try:
                                conn.close()
                            except Exception:
                                pass
                break


def _reinstall_pip_deps() -> str | None:
    """Reinstall Python dependencies from requirements.txt into the managed venv."""
    req_file = SRC_DIR / "requirements.txt"
    if not req_file.exists() and _PACKAGED_INSTALL:
        # In packaged mode, requirements.txt lives in the npm package's src/ dir
        npm_src = _find_npm_pkg_src()
        if npm_src:
            req_file = npm_src / "requirements.txt"
    if not req_file.exists():
        return None  # No requirements file, skip
    venv_error = _ensure_managed_venv(NEXO_HOME)
    if venv_error is not None:
        return venv_error
    venv_pip = _venv_pip_path(NEXO_HOME)
    if not venv_pip.exists() and sys.platform != "win32":
        alt_pip = NEXO_HOME / ".venv" / "bin" / "pip3"
        if alt_pip.exists():
            venv_pip = alt_pip
    if not venv_pip.exists():
        # No venv, try system pip with --break-system-packages
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(req_file), "--break-system-packages"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return f"pip install failed: {result.stderr or result.stdout}"
        except Exception as e:
            return f"pip install error: {e}"
        return None
    try:
        result = subprocess.run(
            [str(venv_pip), "install", "--quiet", "-r", str(req_file)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return f"pip install failed: {result.stderr or result.stdout}"
    except Exception as e:
        return f"pip install error: {e}"
    return None


def _read_runtime_dependencies() -> list[dict]:
    """Read runtimeDependencies from package.json."""
    for candidate in (PACKAGE_JSON, NEXO_HOME / "package.json"):
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text())
                deps = data.get("runtimeDependencies")
                if isinstance(deps, list):
                    return deps
        except Exception:
            continue
    # Fallback: check the npm-installed package's package.json
    npm_src = _find_npm_pkg_src()
    if npm_src:
        pkg = npm_src.parent / "package.json"
        try:
            if pkg.is_file():
                data = json.loads(pkg.read_text())
                deps = data.get("runtimeDependencies")
                if isinstance(deps, list):
                    return deps
        except Exception:
            pass
    return []


def _get_npm_global_version(package_name: str) -> str | None:
    """Return the currently installed global npm package version, or None."""
    try:
        result = subprocess.run(
            ["npm", "list", "-g", package_name, "--json", "--depth=0"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            deps = data.get("dependencies", {})
            info = deps.get(package_name)
            if info and isinstance(info, dict):
                return info.get("version")
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return None


def _get_npm_registry_version(package_name: str) -> str | None:
    """Return the latest version of a package from the npm registry."""
    try:
        result = subprocess.run(
            ["npm", "view", package_name, "version"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return None


def _update_runtime_dependencies(progress_fn=None) -> list[dict]:
    """Update all declared runtimeDependencies. Returns a list of result dicts.

    Each result dict contains:
        name: package name
        old_version: version before update (or None if not installed)
        new_version: version after update (or None on failure)
        status: "updated" | "already_latest" | "installed" | "failed" | "skipped"
        error: error message (only when status == "failed")
    """
    deps = _read_runtime_dependencies()
    if not deps:
        return []

    results = []
    for dep in deps:
        name = dep.get("name", "")
        dep_type = dep.get("type", "")
        optional = dep.get("optional", True)

        if not name or dep_type != "npm-global":
            results.append({
                "name": name or "(unknown)",
                "old_version": None,
                "new_version": None,
                "status": "skipped",
            })
            continue

        _emit_progress(progress_fn, f"Checking runtime dependency: {name}...")

        old_version = _get_npm_global_version(name)
        latest_version = _get_npm_registry_version(name)

        if old_version is None:
            # Not installed
            if optional:
                results.append({
                    "name": name,
                    "old_version": None,
                    "new_version": None,
                    "status": "skipped",
                })
                continue
            # Install it
            _emit_progress(progress_fn, f"Installing {name}...")
            try:
                r = subprocess.run(
                    ["npm", "install", "-g", name],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    new_version = _get_npm_global_version(name)
                    results.append({
                        "name": name,
                        "old_version": None,
                        "new_version": new_version,
                        "status": "installed",
                    })
                else:
                    results.append({
                        "name": name,
                        "old_version": None,
                        "new_version": None,
                        "status": "failed",
                        "error": r.stderr or r.stdout or "npm install failed",
                    })
            except subprocess.TimeoutExpired:
                results.append({
                    "name": name, "old_version": None, "new_version": None,
                    "status": "failed", "error": "npm install timed out (120s)",
                })
            except Exception as e:
                results.append({
                    "name": name, "old_version": None, "new_version": None,
                    "status": "failed", "error": str(e),
                })
            continue

        # Already installed — check if update needed
        if latest_version and old_version == latest_version:
            results.append({
                "name": name,
                "old_version": old_version,
                "new_version": old_version,
                "status": "already_latest",
            })
            continue

        # Update
        _emit_progress(progress_fn, f"Updating {name} {old_version} -> {latest_version or 'latest'}...")
        try:
            r = subprocess.run(
                ["npm", "update", "-g", name],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                new_version = _get_npm_global_version(name) or latest_version
                results.append({
                    "name": name,
                    "old_version": old_version,
                    "new_version": new_version,
                    "status": "updated" if new_version != old_version else "already_latest",
                })
            else:
                results.append({
                    "name": name,
                    "old_version": old_version,
                    "new_version": old_version,
                    "status": "failed",
                    "error": r.stderr or r.stdout or "npm update failed",
                })
        except subprocess.TimeoutExpired:
            results.append({
                "name": name, "old_version": old_version, "new_version": old_version,
                "status": "failed", "error": "npm update timed out (120s)",
            })
        except Exception as e:
            results.append({
                "name": name, "old_version": old_version, "new_version": old_version,
                "status": "failed", "error": str(e),
            })

    return results


def _format_dep_results(dep_results: list[dict]) -> list[str]:
    """Format runtime dependency results as human-readable lines."""
    lines = []
    for dep in dep_results:
        name = dep.get("name", "")
        status = dep.get("status", "")
        old_v = dep.get("old_version")
        new_v = dep.get("new_version")
        if status == "updated":
            lines.append(f"  Dependencies: {name} {old_v} -> {new_v}")
        elif status == "installed":
            lines.append(f"  Dependencies: {name} installed ({new_v})")
        elif status == "already_latest":
            lines.append(f"  Dependencies: {name} {old_v} (latest)")
        elif status == "failed":
            lines.append(f"  WARNING: {name} update failed: {dep.get('error', 'unknown')}")
    return lines


def _run_migrations() -> str | None:
    """Run init_db() to apply pending migrations. Returns error or None."""
    # In packaged mode, db/ lives in NEXO_HOME; in dev mode, in SRC_DIR
    cwd = str(NEXO_HOME) if _PACKAGED_INSTALL else str(SRC_DIR)
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import db; db.init_db()"],
            cwd=cwd,
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
    # In packaged mode, server.py lives in NEXO_HOME; in dev mode, in SRC_DIR
    cwd = str(NEXO_HOME) if _PACKAGED_INSTALL else str(SRC_DIR)
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return f"Import verification failed: {result.stderr or result.stdout}"
    except Exception as e:
        return f"Import verification error: {e}"
    return None


def _sync_hooks_to_home():
    """Copy hook scripts from src/hooks/ to NEXO_HOME/hooks/ after update."""
    import shutil
    hooks_src = SRC_DIR / "hooks"
    hooks_dest = NEXO_HOME / "hooks"
    if not hooks_src.is_dir():
        return
    hooks_dest.mkdir(parents=True, exist_ok=True)
    synced = 0
    for f in hooks_src.iterdir():
        if f.is_file() and f.suffix == ".sh" and not is_duplicate_artifact_name(f):
            dest = hooks_dest / f.name
            if not _paths_match(f, dest):
                shutil.copy2(str(f), str(dest))
            os.chmod(str(dest), 0o755)
            synced += 1
    if synced:
        print(f"[NEXO update] Synced {synced} hook(s) to {hooks_dest}", file=sys.stderr)


def _backup_code_tree() -> tuple[str | None, str | None]:
    """Snapshot NEXO_HOME code dirs before npm update. Returns (backup_dir, error)."""
    timestamp = time.strftime("%Y-%m-%d-%H%M%S")
    backup_dir = BACKUP_BASE / f"code-tree-{timestamp}"
    # Directories and flat files that postinstall copies into NEXO_HOME
    code_dirs = [
        "bin",
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
    code_files_glob = ["*.py", "requirements.txt", "package.json"]
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Backup directories
        for d in code_dirs:
            src = NEXO_HOME / d
            if src.is_dir():
                shutil.copytree(src, backup_dir / d, dirs_exist_ok=True)
        # Backup flat code files in NEXO_HOME root
        for pattern in code_files_glob:
            for f in NEXO_HOME.glob(pattern):
                if f.is_file():
                    shutil.copy2(f, backup_dir / f.name)
        # Backup version.json
        vf = NEXO_HOME / "version.json"
        if vf.is_file():
            shutil.copy2(vf, backup_dir / "version.json")
    except Exception as e:
        return None, f"Code tree backup failed: {e}"
    return str(backup_dir), None


def _restore_code_tree(backup_dir: str) -> str | None:
    """Restore NEXO_HOME code dirs from a backup snapshot. Returns error or None."""
    bdir = Path(backup_dir)
    if not bdir.is_dir():
        return f"Code tree backup dir not found: {backup_dir}"
    try:
        for item in bdir.iterdir():
            dest = NEXO_HOME / item.name
            if item.is_dir():
                if dest.is_dir():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            elif item.is_file():
                shutil.copy2(item, dest)
    except Exception as e:
        return f"Code tree restore failed: {e}"
    return None


def _normalize_preferences_for_client_sync() -> dict:
    from client_preferences import normalize_client_preferences
    from model_defaults import heal_runtime_profiles

    schedule_path = NEXO_HOME / "config" / "schedule.json"
    schedule_payload = json.loads(schedule_path.read_text()) if schedule_path.exists() else {}
    # Heal invalid models (e.g. Claude-family written into codex profile by
    # earlier buggy versions). Must run BEFORE normalize so the healed values
    # propagate into preferences and downstream client config files.
    existing_profiles = schedule_payload.get("client_runtime_profiles") or {}
    healed_profiles, _heal_messages = heal_runtime_profiles(existing_profiles)
    if _heal_messages:
        schedule_payload["client_runtime_profiles"] = healed_profiles
    normalized_preferences = normalize_client_preferences(schedule_payload)
    if normalized_preferences != {
        key: schedule_payload.get(key)
        for key in normalized_preferences
    }:
        merged_schedule = dict(schedule_payload)
        merged_schedule.update(normalized_preferences)
        schedule_path.parent.mkdir(parents=True, exist_ok=True)
        schedule_path.write_text(json.dumps(merged_schedule, indent=2, ensure_ascii=False) + "\n")
    return normalized_preferences


def _sync_packaged_clients() -> tuple[bool, str | None]:
    try:
        from client_sync import sync_all_clients
    except Exception as e:
        return False, f"client sync import failed: {e}"

    try:
        preferences = _normalize_preferences_for_client_sync()
        result = sync_all_clients(
            nexo_home=NEXO_HOME,
            runtime_root=NEXO_HOME,
            operator_name=os.environ.get("NEXO_NAME", ""),
            preferences=preferences,
            auto_install_missing_claude=True,
        )
    except Exception as e:
        return False, f"client sync failed: {e}"

    if result.get("ok"):
        return True, None

    clients = result.get("clients", {})
    failures = []
    for key, payload in clients.items():
        if payload.get("ok") or payload.get("skipped"):
            continue
        failures.append(f"{key}: {payload.get('error', 'unknown error')}")
    if not failures:
        failures.append("unknown client sync failure")
    return False, "; ".join(failures)


def _rollback_npm_package(target_version: str) -> str | None:
    """Rollback nexo-brain npm package to a specific version.

    Uses NEXO_SKIP_POSTINSTALL because we restore the code tree
    from our own pre-update backup — no need for postinstall migration.
    """
    try:
        result = subprocess.run(
            ["npm", "install", "-g", f"nexo-brain@{target_version}"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "NEXO_SKIP_POSTINSTALL": "1", "NEXO_HOME": str(NEXO_HOME)},
        )
        if result.returncode != 0:
            return f"npm rollback failed: {result.stderr or result.stdout}"
    except Exception as e:
        return f"npm rollback error: {e}"
    return None


def _emit_progress(progress_fn, message: str) -> None:
    if callable(progress_fn):
        try:
            progress_fn(message)
        except Exception:
            pass


def _paths_match(src: Path, dest: Path) -> bool:
    try:
        return src.exists() and dest.exists() and src.samefile(dest)
    except Exception:
        return False


def _sync_packaged_crons(progress_fn=None) -> tuple[bool, str | None]:
    sync_path = NEXO_HOME / "crons" / "sync.py"
    if not sync_path.is_file():
        _refresh_installed_manifest()
        return True, None
    try:
        _emit_progress(progress_fn, "Syncing core cron definitions...")
        result = subprocess.run(
            [sys.executable, str(sync_path)],
            cwd=str(NEXO_HOME),
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_HOME)},
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip() or "cron sync failed"
        _refresh_installed_manifest()
        return True, None
    except Exception as e:
        return False, f"cron sync error: {e}"


def _reload_launch_agents_after_bump() -> dict:
    result: dict = {
        "scanned": 0,
        "reloaded": 0,
        "skipped_missing": 0,
        "errors": [],
        "platform": sys.platform,
    }

    if sys.platform != "darwin":
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
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            load_proc = subprocess.run(
                ["launchctl", "load", "-w", str(plist)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if load_proc.returncode == 0:
                result["reloaded"] += 1
            else:
                result["errors"].append(
                    {
                        "plist": plist.name,
                        "stderr": (load_proc.stderr or load_proc.stdout or "load failed")[:300],
                    }
                )
        except subprocess.TimeoutExpired:
            result["errors"].append({"plist": plist.name, "stderr": "launchctl timeout"})
        except Exception as e:
            result["errors"].append({"plist": plist.name, "stderr": str(e)[:300]})

    return result


def _handle_packaged_update(progress_fn=None) -> str:
    """Update a packaged (npm) install — no git repo available."""
    old_version = _read_version()

    # 1. Backup databases BEFORE any changes
    _emit_progress(progress_fn, "Backing up runtime databases...")
    backup_dir, backup_err = _backup_databases()
    if backup_err:
        return f"ABORTED at backup: {backup_err}"

    # 2. Backup NEXO_HOME code tree BEFORE npm update
    #    postinstall copies hooks/core/plugins/scripts into NEXO_HOME,
    #    so we need a full snapshot to restore on failure.
    _emit_progress(progress_fn, "Backing up runtime files...")
    code_backup_dir, code_err = _backup_code_tree()
    if code_err:
        return f"ABORTED at code tree backup: {code_err}"

    # 3. Run npm update (postinstall.js will migrate NEXO_HOME in-place)
    try:
        _emit_progress(progress_fn, "Downloading and applying the latest npm package...")
        result = subprocess.run(
            ["npm", "update", "-g", "nexo-brain"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "NEXO_HOME": str(NEXO_HOME)},
        )
        if result.returncode != 0:
            # npm failed (including postinstall failures) — full rollback
            if backup_dir:
                _restore_databases(backup_dir)
            if code_backup_dir:
                _restore_code_tree(code_backup_dir)
                # Reinstall pip deps from restored old requirements.txt
                _reinstall_pip_deps()
            rollback_err = _rollback_npm_package(old_version)
            msg = f"ABORTED: npm update failed: {result.stderr or result.stdout}"
            if rollback_err:
                msg += f"\n  WARNING: npm rollback also failed: {rollback_err}"
                msg += f"\n  Manual rollback: npm install -g nexo-brain@{old_version}"
            return msg
    except FileNotFoundError:
        return "ABORTED: npm not found. Install Node.js to update packaged installs."
    except Exception as e:
        if backup_dir:
            _restore_databases(backup_dir)
        if code_backup_dir:
            _restore_code_tree(code_backup_dir)
            # Reinstall pip deps from restored old requirements.txt
            _reinstall_pip_deps()
        rollback_err = _rollback_npm_package(old_version)
        msg = f"ABORTED: npm update error: {e}"
        if rollback_err:
            msg += f"\n  WARNING: npm rollback also failed: {rollback_err}"
            msg += f"\n  Manual rollback: npm install -g nexo-brain@{old_version}"
        return msg

    new_version = _read_version()
    if old_version == new_version:
        return f"Already up to date (v{old_version}). No changes."

    # 4. Post-npm verification steps
    errors = []

    # Reinstall pip deps for new version
    _emit_progress(progress_fn, "Reconciling Python dependencies...")
    pip_err = _reinstall_pip_deps()
    if pip_err:
        errors.append(f"pip deps: {pip_err}")

    # Run migrations
    _emit_progress(progress_fn, "Running runtime migrations...")
    mig_err = _run_migrations()
    if mig_err:
        errors.append(f"migrations: {mig_err}")

    # Verify server can still import
    _emit_progress(progress_fn, "Verifying runtime import health...")
    verify_err = _verify_import()
    if verify_err:
        errors.append(f"verification: {verify_err}")

    hook_sync_warning = None
    cron_sync_warning = None
    retired_runtime_files: list[str] = []
    launchagent_reload_warning = None
    launchagent_reload_summary = None
    cron_sync_ok, cron_sync_error = _sync_packaged_crons(progress_fn=progress_fn)
    if not cron_sync_ok:
        errors.append(f"cron sync: {cron_sync_error}")
        cron_sync_warning = cron_sync_error
    try:
        _emit_progress(progress_fn, "Refreshing installed hooks and manifests...")
        _sync_hooks_to_home()
        retired_runtime_files = _cleanup_retired_runtime_files()
    except Exception as e:
        hook_sync_warning = f"{e}"

    # Update runtime dependencies (best-effort, never aborts)
    dep_results: list[dict] = []
    try:
        dep_results = _update_runtime_dependencies(progress_fn=progress_fn)
    except Exception:
        pass  # Non-critical

    client_sync_warning = None
    _emit_progress(progress_fn, "Refreshing shared client configs...")
    clients_ok, client_sync_error = _sync_packaged_clients()
    if not clients_ok:
        client_sync_warning = client_sync_error or "unknown client sync error"

    if old_version != new_version:
        _emit_progress(progress_fn, "Reloading LaunchAgents after version bump...")
        try:
            launchagent_reload_summary = _reload_launch_agents_after_bump()
            if launchagent_reload_summary.get("errors"):
                launchagent_reload_warning = (
                    f"reloaded {launchagent_reload_summary['reloaded']}/"
                    f"{launchagent_reload_summary['scanned']} with "
                    f"{len(launchagent_reload_summary['errors'])} error(s)"
                )
        except Exception as e:
            launchagent_reload_warning = f"launchagent reload error: {e}"

    if errors:
        # 5. Full rollback: restore code tree + DBs + pip deps + rollback npm package
        if code_backup_dir:
            tree_err = _restore_code_tree(code_backup_dir)
        else:
            tree_err = "no code tree backup available"
        if backup_dir:
            _restore_databases(backup_dir)
        # Reinstall pip deps from the restored (old) requirements.txt
        # so the venv matches the rolled-back code tree
        pip_rollback_err = _reinstall_pip_deps() if not tree_err else None
        rollback_err = _rollback_npm_package(old_version)
        lines = [f"UPDATE FAILED (packaged install, v{old_version} -> v{new_version})"]
        for err in errors:
            lines.append(f"  ERROR: {err}")
        lines.append(f"  Databases restored from: {backup_dir}")
        if tree_err:
            lines.append(f"  WARNING: code tree restore failed: {tree_err}")
        else:
            lines.append(f"  Code tree restored from: {code_backup_dir}")
        if pip_rollback_err:
            lines.append(f"  WARNING: pip deps rollback failed: {pip_rollback_err}")
        elif not tree_err:
            lines.append("  Python deps: reinstalled from old requirements.txt")
        if rollback_err:
            lines.append(f"  WARNING: npm rollback failed: {rollback_err}")
            lines.append(f"  Manual rollback: npm install -g nexo-brain@{old_version}")
        else:
            lines.append(f"  npm package rolled back to v{old_version}")
        lines.append("")
        lines.append("Fix the errors above, then run nexo_update again.")
        return "\n".join(lines)

    lines = ["UPDATE SUCCESSFUL (packaged install)"]
    lines.append(f"  Version: {old_version} -> {new_version}")
    lines.append(f"  Backup: {backup_dir}")
    if not cron_sync_warning:
        lines.append("  Crons: synced with manifest")
    else:
        lines.append(f"  WARNING: cron sync: {cron_sync_warning}")
    if not hook_sync_warning:
        lines.append("  Hooks: synced to NEXO_HOME")
    else:
        lines.append(f"  WARNING: hook sync: {hook_sync_warning}")
    if retired_runtime_files:
        lines.append(f"  Cleanup: removed {len(retired_runtime_files)} retired runtime file(s)")
    lines.extend(_format_dep_results(dep_results))
    if not client_sync_warning:
        lines.append("  Clients: configured client targets synced")
    else:
        lines.append(f"  WARNING: client sync: {client_sync_warning}")
    if launchagent_reload_summary and launchagent_reload_summary.get("scanned"):
        if not launchagent_reload_warning:
            lines.append(
                "  LaunchAgents: reloaded "
                f"{launchagent_reload_summary['reloaded']}/"
                f"{launchagent_reload_summary['scanned']}"
            )
        else:
            lines.append(f"  WARNING: launchagent reload: {launchagent_reload_warning}")
    lines.append("")
    lines.append("MCP server restart needed to load new code.")
    return "\n".join(lines)


def handle_update(remote: str = "origin", branch: str = "main", progress_fn=None) -> str:
    """Pull latest NEXO code, backup databases, run migrations, and verify.

    Supports both git checkouts and packaged (npm) installs.

    Full update flow (git):
    1. Check for uncommitted changes in entire worktree
    2. Backup all .db files
    3. git pull
    4. Reinstall Python dependencies if version changed
    5. Run migrations if version changed
    6. Verify server.py imports
    7. Rollback on failure (git reset --hard to saved commit)

    Args:
        remote: Git remote name (default: origin)
        branch: Git branch to pull (default: main)
    """
    # Packaged install — no git repo
    if not _is_git_repo():
        return _handle_packaged_update(progress_fn=progress_fn)

    steps_done = []
    old_commit = None
    backup_dir = None

    try:
        # Step 1: Check dirty (full worktree)
        _emit_progress(progress_fn, "Checking repository state...")
        dirty_err = _check_dirty()
        if dirty_err:
            return f"ABORTED: {dirty_err}"
        steps_done.append("clean-check")

        # Record current state
        old_version = _read_version()
        old_req_hash = _requirements_hash()
        rc, old_commit, _ = _git("rev-parse", "HEAD")
        if rc != 0:
            return "ABORTED: Not a git repository or git not available."

        # Step 2: Backup databases
        _emit_progress(progress_fn, "Backing up runtime databases...")
        backup_dir, backup_err = _backup_databases()
        if backup_err:
            return f"ABORTED at backup: {backup_err}"
        steps_done.append("backup")

        # Step 3: git pull
        _emit_progress(progress_fn, "Pulling latest source changes...")
        rc, pull_out, pull_err = _git("pull", remote, branch)
        if rc != 0:
            return f"ABORTED at git pull: {pull_err or pull_out}"
        steps_done.append("git-pull")

        # Step 4: Check version and dependency changes
        new_version = _read_version()
        version_changed = old_version != new_version
        new_req_hash = _requirements_hash()
        deps_changed = old_req_hash != new_req_hash

        # Step 5: Reinstall pip dependencies if requirements.txt changed
        if deps_changed or version_changed:
            _emit_progress(progress_fn, "Reconciling Python dependencies...")
            pip_err = _reinstall_pip_deps()
            if pip_err:
                raise RuntimeError(f"Pip install failed: {pip_err}")
            steps_done.append("pip-deps")

        # Step 6: Run migrations if version changed
        if version_changed:
            _emit_progress(progress_fn, "Running runtime migrations...")
            mig_err = _run_migrations()
            if mig_err:
                raise RuntimeError(f"Migration failed: {mig_err}")
            steps_done.append("migrations")

        # Step 7: Verify import
        _emit_progress(progress_fn, "Verifying runtime import health...")
        verify_err = _verify_import()
        if verify_err:
            raise RuntimeError(f"Verification failed: {verify_err}")
        steps_done.append("verify")

        # Step 8: Sync crons with manifest
        cron_sync_result = ""
        try:
            cron_sync_path = SRC_DIR / "crons" / "sync.py"
            if cron_sync_path.exists():
                _emit_progress(progress_fn, "Syncing core cron definitions...")
                r = subprocess.run(
                    [sys.executable, str(cron_sync_path)],
                    capture_output=True, text=True, timeout=30,
                    env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(SRC_DIR)},
                )
                cron_sync_result = r.stdout.strip()
                if r.returncode == 0:
                    steps_done.append("cron-sync")
                    # Refresh installed manifest only after successful sync
                    _refresh_installed_manifest()
                else:
                    cron_sync_result = f"Cron sync failed (exit {r.returncode}): {r.stderr or r.stdout}"
        except Exception as e:
            cron_sync_result = f"Cron sync warning: {e}"

        # Step 9: Sync hooks to NEXO_HOME
        retired_runtime_files: list[str] = []
        try:
            _emit_progress(progress_fn, "Syncing core Claude hooks...")
            _sync_hooks_to_home()
            retired_runtime_files = _cleanup_retired_runtime_files()
            steps_done.append("hook-sync")
        except Exception as e:
            pass  # Non-critical, log in function

        # Step 10: Update runtime dependencies (best-effort, never aborts)
        dep_results: list[dict] = []
        try:
            dep_results = _update_runtime_dependencies(progress_fn=progress_fn)
            if dep_results:
                steps_done.append("runtime-deps")
        except Exception:
            pass  # Non-critical

        # Step 11: Sync shared client configs
        try:
            _emit_progress(progress_fn, "Refreshing shared client configs...")
            from client_sync import sync_all_clients
            from client_preferences import normalize_client_preferences
            from model_defaults import heal_runtime_profiles

            schedule_path = NEXO_HOME / "config" / "schedule.json"
            schedule_payload = json.loads(schedule_path.read_text()) if schedule_path.exists() else {}
            # Heal Claude-family models written into Codex profile by earlier
            # buggy versions.  Must run BEFORE normalize so healed values
            # propagate into the saved preferences.
            existing_profiles = schedule_payload.get("client_runtime_profiles") or {}
            healed_profiles, heal_messages = heal_runtime_profiles(existing_profiles)
            if heal_messages:
                schedule_payload["client_runtime_profiles"] = healed_profiles
                for msg in heal_messages:
                    _emit_progress(progress_fn, msg)
                    steps_done.append("model-heal")
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
                nexo_home=NEXO_HOME,
                runtime_root=SRC_DIR,
                operator_name=os.environ.get("NEXO_NAME", ""),
                preferences=normalized_preferences,
            )
            if client_sync_result.get("ok"):
                steps_done.append("client-sync")
        except Exception:
            pass  # Non-critical, configs can be re-synced later

        # Build result
        dep_summary_lines = _format_dep_results(dep_results)
        if pull_out == "Already up to date.":
            msg = f"Already up to date (v{old_version}). No changes pulled."
            if dep_summary_lines:
                msg += "\n" + "\n".join(dep_summary_lines)
            return msg

        lines = ["UPDATE SUCCESSFUL"]
        if version_changed:
            lines.append(f"  Version: {old_version} -> {new_version}")
        else:
            lines.append(f"  Version: {old_version} (unchanged)")
        lines.append(f"  Branch: {remote}/{branch}")
        lines.append(f"  Backup: {backup_dir}")
        if "pip-deps" in steps_done:
            lines.append("  Python deps: reinstalled")
        if version_changed:
            lines.append("  Migrations: applied")
        if "cron-sync" in steps_done:
            lines.append("  Crons: synced with manifest")
        if "hook-sync" in steps_done:
            lines.append("  Hooks: synced to NEXO_HOME")
        if retired_runtime_files:
            lines.append(f"  Cleanup: removed {len(retired_runtime_files)} retired runtime file(s)")
        lines.extend(dep_summary_lines)
        if "client-sync" in steps_done:
            lines.append("  Clients: configured client targets synced")
        lines.append("")
        lines.append("MCP server restart needed to load new code.")
        return "\n".join(lines)

    except Exception as e:
        # Rollback — use git checkout to saved commit (safer than reset --hard)
        rollback_lines = [f"UPDATE FAILED: {e}", "", "Rolling back..."]

        if old_commit and "git-pull" in steps_done:
            # Full rollback: reset HEAD + index + worktree to old commit
            rc, _, err = _git("reset", "--hard", old_commit)
            if rc == 0:
                rollback_lines.append(f"  Git: restored files to {old_commit[:8]}")
                # Reinstall pip deps from the restored old requirements.txt
                # so the venv matches the rolled-back code
                if "pip-deps" in steps_done:
                    pip_rb_err = _reinstall_pip_deps()
                    if pip_rb_err:
                        rollback_lines.append(f"  WARNING: pip deps rollback failed: {pip_rb_err}")
                    else:
                        rollback_lines.append("  Python deps: reinstalled from old requirements.txt")
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
