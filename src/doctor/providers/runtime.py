"""Runtime tier checks — read-only health checks from existing artifacts. Target <5s."""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import plistlib
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from client_preferences import (
    detect_installed_clients,
    normalize_client_preferences,
    resolve_client_runtime_profile,
)
from cron_recovery import resolve_declared_schedule, should_run_at_load
from doctor.models import DoctorCheck, safe_check

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PROTECTED_MACOS_ROOTS = (
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Library" / "Mobile Documents",
)

# Freshness thresholds in seconds
IMMUNE_FRESHNESS = 3600  # 1 hour (runs every 30 min)
WATCHDOG_FRESHNESS = 3600  # 1 hour (runs every 30 min)
DEFAULT_CRON_THRESHOLD = 7200  # Fallback when manifest data is unavailable
AUXILIARY_CORE_LAUNCHAGENT_IDS = {"dashboard", "prevent-sleep", "tcc-approve"}
SPECIAL_LAUNCHAGENT_IDS = {"prevent-sleep", "tcc-approve"}
SPECIAL_ENV_NORMALIZE_IDS = SPECIAL_LAUNCHAGENT_IDS
OPTIONALS_FILE = NEXO_HOME / "config" / "optionals.json"
SCHEDULE_FILE = NEXO_HOME / "config" / "schedule.json"
PACKAGE_JSON = NEXO_CODE / "package.json"
CHANGELOG_FILE = NEXO_CODE / "CHANGELOG.md"


def _recorded_source_root() -> Path | None:
    version_file = NEXO_HOME / "version.json"
    try:
        payload = json.loads(version_file.read_text())
    except Exception:
        return None
    source = payload.get("source")
    if not source:
        return None
    candidate = Path(str(source)).expanduser()
    if (candidate / "package.json").is_file() and (candidate / "CHANGELOG.md").is_file():
        return candidate
    return None


def _release_root() -> Path:
    source_root = _recorded_source_root()
    candidates = [
        source_root,
        PACKAGE_JSON.parent,
        CHANGELOG_FILE.parent,
        NEXO_CODE,
        NEXO_CODE.parent,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        candidate = Path(candidate)
        if (candidate / "package.json").is_file() and (candidate / "CHANGELOG.md").is_file():
            return candidate
    return Path(NEXO_CODE)


def _package_json_path() -> Path:
    if PACKAGE_JSON.is_file():
        return PACKAGE_JSON
    return _release_root() / "package.json"


def _changelog_path() -> Path:
    if CHANGELOG_FILE.is_file():
        return CHANGELOG_FILE
    return _release_root() / "CHANGELOG.md"


def _codex_bootstrap_config_status() -> dict:
    path = Path.home() / ".codex" / "config.toml"
    if not path.is_file():
        return {"exists": False, "path": str(path), "bootstrap_managed": False}
    try:
        payload = tomllib.loads(path.read_text())
    except Exception as exc:
        return {
            "exists": True,
            "path": str(path),
            "bootstrap_managed": False,
            "error": str(exc),
        }
    managed = bool(payload.get("nexo", {}).get("codex", {}).get("bootstrap_managed"))
    mcp_managed = bool(payload.get("nexo", {}).get("codex", {}).get("mcp_managed"))
    initial_messages = payload.get("initial_messages", [])
    has_initial_messages = bool(initial_messages)
    mcp_server = payload.get("mcp_servers", {}).get("nexo", {})
    return {
        "exists": True,
        "path": str(path),
        "bootstrap_managed": managed,
        "mcp_managed": mcp_managed,
        "has_initial_messages": has_initial_messages,
        "model": str(payload.get("model", "") or ""),
        "reasoning_effort": str(payload.get("model_reasoning_effort", "") or ""),
        "has_mcp_server": isinstance(mcp_server, dict) and bool(mcp_server.get("command")) and bool(mcp_server.get("args")),
        "mcp_runtime_home": str((mcp_server.get("env") or {}).get("NEXO_HOME", "") or ""),
        "mcp_runtime_root": str((mcp_server.get("env") or {}).get("NEXO_CODE", "") or ""),
    }


def _claude_desktop_shared_brain_status() -> dict:
    path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if not path.is_file():
        return {"exists": False, "path": str(path), "shared_brain_managed": False}
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return {
            "exists": True,
            "path": str(path),
            "shared_brain_managed": False,
            "error": str(exc),
        }
    mcp_server = (payload.get("mcpServers") or {}).get("nexo", {})
    metadata = ((payload.get("nexo") or {}).get("claude_desktop") or {})
    return {
        "exists": True,
        "path": str(path),
        "has_mcp_server": isinstance(mcp_server, dict) and bool(mcp_server.get("command")) and bool(mcp_server.get("args")),
        "shared_brain_managed": bool(metadata.get("shared_brain_managed")),
        "shared_brain_mode": str(metadata.get("shared_brain_mode", "") or ""),
        "managed_runtime_home": str(metadata.get("managed_runtime_home", "") or ""),
        "managed_runtime_root": str(metadata.get("managed_runtime_root", "") or ""),
    }


def _recent_codex_session_parity_status(*, days: int = 7, max_files: int = 24) -> dict:
    roots = [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    ]
    cutoff = time.time() - (days * 86400)
    candidates: list[tuple[float, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                candidates.append((mtime, path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    files = [path for _, path in candidates[:max_files]]

    status = {
        "files": len(files),
        "bootstrap_sessions": 0,
        "startup_sessions": 0,
        "heartbeat_sessions": 0,
        "origins": set(),
        "samples": [],
    }
    for path in files:
        saw_bootstrap = False
        saw_startup = False
        saw_heartbeat = False
        origin = ""
        try:
            with path.open() as fh:
                for raw in fh:
                    if (
                        not saw_bootstrap
                        and (
                            "NEXO Shared Brain for Codex" in raw
                            or "<!-- nexo-codex-agents-version:" in raw
                            or "You are NEXO" in raw
                        )
                    ):
                        saw_bootstrap = True
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    payload = event.get("payload", {})
                    if event.get("type") == "session_meta" and isinstance(payload, dict):
                        origin = str(payload.get("originator", "") or payload.get("source", "") or "")
                    if event.get("type") != "response_item" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "function_call":
                        continue
                    name = str(payload.get("name", "") or "")
                    if name in {"mcp__nexo__nexo_startup", "nexo_startup"}:
                        saw_startup = True
                    elif name in {"mcp__nexo__nexo_heartbeat", "nexo_heartbeat"}:
                        saw_heartbeat = True
                    if saw_bootstrap and saw_startup and saw_heartbeat and origin:
                        break
        except Exception:
            continue
        if origin:
            status["origins"].add(origin)
        if saw_bootstrap:
            status["bootstrap_sessions"] += 1
        if saw_startup:
            status["startup_sessions"] += 1
        if saw_heartbeat:
            status["heartbeat_sessions"] += 1
        status["samples"].append(
            {
                "file": str(path),
                "bootstrap": saw_bootstrap,
                "startup": saw_startup,
                "heartbeat": saw_heartbeat,
                "origin": origin,
            }
        )
    status["origins"] = sorted(status["origins"])
    return status


def _normalize_path_token(value: str) -> str:
    return str(value or "").replace("\\", "/").rstrip("/").lower()


def _split_applies_to(applies_to: str) -> list[str]:
    return [item.strip() for item in str(applies_to or "").split(",") if item.strip()]


def _applies_to_matches_file(applies_to: str, filepath: str) -> bool:
    file_path = Path(filepath)
    file_norm = _normalize_path_token(str(file_path))
    parent_norm = _normalize_path_token(str(file_path.parent))
    filename = file_path.name.lower()
    stem = file_path.stem.lower()
    parent_name = file_path.parent.name.lower()

    for raw in _split_applies_to(applies_to):
        token_norm = _normalize_path_token(raw)
        if not token_norm:
            continue
        if "/" in token_norm:
            if (
                file_norm == token_norm
                or file_norm.endswith(f"/{token_norm}")
                or file_norm.startswith(f"{token_norm}/")
                or parent_norm == token_norm
                or parent_norm.endswith(f"/{token_norm}")
            ):
                return True
            continue
        if token_norm in {filename, stem, parent_name}:
            return True
    return False


def _parse_jsonish_arguments(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_candidate_path(token: str, cwd: str) -> str:
    token = str(token or "").strip()
    if not token:
        return ""
    if token.startswith("~"):
        token = str(Path(token).expanduser())
    path = Path(token)
    if not path.is_absolute():
        if not cwd.strip():
            return ""
        path = Path(cwd).expanduser() / path
    return str(path.resolve())


def _extract_shell_file_candidates(command: str, cwd: str) -> list[str]:
    if not command.strip():
        return []
    try:
        tokens = shlex.split(command)
    except Exception:
        tokens = command.split()

    candidates: list[str] = []
    seen = set()
    shell_noise = {"&&", "||", "|", ";", ">", ">>", "<", "<<<"}
    suffixes = {
        ".py", ".md", ".json", ".jsonl", ".sh", ".txt", ".toml", ".yaml", ".yml",
        ".js", ".ts", ".tsx", ".jsx", ".php", ".sql", ".rs", ".go", ".c", ".cpp",
        ".h", ".css", ".html",
    }
    for token in tokens:
        if token in shell_noise or token.startswith("-"):
            continue
        if not token.startswith(("/", "~", ".")) and "/" not in token and Path(token).suffix.lower() not in suffixes:
            continue
        resolved = _resolve_candidate_path(token, cwd)
        normalized = _normalize_path_token(resolved)
        if resolved and normalized not in seen:
            seen.add(normalized)
            candidates.append(resolved)
    return candidates


def _classify_shell_operation(command: str) -> str:
    if not command.strip():
        return "read"
    try:
        tokens = shlex.split(command)
    except Exception:
        tokens = command.split()
    if not tokens:
        return "read"
    base = Path(tokens[0]).name.lower()
    if base in {"rm", "unlink", "rmdir"}:
        return "delete"
    if base in {"mv", "cp", "touch", "install"}:
        return "write"
    if base == "sed" and "-i" in tokens:
        return "write"
    if base == "perl" and any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
        return "write"
    return "read"


def _extract_shell_file_touches(command: str, cwd: str) -> list[tuple[str, str]]:
    operation = _classify_shell_operation(command)
    return [(candidate, operation) for candidate in _extract_shell_file_candidates(command, cwd)]


def _extract_apply_patch_targets(patch_text: str, cwd: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen = set()
    for raw_line in str(patch_text or "").splitlines():
        line = raw_line.strip()
        prefix = None
        operation = "write"
        if line.startswith("*** Update File: "):
            prefix = "*** Update File: "
        elif line.startswith("*** Add File: "):
            prefix = "*** Add File: "
        elif line.startswith("*** Delete File: "):
            prefix = "*** Delete File: "
            operation = "delete"
        if not prefix:
            continue
        resolved = _resolve_candidate_path(line[len(prefix):].strip(), cwd)
        normalized = _normalize_path_token(resolved)
        if resolved and normalized not in seen:
            seen.add(normalized)
            targets.append((resolved, operation))
    return targets


def _extract_declared_file_targets(args: dict, cwd: str) -> set[str]:
    raw_items: list[str] = []
    for key in ("files", "paths", "file_paths"):
        value = args.get(key)
        if isinstance(value, str):
            raw_items.extend(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, list):
            raw_items.extend(str(item).strip() for item in value if str(item).strip())
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            raw_items.append(value.strip())
    resolved = set()
    for item in raw_items:
        candidate = _resolve_candidate_path(item, cwd)
        if candidate:
            resolved.add(_normalize_path_token(candidate))
    return resolved


def _load_active_conditioned_learnings() -> list[dict]:
    db_path = NEXO_HOME / "data" / "nexo.db"
    if not db_path.is_file():
        return []
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='learnings'"
            ).fetchone()
            if not table:
                return []
            rows = conn.execute(
                """SELECT id, title, applies_to
                   FROM learnings
                   WHERE status = 'active' AND COALESCE(applies_to, '') != ''
                   ORDER BY updated_at DESC, id DESC"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _recent_codex_conditioned_file_discipline_status(*, days: int = 7, max_files: int = 24) -> dict:
    conditioned = _load_active_conditioned_learnings()
    status = {
        "files": 0,
        "conditioned_rules": len(conditioned),
        "conditioned_sessions": 0,
        "conditioned_touches": 0,
        "read_without_protocol": 0,
        "write_without_protocol": 0,
        "write_without_guard_ack": 0,
        "delete_without_protocol": 0,
        "delete_without_guard_ack": 0,
        "latest_violation_age_seconds": None,
        "samples": [],
    }
    if not conditioned:
        return status

    roots = [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    ]
    cutoff = time.time() - (days * 86400)
    candidates: list[tuple[float, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                candidates.append((mtime, path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    files = candidates[:max_files]
    status["files"] = len(files)

    for file_mtime, path in files:
        cwd = ""
        protocol_active = False
        protocol_files: set[str] = set()
        guard_files: set[str] = set()
        guard_ack = False
        session_touches = 0
        session_samples: list[dict] = []

        try:
            with path.open() as fh:
                for raw in fh:
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    event_age_seconds = None
                    event_ts = _parse_timestamp(str(event.get("timestamp", "") or ""))
                    if event_ts is not None:
                        event_age_seconds = max(0.0, time.time() - event_ts.timestamp())
                    payload = event.get("payload", {})
                    if event.get("type") == "session_meta" and isinstance(payload, dict):
                        cwd = str(payload.get("cwd", "") or "")
                        continue
                    if event.get("type") != "response_item" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "function_call":
                        continue

                    name = str(payload.get("name", "") or "")
                    args = _parse_jsonish_arguments(payload.get("arguments"))

                    if name in {"mcp__nexo__nexo_task_open", "nexo_task_open"}:
                        protocol_active = True
                        protocol_files.update(_extract_declared_file_targets(args, cwd))
                        continue
                    if name in {
                        "mcp__nexo__nexo_guard_check",
                        "nexo_guard_check",
                        "mcp__nexo__nexo_guard_file_check",
                        "nexo_guard_file_check",
                    }:
                        guard_files.update(_extract_declared_file_targets(args, cwd))
                        continue
                    if name in {"mcp__nexo__nexo_task_acknowledge_guard", "nexo_task_acknowledge_guard"}:
                        guard_ack = True
                        continue

                    touched_files: list[tuple[str, str]] = []
                    if name in {"exec_command", "functions.exec_command"}:
                        touched_files = _extract_shell_file_touches(str(args.get("cmd", "") or ""), cwd)
                    elif name in {"apply_patch", "functions.apply_patch"}:
                        patch_text = payload.get("arguments", "")
                        touched_files = _extract_apply_patch_targets(str(patch_text or ""), cwd)

                    if not touched_files:
                        continue

                    for touched, operation in touched_files:
                        matches = [row for row in conditioned if _applies_to_matches_file(str(row.get("applies_to", "")), touched)]
                        if not matches:
                            continue
                        session_touches += 1
                        status["conditioned_touches"] += 1
                        normalized = _normalize_path_token(touched)
                        has_protocol = protocol_active or normalized in protocol_files
                        has_guard_review = normalized in guard_files
                        if operation == "read":
                            if not has_protocol and not has_guard_review:
                                status["read_without_protocol"] += 1
                                age_seconds = (
                                    event_age_seconds
                                    if event_age_seconds is not None
                                    else max(0.0, time.time() - float(file_mtime))
                                )
                                current_latest = status.get("latest_violation_age_seconds")
                                if current_latest is None or age_seconds < float(current_latest):
                                    status["latest_violation_age_seconds"] = round(age_seconds, 1)
                                session_samples.append(
                                    {"kind": "read_without_protocol", "file": touched, "tool": name}
                                )
                        elif operation in {"write", "delete"}:
                            if not has_protocol and not has_guard_review:
                                status["write_without_protocol"] += 1
                                if operation == "delete":
                                    status["delete_without_protocol"] += 1
                                age_seconds = (
                                    event_age_seconds
                                    if event_age_seconds is not None
                                    else max(0.0, time.time() - float(file_mtime))
                                )
                                current_latest = status.get("latest_violation_age_seconds")
                                if current_latest is None or age_seconds < float(current_latest):
                                    status["latest_violation_age_seconds"] = round(age_seconds, 1)
                                session_samples.append(
                                    {
                                        "kind": f"{operation}_without_protocol",
                                        "file": touched,
                                        "tool": name,
                                    }
                                )
                            elif not guard_ack:
                                status["write_without_guard_ack"] += 1
                                if operation == "delete":
                                    status["delete_without_guard_ack"] += 1
                                age_seconds = (
                                    event_age_seconds
                                    if event_age_seconds is not None
                                    else max(0.0, time.time() - float(file_mtime))
                                )
                                current_latest = status.get("latest_violation_age_seconds")
                                if current_latest is None or age_seconds < float(current_latest):
                                    status["latest_violation_age_seconds"] = round(age_seconds, 1)
                                session_samples.append(
                                    {
                                        "kind": f"{operation}_without_guard_ack",
                                        "file": touched,
                                        "tool": name,
                                    }
                                )
        except Exception:
            continue

        if session_touches:
            status["conditioned_sessions"] += 1
            for sample in session_samples[:3]:
                if len(status["samples"]) >= 6:
                    break
                status["samples"].append({"session_file": str(path), **sample})

    return status


def _open_protocol_debt_summary(*debt_types: str) -> dict:
    db_path = NEXO_HOME / "data" / "nexo.db"
    summary = {"available": False, "open_total": 0, "counts": {}}
    if not db_path.is_file() or not debt_types:
        return summary

    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='protocol_debt'"
            ).fetchone()
            if not table:
                return summary
            placeholders = ",".join("?" for _ in debt_types)
            rows = conn.execute(
                f"""SELECT debt_type, COUNT(*) AS total
                    FROM protocol_debt
                    WHERE status = 'open' AND debt_type IN ({placeholders})
                    GROUP BY debt_type""",
                tuple(debt_types),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return summary

    counts = {str(row["debt_type"]): int(row["total"] or 0) for row in rows}
    summary["available"] = True
    summary["counts"] = counts
    summary["open_total"] = sum(counts.values())
    return summary


def _client_assumption_regressions() -> list[str]:
    src_root = NEXO_CODE if (NEXO_CODE / "server.py").is_file() else (NEXO_CODE / "src")
    if not src_root.is_dir():
        return []
    src_root = src_root.resolve()
    backup_root = (NEXO_HOME / "backups").resolve()
    contrib_root = (NEXO_HOME / "contrib").resolve()
    allowed_relative_paths = {
        Path("scripts") / "deep-sleep" / "collect.py",
        Path("doctor") / "providers" / "runtime.py",
    }
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        try:
            text = path.read_text()
        except Exception:
            continue
        resolved = path.resolve()
        try:
            if resolved.is_relative_to(backup_root):
                continue
        except Exception:
            pass
        try:
            if resolved.is_relative_to(contrib_root):
                continue
        except Exception:
            pass
        try:
            relative_path = resolved.relative_to(src_root)
        except Exception:
            relative_path = path.relative_to(src_root)
        if ".claude/projects" in text and relative_path not in allowed_relative_paths:
            offenders.append(f"{relative_path} hardcodes ~/.claude/projects")
    collect_path = src_root / "scripts" / "deep-sleep" / "collect.py"
    try:
        collect_text = collect_path.read_text()
    except Exception:
        collect_text = ""
    if collect_text and (".claude/projects" in collect_text) and (".codex" not in collect_text or "find_codex_session_files" not in collect_text):
        offenders.append("deep-sleep/collect.py references Claude transcripts without Codex transcript parity")
    return offenders


def _file_age_seconds(path: Path) -> float | None:
    """Return file age in seconds, or None if not found."""
    try:
        if path.is_file():
            return time.time() - path.stat().st_mtime
    except Exception:
        pass
    return None


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _latest_periodic_summary(kind: str) -> dict | None:
    pattern = f"*-{kind}-summary.json"
    candidates: list[tuple[str, Path]] = []
    for path in (NEXO_HOME / "operations" / "deep-sleep").glob(pattern):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        label = str(payload.get("label", "") or "")
        if label:
            candidates.append((label, path))
    if not candidates:
        return None
    _, path = sorted(candidates, key=lambda item: item[0])[-1]
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _package_version() -> str:
    try:
        payload = json.loads(_package_json_path().read_text())
    except Exception:
        return ""
    return str(payload.get("version", "") or "").strip()


def _top_changelog_version() -> str:
    try:
        text = _changelog_path().read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r"^## \[([^\]]+)\]", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _count_checks(checks) -> int:
    if isinstance(checks, list):
        return len(checks)
    if isinstance(checks, dict):
        total = 0
        for value in checks.values():
            if isinstance(value, list):
                total += len(value)
            elif value:
                total += 1
        return total
    return 0


def _parse_timestamp(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _enabled_optionals() -> dict[str, bool]:
    try:
        if OPTIONALS_FILE.is_file():
            data = json.loads(OPTIONALS_FILE.read_text())
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _enabled_manifest_crons() -> list[dict]:
    manifest_candidates = [
        NEXO_HOME / "crons" / "manifest.json",
        NEXO_CODE / "crons" / "manifest.json",
    ]
    optionals = _enabled_optionals()
    automation_default = True
    try:
        if SCHEDULE_FILE.is_file():
            schedule = _load_json(SCHEDULE_FILE)
            if isinstance(schedule, dict):
                automation_default = bool(schedule.get("automation_enabled", True))
    except Exception:
        pass
    for manifest_path in manifest_candidates:
        if not manifest_path.is_file():
            continue
        try:
            data = _load_json(manifest_path)
        except Exception:
            continue

        enabled = []
        for cron in data.get("crons", []):
            cron_id = cron.get("id")
            if not cron_id:
                continue
            optional_key = cron.get("optional")
            if optional_key == "automation":
                optional_enabled = optionals.get(optional_key, automation_default)
            else:
                optional_enabled = optionals.get(optional_key, False)
            if optional_key and not optional_enabled:
                continue
            enabled.append(cron)
        return enabled
    return []


def _cron_expectations() -> dict[str, dict]:
    expectations = {}
    for cron in _enabled_manifest_crons():
        cron_id = cron.get("id")
        if not cron_id or cron.get("keep_alive"):
            continue
        if cron.get("run_at_load") and not cron.get("interval_seconds") and not cron.get("schedule"):
            continue

        interval_seconds = cron.get("interval_seconds")
        schedule = cron.get("schedule") or {}
        if interval_seconds:
            threshold = max(int(interval_seconds) * 3, int(interval_seconds) + 600)
            label = f"every {int(interval_seconds) // 60}m"
        elif "weekday" in schedule:
            threshold = 8 * 86400
            label = "weekly"
        elif "hour" in schedule and "minute" in schedule:
            threshold = 36 * 3600
            label = "daily"
        else:
            threshold = DEFAULT_CRON_THRESHOLD
            label = "custom"

        expectations[cron_id] = {"threshold": threshold, "label": label}
    return expectations


def _run_at_load_cron_ids() -> set[str]:
    ids: set[str] = set()
    for cron_id, expected in _launchagent_schedule_expectations().items():
        if expected.get("RunAtLoad") is not True:
            continue
        if expected.get("StartInterval") or expected.get("StartCalendarInterval") or expected.get("KeepAlive"):
            continue
        ids.add(cron_id)
    return ids


def _launchagent_schedule_expectations() -> dict[str, dict]:
    expectations = {}
    for cron in _enabled_manifest_crons():
        cron_id = cron.get("id")
        if not cron_id:
            continue

        expected = {
            "StartInterval": None,
            "StartCalendarInterval": None,
            "RunAtLoad": None,
            "KeepAlive": None,
            "schedule_configured": False,
        }
        if cron.get("keep_alive"):
            expected["RunAtLoad"] = True
            expected["KeepAlive"] = True
            expected["schedule_configured"] = True
        elif "interval_seconds" in cron:
            expected["StartInterval"] = int(cron["interval_seconds"])
            expected["RunAtLoad"] = True if should_run_at_load(cron) else None
            expected["schedule_configured"] = True
        elif "schedule" in cron:
            schedule = resolve_declared_schedule(cron)
            cal = {}
            if "hour" in schedule:
                cal["Hour"] = schedule["hour"]
            if "minute" in schedule:
                cal["Minute"] = schedule["minute"]
            if "weekday" in schedule:
                cal["Weekday"] = schedule["weekday"]
            expected["StartCalendarInterval"] = cal
            expected["RunAtLoad"] = True if should_run_at_load(cron) else None
            expected["schedule_configured"] = True
        elif should_run_at_load(cron):
            expected["RunAtLoad"] = True
            expected["schedule_configured"] = True
        expectations[cron_id] = expected
    return expectations


def _managed_launchagent_plists() -> list[tuple[str, Path]]:
    ids = set(AUXILIARY_CORE_LAUNCHAGENT_IDS)
    for cron_id, expected in _launchagent_schedule_expectations().items():
        if expected.get("schedule_configured"):
            ids.add(cron_id)

    plists = []
    for cron_id in sorted(ids):
        plist_path = LAUNCH_AGENTS_DIR / f"com.nexo.{cron_id}.plist"
        if plist_path.is_file():
            plists.append((cron_id, plist_path))
    return plists


def _known_nexo_launchagent_ids() -> set[str]:
    ids = set(AUXILIARY_CORE_LAUNCHAGENT_IDS)
    for cron_id, expected in _launchagent_schedule_expectations().items():
        if expected.get("schedule_configured"):
            ids.add(cron_id)
    try:
        from db import init_db, list_personal_script_schedules

        init_db()
        for schedule in list_personal_script_schedules():
            cron_id = str(schedule.get("cron_id", "") or "").strip()
            if cron_id:
                ids.add(cron_id)
    except Exception:
        pass
    return ids


def _discover_actual_nexo_launchagent_ids() -> tuple[set[str], dict[str, str]]:
    ids: set[str] = set()
    evidence: dict[str, str] = {}

    if LAUNCH_AGENTS_DIR.is_dir():
        for plist_path in sorted(LAUNCH_AGENTS_DIR.glob("com.nexo.*.plist")):
            cron_id = plist_path.stem.removeprefix("com.nexo.")
            ids.add(cron_id)
            evidence.setdefault(cron_id, f"plist on disk: {plist_path}")

    if platform.system() != "Darwin":
        return ids, evidence

    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return ids, evidence

    for raw_line in (result.stdout or "").splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        label = parts[-1].strip()
        if not label.startswith("com.nexo."):
            continue
        cron_id = label.removeprefix("com.nexo.")
        ids.add(cron_id)
        evidence.setdefault(cron_id, f"loaded in launchctl: {label}")
    return ids, evidence


def _extract_launchctl_value(output: str, prefix: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def _is_protected_macos_path(value: str | os.PathLike[str] | None) -> bool:
    if not value or platform.system() != "Darwin":
        return False
    try:
        raw = str(value).replace("~", str(Path.home()), 1)
        candidate = Path(raw).expanduser().resolve(strict=False)
    except Exception:
        return False
    return any(candidate == root or root in candidate.parents for root in PROTECTED_MACOS_ROOTS)


def _plist_runtime_paths(plist_data: dict) -> list[str]:
    paths: list[str] = []
    env = plist_data.get("EnvironmentVariables") or {}
    for key in ("NEXO_HOME", "NEXO_CODE"):
        value = env.get(key)
        if value:
            paths.append(str(value))
    for arg in plist_data.get("ProgramArguments") or []:
        arg_str = str(arg)
        if arg_str.startswith("/") or arg_str.startswith("~"):
            paths.append(arg_str)
    return paths


def _recent_permission_denial(cron_id: str, max_age_seconds: int = 7 * 86400) -> bool:
    stderr_path = NEXO_HOME / "logs" / f"{cron_id}-stderr.log"
    age = _file_age_seconds(stderr_path)
    if age is None or age > max_age_seconds:
        return False
    try:
        tail = "\n".join(stderr_path.read_text(errors="ignore").splitlines()[-50:])
    except Exception:
        return False
    return "Operation not permitted" in tail


def _repair_launchagents(items: list[tuple[str, Path]]) -> tuple[bool, list[str]]:
    evidence = []
    uid = str(os.getuid())
    ok = True
    for cron_id, plist_path in items:
        label = f"com.nexo.{cron_id}"
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            ok = False
            evidence.append(f"{label}: {result.stderr.strip() or result.stdout.strip() or 'bootstrap failed'}")
    return ok, evidence


def _repair_special_launchagent_plists(items: list[tuple[str, Path]]) -> tuple[bool, list[str]]:
    evidence: list[str] = []
    ok = True
    for cron_id, plist_path in items:
        if cron_id not in SPECIAL_ENV_NORMALIZE_IDS:
            continue
        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
            env = plist_data.setdefault("EnvironmentVariables", {})
            changed = False
            if env.get("NEXO_CODE") != str(NEXO_HOME):
                env["NEXO_CODE"] = str(NEXO_HOME)
                changed = True
            if env.get("NEXO_HOME") != str(NEXO_HOME):
                env["NEXO_HOME"] = str(NEXO_HOME)
                changed = True
            if changed:
                with plist_path.open("wb") as fh:
                    plistlib.dump(plist_data, fh)
                evidence.append(f"com.nexo.{cron_id}: normalized special LaunchAgent env")
        except Exception as e:
            ok = False
            evidence.append(f"com.nexo.{cron_id}: {e}")
    return ok, evidence


def _sync_launchagents_from_manifest() -> tuple[bool, list[str]]:
    sync_path = NEXO_CODE / "crons" / "sync.py"
    if not sync_path.is_file():
        return False, [f"cron sync script not found at {sync_path}"]

    try:
        result = subprocess.run(
            [sys.executable, str(sync_path)],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NEXO_HOME": str(NEXO_HOME), "NEXO_CODE": str(NEXO_CODE)},
        )
    except Exception as e:
        return False, [f"cron sync failed: {e}"]

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "cron sync failed"
        return False, [detail]
    return True, []


def check_immune_status() -> DoctorCheck:
    """Check immune system status freshness."""
    status_file = NEXO_HOME / "coordination" / "immune-status.json"
    age = _file_age_seconds(status_file)

    if age is None:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Immune status file not found",
            evidence=[f"Expected: {status_file}"],
            repair_plan=["Check if immune cron is installed and running"],
            escalation_prompt="Immune system has never run or status file was deleted.",
        )

    age_min = age / 60
    if age > IMMUNE_FRESHNESS:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Immune status stale ({age_min:.0f} min old, threshold {IMMUNE_FRESHNESS // 60} min)",
            evidence=[f"{status_file} last modified {age_min:.0f} minutes ago"],
            repair_plan=[
                "Check LaunchAgent/systemd timer for immune cron",
                "nexo scripts call nexo_schedule_status --input '{}'",
            ],
            escalation_prompt="Investigate why immune system stopped refreshing.",
        )

    # Read status for additional context
    try:
        data = _load_json(status_file)
        counts = data.get("counts") or {}
        ok_count = int(counts.get("OK", 0) or 0)
        warn_count = int(counts.get("WARN", 0) or 0)
        fail_count = int(counts.get("FAIL", 0) or 0)
        checks_count = _count_checks(data.get("checks"))
        if fail_count > 0:
            status = "critical"
            severity = "error"
            overall = "fail"
        elif warn_count > 0:
            status = "degraded"
            severity = "warn"
            overall = "warn"
        else:
            status = "healthy"
            severity = "info"
            overall = "ok"
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status=status,
            severity=severity,
            summary=(
                f"Immune: {overall} "
                f"({ok_count} OK, {warn_count} WARN, {fail_count} FAIL; "
                f"{checks_count} checks, {age_min:.0f} min ago)"
            ),
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.immune_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Immune status unreadable ({age_min:.0f} min ago)",
            evidence=[str(e)],
        )


def check_watchdog_status() -> DoctorCheck:
    """Check watchdog status freshness."""
    status_file = NEXO_HOME / "operations" / "watchdog-status.json"
    age = _file_age_seconds(status_file)

    if age is None:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Watchdog status file not found",
            evidence=[f"Expected: {status_file}"],
            repair_plan=["Check if watchdog cron is installed and running"],
            escalation_prompt="Watchdog has never run or status file was deleted.",
        )

    age_min = age / 60
    if age > WATCHDOG_FRESHNESS:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Watchdog status stale ({age_min:.0f} min old)",
            evidence=[
                f"{status_file} last modified {age_min:.0f} minutes ago",
                f"Expected freshness threshold: {WATCHDOG_FRESHNESS // 60} minutes",
            ],
            repair_plan=[
                "Inspect LaunchAgent or systemd timer for watchdog",
                "Check for macOS sandbox errors in stderr logs",
            ],
            escalation_prompt="Investigate why watchdog stopped refreshing despite timer being installed.",
        )

    # Read for detail
    try:
        data = _load_json(status_file)
        summary = data.get("summary") or {}
        monitors = summary.get("total", "?")
        passes = summary.get("pass", "?")
        warns = int(summary.get("warn", 0) or 0)
        fails = int(summary.get("fail", 0) or 0)
        overall = str(summary.get("overall", "UNKNOWN")).upper()
        if overall == "FAIL" or fails > 0:
            status = "critical"
            severity = "error"
        elif overall == "WARN" or warns > 0:
            status = "degraded"
            severity = "warn"
        else:
            status = "healthy"
            severity = "info"
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status=status,
            severity=severity,
            summary=(
                f"Watchdog: {passes}/{monitors} pass, {warns} warn, {fails} fail "
                f"({age_min:.0f} min ago)"
            ),
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.watchdog_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Watchdog status unreadable ({age_min:.0f} min ago)",
            evidence=[str(e)],
        )


def check_stale_sessions() -> DoctorCheck:
    """Check for stale sessions from DB."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="runtime.stale_sessions",
                tier="runtime",
                status="healthy",
                severity="info",
                summary="No DB to check sessions",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.row_factory = sqlite3.Row
            cutoff = time.time() - 7200
            day_ago = time.time() - 86400
            rows = conn.execute(
                "SELECT COUNT(*) as cnt FROM sessions WHERE last_update_epoch < ? AND last_update_epoch > ?",
                (cutoff, day_ago),
            ).fetchone()
        finally:
            conn.close()
        count = rows["cnt"] if rows else 0
        if count > 0:
            return DoctorCheck(
                id="runtime.stale_sessions",
                tier="runtime",
                status="degraded",
                severity="warn",
                summary=f"{count} stale session{'s' if count > 1 else ''} (no heartbeat >2h)",
                repair_plan=["auto_close_sessions cron should handle this automatically"],
            )
        return DoctorCheck(
            id="runtime.stale_sessions",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No stale sessions",
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.stale_sessions",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Session check failed: {e}",
        )


def check_cron_freshness() -> DoctorCheck:
    """Check cron_runs table for recent executions."""
    try:
        import sqlite3
        db_path = NEXO_HOME / "data" / "nexo.db"
        if not db_path.is_file():
            return DoctorCheck(
                id="runtime.cron_freshness",
                tier="runtime",
                status="healthy",
                severity="info",
                summary="No DB to check cron runs",
            )
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            # Check if cron_runs table exists
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cron_runs'"
            ).fetchone()
            if not tables:
                return DoctorCheck(
                    id="runtime.cron_freshness",
                    tier="runtime",
                    status="healthy",
                    severity="info",
                    summary="No cron_runs table yet",
                )
            # Latest run per cron
            rows = conn.execute(
                "SELECT cron_id, MAX(started_at) as last_run FROM cron_runs GROUP BY cron_id"
            ).fetchall()
        finally:
            conn.close()

        stale = []
        expectations = _cron_expectations()
        ignored_crons = _run_at_load_cron_ids()
        tracked_crons = set(expectations)
        now = time.time()
        for row in rows:
            cron_id = row[0]
            if cron_id in ignored_crons:
                continue
            if cron_id not in tracked_crons:
                continue
            parsed = _parse_timestamp(row[1]) if row[1] else None
            if parsed is None:
                stale.append(f"{cron_id}: unreadable timestamp {row[1]!r}")
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)

            age = now - parsed.timestamp()
            expected = expectations.get(cron_id, {"threshold": DEFAULT_CRON_THRESHOLD, "label": "runtime default"})
            if age > expected["threshold"]:
                stale.append(f"{cron_id}: {int(age / 3600)}h ago (expected {expected['label']})")

        if stale:
            return DoctorCheck(
                id="runtime.cron_freshness",
                tier="runtime",
                status="degraded",
                severity="warn",
                summary=f"{len(stale)} cron(s) haven't run recently",
                evidence=stale,
            )
        return DoctorCheck(
            id="runtime.cron_freshness",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"All {len(tracked_crons)} tracked crons ran recently",
        )
    except Exception as e:
        return DoctorCheck(
            id="runtime.cron_freshness",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Cron check failed: {e}",
        )


def check_launchagent_integrity(fix: bool = False) -> DoctorCheck:
    """Check that core LaunchAgents are loaded from the real plist paths, not temp installs."""
    if platform.system() != "Darwin":
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="LaunchAgent integrity check skipped on non-macOS",
        )

    managed = _managed_launchagent_plists()
    if not managed:
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No managed LaunchAgents found on disk",
        )

    uid = str(os.getuid())
    problems = []
    problem_items: list[tuple[str, Path]] = []
    tmp_drift = False
    tcc_risk = False
    tcc_failure = False
    schedule_expectations = _launchagent_schedule_expectations()
    for cron_id, plist_path in managed:
        label = f"com.nexo.{cron_id}"
        had_problem = False
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception as e:
            problems.append(f"{label}: launchctl print failed ({e})")
            continue

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 or "Could not find service" in output:
            problems.append(f"{label}: not loaded")
            had_problem = True
            problem_items.append((cron_id, plist_path))
            continue

        expected_path = str(plist_path)
        actual_path = _extract_launchctl_value(output, "path = ")
        if actual_path != expected_path:
            problems.append(f"{label}: loaded from {actual_path or 'unknown path'}")
            had_problem = True
            if actual_path and "/tmp/" in actual_path:
                tmp_drift = True

        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
            env = plist_data.get("EnvironmentVariables") or {}
        except Exception as e:
            problems.append(f"{label}: plist unreadable ({e})")
            continue

        protected_refs = [path for path in _plist_runtime_paths(plist_data) if _is_protected_macos_path(path)]
        if protected_refs:
            tcc_risk = True
            if _recent_permission_denial(cron_id):
                tcc_failure = True
                problems.append(
                    f"{label}: recent 'Operation not permitted' while using protected macOS path {protected_refs[0]}"
                )
            else:
                problems.append(f"{label}: runtime points into protected macOS path {protected_refs[0]}")
            had_problem = True

        for env_key in ("NEXO_HOME", "NEXO_CODE"):
            expected_value = env.get(env_key)
            if not expected_value:
                continue
            marker = f"{env_key} => {expected_value}"
            if marker not in output:
                problems.append(f"{label}: {env_key} drift")
                had_problem = True
                if "/tmp/" in output:
                    tmp_drift = True

        expected_schedule = schedule_expectations.get(cron_id)
        if expected_schedule is not None and expected_schedule.get("schedule_configured"):
            actual_schedule = {
                "StartInterval": plist_data.get("StartInterval"),
                "StartCalendarInterval": plist_data.get("StartCalendarInterval"),
                "RunAtLoad": plist_data.get("RunAtLoad"),
                "KeepAlive": plist_data.get("KeepAlive"),
            }
            target_schedule = {
                "StartInterval": expected_schedule.get("StartInterval"),
                "StartCalendarInterval": expected_schedule.get("StartCalendarInterval"),
                "RunAtLoad": expected_schedule.get("RunAtLoad"),
                "KeepAlive": expected_schedule.get("KeepAlive"),
            }
            if actual_schedule != target_schedule:
                problems.append(
                    f"{label}: schedule drift "
                    f"(actual={actual_schedule}, expected={target_schedule})"
                )
                had_problem = True

        if had_problem:
            problem_items.append((cron_id, plist_path))

    if not problems:
        return DoctorCheck(
            id="runtime.launchagents",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"LaunchAgents aligned for {len(managed)} managed job(s)",
        )

    check = DoctorCheck(
        id="runtime.launchagents",
        tier="runtime",
        status="critical" if (tmp_drift or tcc_failure) else "degraded",
        severity="error" if (tmp_drift or tcc_failure) else "warn",
        summary=(
            f"LaunchAgent drift detected in {len(problems)} job(s)"
            if not tcc_risk
            else f"LaunchAgent drift or TCC/runtime path risk detected in {len(problems)} job(s)"
        ),
        evidence=problems[:10],
        repair_plan=[
            "Reload the affected LaunchAgents from ~/Library/LaunchAgents",
            "Re-sync core cron plists from crons/manifest.json if the schedule drifted",
            "If any job is loaded from /tmp, boot it out before bootstrapping the real plist",
            "If any core job points into Documents/Desktop/Downloads, re-sync it so it runs from NEXO_HOME instead",
        ],
        escalation_prompt=(
            "Launchd is serving stale or drifted NEXO jobs. Compare loaded job paths with plist paths on disk, "
            "and treat recent 'Operation not permitted' against Documents/Desktop/Downloads as a TCC/runtime path issue."
        ),
    )
    if tcc_risk:
        check.repair_plan.append(
            "On macOS, grant Full Disk Access manually if protected folders are required; "
            "NEXO can only open the System Settings pane and verify best effort"
        )

    if fix:
        sync_ok, sync_evidence = _sync_launchagents_from_manifest()
        special_ok, special_evidence = _repair_special_launchagent_plists(problem_items)
        repaired, repair_evidence = _repair_launchagents(problem_items)
        if sync_ok and special_ok and repaired:
            post_check = check_launchagent_integrity(fix=False)
            if post_check.status == "healthy":
                post_check.fixed = True
                post_check.summary += " (fixed)"
                return post_check
        check.evidence.extend((sync_evidence + special_evidence + repair_evidence)[:10])
    return check


def check_launchagent_inventory() -> DoctorCheck:
    """Check that every discovered com.nexo LaunchAgent is known to core or the personal registry."""
    if platform.system() != "Darwin":
        return DoctorCheck(
            id="runtime.launchagent_inventory",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="LaunchAgent inventory check skipped on non-macOS",
        )

    actual_ids, actual_evidence = _discover_actual_nexo_launchagent_ids()
    if not actual_ids:
        return DoctorCheck(
            id="runtime.launchagent_inventory",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No com.nexo LaunchAgents discovered on this Mac",
        )

    known_ids = _known_nexo_launchagent_ids()
    unknown_ids = sorted(actual_ids - known_ids)
    if not unknown_ids:
        return DoctorCheck(
            id="runtime.launchagent_inventory",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=f"LaunchAgent inventory aligned ({len(actual_ids)} discovered, all known to NEXO)",
        )

    evidence = [actual_evidence.get(cron_id, f"com.nexo.{cron_id}") for cron_id in unknown_ids[:10]]
    return DoctorCheck(
        id="runtime.launchagent_inventory",
        tier="runtime",
        status="degraded",
        severity="warn",
        summary=f"Unknown com.nexo LaunchAgents detected ({len(unknown_ids)})",
        evidence=evidence,
        repair_plan=[
            "If it is a personal automation, register/sync it through nexo scripts sync/reconcile",
            "If it is a core helper, add it to the core LaunchAgent inventory instead of leaving it implicit",
            "If it is retired, boot it out and remove its plist through the owning flow rather than editing plists by hand",
        ],
        escalation_prompt=(
            "There are active or installed com.nexo LaunchAgents that NEXO cannot explain from the core manifest, "
            "auxiliary core services, or the personal script registry."
        ),
    )


def check_skill_health(fix: bool = False) -> DoctorCheck:
    """Check executable skill consistency and approval state."""
    try:
        from db import get_skill_health_report
        report = get_skill_health_report(fix=fix)
    except Exception as e:
        return DoctorCheck(
            id="runtime.skills",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Skill health check failed: {e}",
        )

    issues = report.get("issues", [])
    if not issues:
        summary = f"Skills consistent ({report.get('checked', 0)} checked)"
        if fix:
            summary += " (fixed)"
        return DoctorCheck(
            id="runtime.skills",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=summary,
            fixed=fix,
        )

    errors = [issue for issue in issues if issue.get("severity") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") != "error"]
    status = "critical" if errors else "degraded"
    severity = "error" if errors else "warn"
    evidence = [f"{issue['skill_id']}: {issue['message']}" for issue in issues[:10]]
    return DoctorCheck(
        id="runtime.skills",
        tier="runtime",
        status=status,
        severity=severity,
        summary=f"Skill issues detected in {len(issues)} item(s)",
        evidence=evidence,
        repair_plan=[
            "Run nexo skills sync to reconcile filesystem definitions",
            "Auto-reconcile execution metadata for executable skills",
            "Fix or restore missing executable files for execute/hybrid skills",
        ],
        escalation_prompt="Skill metadata and filesystem artifacts are out of sync or an executable skill is missing artifacts.",
    )


def check_personal_script_registry(fix: bool = False) -> DoctorCheck:
    """Check the DB-backed personal script registry against filesystem/plists."""
    try:
        from db import init_db, get_personal_script_health_report
        from script_registry import sync_personal_scripts

        init_db()
        sync_personal_scripts(prune_missing=True)
        report = get_personal_script_health_report(fix=fix)
    except Exception as e:
        return DoctorCheck(
            id="runtime.personal_scripts",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Personal scripts registry check failed: {e}",
        )

    issues = report.get("issues", [])
    if not issues:
        audit = report.get("schedule_audit", {}).get("summary", {})
        summary = (
            f"Personal scripts registered "
            f"({report.get('scripts', 0)} scripts, {report.get('schedules', 0)} schedules"
            f", {audit.get('healthy', report.get('schedules', 0))} managed)"
        )
        keep_alive = int(audit.get("keep_alive", 0) or 0)
        if keep_alive:
            summary += (
                f", keep_alive {int(audit.get('runtime_alive', 0) or 0)}/{keep_alive} alive"
            )
        if fix:
            summary += " (fixed)"
        return DoctorCheck(
            id="runtime.personal_scripts",
            tier="runtime",
            status="healthy",
            severity="info",
            summary=summary,
            fixed=fix,
        )

    errors = [issue for issue in issues if issue.get("severity") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") != "error"]
    return DoctorCheck(
        id="runtime.personal_scripts",
        tier="runtime",
        status="critical" if errors else "degraded",
        severity="error" if errors else "warn",
        summary=f"Personal scripts registry issues detected in {len(issues)} item(s)",
        evidence=[issue["message"] for issue in issues[:10]],
        repair_plan=[
            "Run nexo scripts sync to reconcile filesystem scripts and personal LaunchAgents",
            "Run nexo scripts reconcile so declared schedules are recreated through the official flow",
            "Use nexo doctor --tier runtime --fix to apply the safe reconcile path for declared schedules",
            "Keep personal scripts in NEXO_HOME/scripts so updates do not collide with core",
            "Prefer ps- prefixed filenames for new personal scripts so ownership stays obvious at a glance",
        ],
        escalation_prompt=(
            "Personal script metadata, files, and personal cron schedules are out of sync. "
            "Reconcile NEXO_HOME/scripts with personal LaunchAgents without treating them as core crons."
        ),
    )


def check_client_backend_preferences() -> DoctorCheck:
    schedule = {}
    try:
        if SCHEDULE_FILE.is_file():
            schedule = _load_json(SCHEDULE_FILE)
    except Exception:
        schedule = {}

    prefs = normalize_client_preferences(schedule)
    detected = detect_installed_clients()

    default_terminal = prefs["default_terminal_client"]
    automation_enabled = bool(prefs["automation_enabled"])
    automation_backend = prefs["automation_backend"]
    default_profile = resolve_client_runtime_profile(default_terminal, preferences=prefs)
    automation_profile = (
        resolve_client_runtime_profile(automation_backend, preferences=prefs)
        if automation_enabled and automation_backend != "none"
        else {"model": "", "reasoning_effort": ""}
    )

    evidence: list[str] = []
    repair_plan: list[str] = []
    severity = "info"
    status = "healthy"

    default_info = detected.get(default_terminal, {})
    if not default_info.get("installed"):
        status = "degraded"
        severity = "warn"
        evidence.append(f"default terminal client `{default_terminal}` is selected but not installed")
        repair_plan.append(f"Install {default_terminal} or switch the default terminal client in schedule.json")

    for client_key, enabled in prefs.get("interactive_clients", {}).items():
        if not enabled:
            continue
        info = detected.get(client_key, {})
        if not info.get("installed"):
            status = "degraded"
            severity = "warn"
            evidence.append(f"interactive client `{client_key}` is enabled but not installed")

    if automation_enabled:
        backend_info = detected.get(automation_backend, {})
        if automation_backend == "none":
            status = "degraded"
            severity = "warn"
            evidence.append("automation is enabled but no automation backend is configured")
        elif not backend_info.get("installed"):
            status = "degraded"
            severity = "warn"
            evidence.append(f"automation backend `{automation_backend}` is enabled but not installed")
            repair_plan.append(f"Install {automation_backend} or disable automation in schedule.json")

    if not repair_plan and status != "healthy":
        repair_plan.append("Run `nexo update` or `nexo clients sync` after installing the selected client/backend")

    def _profile_label(client_key: str, profile: dict[str, str]) -> str:
        bits = [client_key]
        if profile.get("model"):
            bits.append(profile["model"])
        if profile.get("reasoning_effort"):
            bits.append(profile["reasoning_effort"])
        return "/".join(bits)

    terminal_label = f"chat={_profile_label(default_terminal, default_profile)}"
    automation_label = (
        f"automation={_profile_label(automation_backend, automation_profile)}"
        if automation_enabled and automation_backend != "none"
        else "automation=none"
    )
    return DoctorCheck(
        id="runtime.clients",
        tier="runtime",
        status=status,
        severity=severity,
        summary=f"Client/backend preferences OK ({terminal_label}, {automation_label})" if status == "healthy" else f"Client/backend preferences need attention ({terminal_label}, {automation_label})",
        evidence=evidence or [
            f"default terminal client: {_profile_label(default_terminal, default_profile)}",
            f"automation backend: {_profile_label(automation_backend, automation_profile) if automation_enabled and automation_backend != 'none' else 'none'}",
        ],
        repair_plan=repair_plan,
        escalation_prompt=(
            "The configured interactive client or automation backend is missing. "
            "Align installed clients with schedule.json so `nexo chat` and background automation use the intended tools."
        ) if status != "healthy" else "",
    )


def check_client_bootstrap_parity(fix: bool = False) -> DoctorCheck:
    """Check managed Claude/Codex bootstrap documents and CORE/USER markers."""
    try:
        from bootstrap_docs import get_bootstrap_status, sync_enabled_bootstraps
    except Exception as e:
        return DoctorCheck(
            id="runtime.client_bootstrap",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary=f"Bootstrap check unavailable: {e}",
        )

    try:
        schedule = _load_json(SCHEDULE_FILE) if SCHEDULE_FILE.is_file() else {}
    except Exception:
        schedule = {}
    prefs = normalize_client_preferences(schedule)
    detected = detect_installed_clients()

    relevant: set[str] = set()
    default_terminal = prefs["default_terminal_client"]
    if default_terminal in {"claude_code", "codex"}:
        relevant.add(default_terminal)
    if prefs.get("automation_enabled", True):
        backend = prefs.get("automation_backend")
        if backend in {"claude_code", "codex"}:
            relevant.add(backend)
    for client_key, enabled in prefs.get("interactive_clients", {}).items():
        if enabled and client_key in {"claude_code", "codex"}:
            relevant.add(client_key)
    if not relevant:
        relevant.add("claude_code")

    evidence: list[str] = []
    repair_plan: list[str] = []
    status = "healthy"
    severity = "info"

    def _evaluate() -> list[tuple[str, dict]]:
        return [
            (client_key, get_bootstrap_status(client_key, nexo_home=NEXO_HOME, user_home=Path.home()))
            for client_key in sorted(relevant)
        ]

    evaluated = _evaluate()
    for client_key, info in evaluated:
        installed = detected.get(client_key, {}).get("installed", False)
        if not installed and client_key in {default_terminal, prefs.get("automation_backend")}:
            status = "degraded"
            severity = "warn"
            evidence.append(f"`{client_key}` selected but not installed; bootstrap parity cannot be verified")
            continue
        if not info.get("exists"):
            status = "degraded"
            severity = "warn"
            evidence.append(f"`{client_key}` bootstrap missing at {info.get('path')}")
            repair_plan.append("Run `nexo clients sync` or `nexo update` to regenerate client bootstrap files")
            continue
        if not info.get("markers_ok"):
            status = "degraded"
            severity = "warn"
            evidence.append(f"`{client_key}` bootstrap lacks CORE/USER markers")
            repair_plan.append("Migrate bootstrap files so NEXO owns CORE and preserves USER")
            continue
        if info.get("template_version") and info.get("version") != info.get("template_version"):
            status = "degraded"
            severity = "warn"
            evidence.append(
                f"`{client_key}` bootstrap version {info.get('version') or 'unknown'} != template {info.get('template_version')}"
            )
            repair_plan.append("Refresh bootstrap files from the current NEXO templates")
        if client_key == "codex":
            codex_config = _codex_bootstrap_config_status()
            if codex_config.get("error"):
                status = "degraded"
                severity = "warn"
                evidence.append(f"codex config TOML invalid at {codex_config.get('path')}: {codex_config.get('error')}")
                repair_plan.append("Repair ~/.codex/config.toml so NEXO can manage Codex bootstrap and model defaults")
            elif codex_config.get("exists") and not codex_config.get("bootstrap_managed"):
                status = "degraded"
                severity = "warn"
                evidence.append(f"codex config missing managed bootstrap injection at {codex_config.get('path')}")
                repair_plan.append("Run `nexo clients sync` or `nexo update` so plain Codex sessions inherit the NEXO bootstrap")
            elif codex_config.get("exists") and not codex_config.get("has_mcp_server"):
                status = "degraded"
                severity = "warn"
                evidence.append(f"codex config missing managed `mcp_servers.nexo` at {codex_config.get('path')}")
                repair_plan.append("Re-sync Codex so manual sessions keep the shared brain even if `codex mcp add` state drifts")
            elif codex_config.get("exists"):
                evidence.append(
                    "codex config bootstrap managed"
                    + (
                        f" ({codex_config.get('model') or 'default'}, {codex_config.get('reasoning_effort') or 'default'})"
                    )
                )

    if fix and status != "healthy":
        try:
            from client_sync import sync_all_clients
            sync_all_clients(
                nexo_home=NEXO_HOME,
                runtime_root=NEXO_CODE,
                user_home=Path.home(),
                preferences=prefs,
            )
        except Exception:
            sync_enabled_bootstraps(
                nexo_home=NEXO_HOME,
                user_home=Path.home(),
                preferences=prefs,
            )
        post = check_client_bootstrap_parity(fix=False)
        if post.status == "healthy":
            post.fixed = True
            post.summary += " (fixed)"
            return post

    return DoctorCheck(
        id="runtime.client_bootstrap",
        tier="runtime",
        status=status,
        severity=severity,
        summary="Client bootstrap parity OK" if status == "healthy" else "Client bootstrap parity needs attention",
        evidence=evidence or [
            f"{client_key}: {info.get('path')}"
            for client_key, info in evaluated
        ],
        repair_plan=repair_plan,
        escalation_prompt=(
            "Claude/Codex startup bootstrap files are missing, outdated, or lack the CORE/USER contract. "
            "Repair them so updates can refresh product rules without clobbering operator-specific instructions."
        ) if status != "healthy" else "",
    )


def check_codex_session_parity() -> DoctorCheck:
    try:
        schedule = _load_json(SCHEDULE_FILE) if SCHEDULE_FILE.is_file() else {}
    except Exception:
        schedule = {}
    prefs = normalize_client_preferences(schedule)
    wants_codex = bool(
        prefs.get("interactive_clients", {}).get("codex")
        or prefs.get("default_terminal_client") == "codex"
        or (prefs.get("automation_enabled", True) and prefs.get("automation_backend") == "codex")
    )
    if not wants_codex:
        return DoctorCheck(
            id="runtime.codex_sessions",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="Codex session parity check skipped (Codex not selected)",
        )

    audit = _recent_codex_session_parity_status()
    if audit["files"] == 0:
        return DoctorCheck(
            id="runtime.codex_sessions",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="No recent Codex sessions found to verify startup discipline",
            repair_plan=[
                "Start Codex through `nexo chat` at least once so doctor can verify recent NEXO startup behavior",
            ],
            escalation_prompt=(
                "Codex is selected, but there are no recent durable Codex sessions to inspect. "
                "NEXO cannot prove that manual Codex sessions are entering the shared-brain startup flow."
            ),
        )

    evidence = [
        f"recent codex sessions inspected: {audit['files']}",
        f"bootstrap markers seen in {audit['bootstrap_sessions']}/{audit['files']}",
        f"nexo_startup seen in {audit['startup_sessions']}/{audit['files']}",
        f"nexo_heartbeat seen in {audit['heartbeat_sessions']}/{audit['files']}",
    ]
    if audit["origins"]:
        evidence.append(f"origins: {', '.join(audit['origins'])}")

    status = "healthy"
    severity = "info"
    repair_plan: list[str] = []
    if audit["bootstrap_sessions"] == 0:
        status = "degraded"
        severity = "warn"
        repair_plan.append("Run `nexo update` or `nexo clients sync` so plain Codex sessions inherit the managed bootstrap")
    if audit["startup_sessions"] == 0:
        status = "degraded"
        severity = "warn"
        repair_plan.append("Use `nexo chat` or keep the global Codex bootstrap intact so sessions actually call `nexo_startup`")

    return DoctorCheck(
        id="runtime.codex_sessions",
        tier="runtime",
        status=status,
        severity=severity,
        summary="Recent Codex sessions show NEXO startup discipline" if status == "healthy" else "Recent Codex sessions need stronger NEXO startup discipline",
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "Codex is selected, but recent durable Codex sessions are not consistently showing NEXO bootstrap markers or `nexo_startup`. "
            "Manual Codex sessions may still be starting too plain."
        ) if status != "healthy" else "",
    )


def check_codex_conditioned_file_discipline() -> DoctorCheck:
    try:
        schedule = _load_json(SCHEDULE_FILE) if SCHEDULE_FILE.is_file() else {}
    except Exception:
        schedule = {}
    prefs = normalize_client_preferences(schedule)
    wants_codex = bool(
        prefs.get("interactive_clients", {}).get("codex")
        or prefs.get("default_terminal_client") == "codex"
        or (prefs.get("automation_enabled", True) and prefs.get("automation_backend") == "codex")
    )
    if not wants_codex:
        return DoctorCheck(
            id="runtime.codex_conditioned_files",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="Codex conditioned-file discipline check skipped (Codex not selected)",
        )

    audit = _recent_codex_conditioned_file_discipline_status()
    debt_summary = _open_protocol_debt_summary(
        "codex_conditioned_read_without_protocol",
        "codex_conditioned_write_without_protocol",
        "codex_conditioned_write_without_guard_ack",
        "codex_conditioned_delete_without_protocol",
        "codex_conditioned_delete_without_guard_ack",
    )
    evidence = [
        f"active conditioned file rules: {audit['conditioned_rules']}",
        f"recent codex sessions inspected: {audit['files']}",
    ]

    if audit["conditioned_rules"] == 0:
        return DoctorCheck(
            id="runtime.codex_conditioned_files",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No active conditioned-file learnings defined for Codex session audits",
            evidence=evidence,
        )

    if audit["files"] == 0 or audit["conditioned_sessions"] == 0:
        return DoctorCheck(
            id="runtime.codex_conditioned_files",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No conditioned-file touches seen in recent Codex sessions",
            evidence=evidence + [f"conditioned touches: {audit['conditioned_touches']}"],
        )

    evidence.extend([
        f"conditioned sessions: {audit['conditioned_sessions']}",
        f"conditioned touches: {audit['conditioned_touches']}",
        f"read touches without protocol/guard review: {audit['read_without_protocol']}",
        f"write touches without protocol task: {audit['write_without_protocol']}",
        f"write touches without guard acknowledgement: {audit['write_without_guard_ack']}",
        f"delete touches without protocol task: {audit['delete_without_protocol']}",
        f"delete touches without guard acknowledgement: {audit['delete_without_guard_ack']}",
    ])
    if audit.get("latest_violation_age_seconds") is not None:
        age_hours = round(float(audit["latest_violation_age_seconds"]) / 3600, 2)
        evidence.append(f"latest violation age hours: {age_hours}")
    if debt_summary["available"]:
        evidence.append(f"open conditioned protocol debt: {debt_summary['open_total']}")
    for sample in audit["samples"][:5]:
        evidence.append(f"{sample['kind']}: {sample['file']} via {sample['tool']}")

    repair_plan: list[str] = []
    if audit["read_without_protocol"]:
        repair_plan.append("Run nexo_task_open or nexo_guard_check before reading conditioned files in Codex sessions")
    if audit["write_without_protocol"]:
        repair_plan.append("Open work with nexo_task_open before editing conditioned files from Codex")
    if audit["write_without_guard_ack"]:
        repair_plan.append("Acknowledge blocking guard rules before writing conditioned files from Codex")
    if audit["delete_without_protocol"]:
        repair_plan.append("Open work with nexo_task_open before deleting conditioned files from Codex")
    if audit["delete_without_guard_ack"]:
        repair_plan.append("Acknowledge blocking guard rules before deleting conditioned files from Codex")
    if not repair_plan:
        repair_plan.append("Keep using managed Codex bootstrap so conditioned-file discipline remains visible in transcripts")

    no_open_conditioned_debt = debt_summary["available"] and debt_summary["open_total"] == 0
    historical_read_only = (
        no_open_conditioned_debt
        and audit["read_without_protocol"] > 0
        and audit["write_without_protocol"] == 0
        and audit["write_without_guard_ack"] == 0
        and audit["delete_without_protocol"] == 0
        and audit["delete_without_guard_ack"] == 0
    )
    historical_write_drift = (
        no_open_conditioned_debt
        and audit.get("latest_violation_age_seconds") is not None
        and float(audit["latest_violation_age_seconds"]) >= 172800
        and audit["write_without_protocol"] > 0
        and audit["write_without_guard_ack"] == 0
        and audit["delete_without_protocol"] == 0
        and audit["delete_without_guard_ack"] == 0
    )

    if audit["write_without_protocol"] or audit["write_without_guard_ack"]:
        if historical_write_drift:
            status = "healthy"
            severity = "info"
        else:
            status = "critical"
            severity = "error"
    elif historical_read_only:
        status = "healthy"
        severity = "info"
    elif audit["read_without_protocol"]:
        status = "degraded"
        severity = "warn"
    else:
        status = "healthy"
        severity = "info"

    return DoctorCheck(
        id="runtime.codex_conditioned_files",
        tier="runtime",
        status=status,
        severity=severity,
        summary=(
            "Historical Codex conditioned-file drift has no open protocol debt"
            if historical_read_only or historical_write_drift
            else "Recent Codex sessions respect conditioned-file discipline"
            if status == "healthy"
            else "Recent Codex sessions are bypassing conditioned-file discipline"
        ),
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "Codex sessions are touching conditioned files without the expected protocol/guard sequence. "
            "Until this is clean, parity with Claude hooks is still incomplete."
        ) if status != "healthy" else "",
    )


def check_claude_desktop_shared_brain() -> DoctorCheck:
    try:
        schedule = _load_json(SCHEDULE_FILE) if SCHEDULE_FILE.is_file() else {}
    except Exception:
        schedule = {}
    prefs = normalize_client_preferences(schedule)
    wants_desktop = bool(prefs.get("interactive_clients", {}).get("claude_desktop"))
    installed = detect_installed_clients().get("claude_desktop", {}).get("installed", False)
    status_info = _claude_desktop_shared_brain_status()

    if not wants_desktop and not installed:
        return DoctorCheck(
            id="runtime.claude_desktop",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="Claude Desktop shared-brain check skipped (client not installed)",
        )

    evidence = [
        f"config: {status_info.get('path')}",
        f"shared brain mode: {status_info.get('shared_brain_mode') or 'mcp_only'}",
    ]
    if status_info.get("managed_runtime_home"):
        evidence.append(f"runtime home: {status_info.get('managed_runtime_home')}")

    if status_info.get("error"):
        return DoctorCheck(
            id="runtime.claude_desktop",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Claude Desktop config is unreadable",
            evidence=evidence + [status_info["error"]],
            repair_plan=["Repair Claude Desktop config JSON and re-run `nexo clients sync`"],
        )

    if not status_info.get("exists") or not status_info.get("has_mcp_server"):
        return DoctorCheck(
            id="runtime.claude_desktop",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Claude Desktop is not pointed at the shared NEXO brain",
            evidence=evidence,
            repair_plan=["Run `nexo clients sync` so Claude Desktop shares the same local brain"],
            escalation_prompt=(
                "Claude Desktop is installed or enabled, but its MCP config does not show the shared `nexo` runtime."
            ),
        )

    if not status_info.get("shared_brain_managed"):
        return DoctorCheck(
            id="runtime.claude_desktop",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Claude Desktop shares NEXO, but managed metadata is missing",
            evidence=evidence,
            repair_plan=["Re-sync Claude Desktop so doctor can verify the managed shared-brain contract"],
        )

    return DoctorCheck(
        id="runtime.claude_desktop",
        tier="runtime",
        status="healthy",
        severity="info",
        summary="Claude Desktop shared-brain parity OK (MCP-only mode)",
        evidence=evidence,
    )


def check_transcript_source_parity() -> DoctorCheck:
    """Check whether Deep Sleep can see transcript sources for the selected clients."""
    try:
        schedule = _load_json(SCHEDULE_FILE) if SCHEDULE_FILE.is_file() else {}
    except Exception:
        schedule = {}
    prefs = normalize_client_preferences(schedule)

    wants_codex = bool(
        prefs.get("interactive_clients", {}).get("codex")
        or prefs.get("default_terminal_client") == "codex"
        or (prefs.get("automation_enabled", True) and prefs.get("automation_backend") == "codex")
    )
    wants_claude = bool(
        prefs.get("interactive_clients", {}).get("claude_code")
        or prefs.get("default_terminal_client") == "claude_code"
        or (prefs.get("automation_enabled", True) and prefs.get("automation_backend") == "claude_code")
    )

    claude_root = Path.home() / ".claude" / "projects"
    codex_roots = [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    ]

    evidence = []
    status = "healthy"
    severity = "info"
    if wants_claude:
        evidence.append(f"claude_code transcripts: {'present' if claude_root.exists() else 'missing'} at {claude_root}")
    if wants_codex:
        codex_present = any(root.exists() for root in codex_roots)
        evidence.append(
            "codex transcripts: "
            + ("present" if codex_present else "missing")
            + f" at {', '.join(str(root) for root in codex_roots)}"
        )
        if not codex_present:
            status = "degraded"
            severity = "warn"

    summary = "Deep Sleep transcript sources available"
    repair_plan = []
    escalation_prompt = ""
    if status != "healthy":
        summary = "Deep Sleep transcript source parity needs attention"
        repair_plan = [
            "Start at least one Codex session so ~/.codex/sessions is created",
            "If Codex sessions already exist elsewhere, update the collector before relying on Codex-only transcript analysis",
        ]
        escalation_prompt = (
            "Codex is selected, but no durable Codex session store is visible under ~/.codex. "
            "Deep Sleep can still use DB artifacts, but transcript-level overnight analysis will be limited until Codex session files exist."
        )

    return DoctorCheck(
        id="runtime.transcript_sources",
        tier="runtime",
        status=status,
        severity=severity,
        summary=summary,
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=escalation_prompt,
    )


def check_client_assumption_regressions() -> DoctorCheck:
    offenders = _client_assumption_regressions()
    if not offenders:
        return DoctorCheck(
            id="runtime.client_assumptions",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No new Claude-only runtime path assumptions detected",
        )
    return DoctorCheck(
        id="runtime.client_assumptions",
        tier="runtime",
        status="critical",
        severity="error",
        summary=f"Detected {len(offenders)} client-parity regression(s) in runtime source",
        evidence=offenders[:10],
        repair_plan=[
            "Replace Claude-only transcript or hook assumptions with shared client abstractions",
            "Keep Deep Sleep and startup flows aware of both Claude Code and Codex surfaces",
        ],
        escalation_prompt=(
            "A runtime source file drifted back to a Claude-only assumption. "
            "Audit the offending file and restore client-agnostic parity before shipping."
        ),
    )


def check_protocol_compliance() -> DoctorCheck:
    try:
        import sqlite3

        db_path = NEXO_HOME / "data" / "nexo.db"
        if db_path.is_file():
            conn = sqlite3.connect(str(db_path), timeout=2)
            try:
                conn.row_factory = sqlite3.Row
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('protocol_tasks', 'protocol_debt')"
                    ).fetchall()
                }
                tasks = None
                debt_rows = None
                if {"protocol_tasks", "protocol_debt"}.issubset(tables):
                    window = "-7 days"
                    tasks = conn.execute(
                        """SELECT * FROM protocol_tasks
                           WHERE opened_at >= datetime('now', ?)
                           ORDER BY opened_at DESC""",
                        (window,),
                    ).fetchall()
                    debt_rows = conn.execute(
                        """SELECT severity, debt_type, COUNT(*) AS total
                           FROM protocol_debt
                           WHERE status = 'open' AND created_at >= datetime('now', ?)
                           GROUP BY severity, debt_type
                           ORDER BY total DESC, debt_type ASC""",
                        (window,),
                    ).fetchall()
                    has_cortex_evaluations = bool(
                        conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cortex_evaluations'"
                        ).fetchone()
                    )
                    covered_tasks = set()
                    if has_cortex_evaluations:
                        covered_tasks = {
                            row["task_id"]
                            for row in conn.execute(
                                "SELECT DISTINCT task_id FROM cortex_evaluations WHERE task_id != ''"
                            ).fetchall()
                        }
            finally:
                conn.close()

            if tasks is not None and debt_rows is not None and (tasks or debt_rows):
                    closed_tasks = [row for row in tasks if row["status"] != "open"]
                    verify_required = [row for row in closed_tasks if row["must_verify"] and row["status"] == "done"]
                    verify_ok = [row for row in verify_required if (row["close_evidence"] or "").strip()]
                    change_required = [row for row in closed_tasks if row["must_change_log"]]
                    change_ok = [row for row in change_required if row["change_log_id"]]
                    learning_required = [row for row in closed_tasks if row["correction_happened"]]
                    learning_ok = [row for row in learning_required if row["learning_id"]]
                    action_tasks = [row for row in tasks if row["task_type"] in ("edit", "execute", "delegate")]
                    cortex_ok = [row for row in action_tasks if row["cortex_mode"] == "act"]
                    has_response_high_stakes = bool(tasks) and "response_high_stakes" in tasks[0].keys()
                    high_stakes_action_tasks = [row for row in action_tasks if row["response_high_stakes"]] if has_response_high_stakes else []
                    decision_ok = [row for row in high_stakes_action_tasks if row["task_id"] in covered_tasks]

                    score_parts = []
                    if verify_required:
                        score_parts.append((len(verify_ok) / len(verify_required)) * 100)
                    if change_required:
                        score_parts.append((len(change_ok) / len(change_required)) * 100)
                    if learning_required:
                        score_parts.append((len(learning_ok) / len(learning_required)) * 100)
                    if action_tasks:
                        score_parts.append((len(cortex_ok) / len(action_tasks)) * 100)
                    if high_stakes_action_tasks:
                        score_parts.append((len(decision_ok) / len(high_stakes_action_tasks)) * 100)

                    base_score = (sum(score_parts) / len(score_parts)) if score_parts else (100.0 if tasks else 0.0)
                    warn_debt = sum(row["total"] for row in debt_rows if row["severity"] == "warn")
                    error_debt = sum(row["total"] for row in debt_rows if row["severity"] == "error")
                    overall = max(0.0, round(base_score - min(60, (warn_debt * 5) + (error_debt * 20)), 1))

                    evidence = [f"live protocol window: 7d", f"protocol tasks: {len(tasks)} total / {len(closed_tasks)} closed"]
                    evidence.append(f"overall live protocol compliance: {overall:.1f}%")
                    if verify_required:
                        evidence.append(f"verified closures: {len(verify_ok)}/{len(verify_required)}")
                    if change_required:
                        evidence.append(f"change_log coverage: {len(change_ok)}/{len(change_required)}")
                    if learning_required:
                        evidence.append(f"learning-after-correction: {len(learning_ok)}/{len(learning_required)}")
                    if action_tasks:
                        evidence.append(f"action tasks Cortex-cleared: {len(cortex_ok)}/{len(action_tasks)}")
                    if high_stakes_action_tasks:
                        evidence.append(f"high-stakes action tasks with alternative evaluation: {len(decision_ok)}/{len(high_stakes_action_tasks)}")
                    for row in debt_rows[:5]:
                        evidence.append(f"open {row['severity']} debt — {row['debt_type']}: {row['total']}")

                    repair_plan: list[str] = []
                    if verify_required and len(verify_ok) != len(verify_required):
                        repair_plan.append("Close tasks with nexo_task_close evidence before claiming completion")
                    if change_required and len(change_ok) != len(change_required):
                        repair_plan.append("Use nexo_task_close or nexo_change_log for edit/execute tasks")
                    if learning_required and len(learning_ok) != len(learning_required):
                        repair_plan.append("Capture reusable learnings whenever a correction happened")
                    if error_debt or warn_debt:
                        repair_plan.append("Resolve open protocol debt before treating the runtime as healthy")

                    if error_debt > 0 or overall < 45:
                        status = "critical"
                        severity = "error"
                    elif warn_debt > 0 or overall < 70:
                        status = "degraded"
                        severity = "warn"
                    else:
                        status = "healthy"
                        severity = "info"

                    return DoctorCheck(
                        id="runtime.protocol_compliance",
                        tier="runtime",
                        status=status,
                        severity=severity,
                        summary="Live protocol compliance looks healthy" if status == "healthy" else "Live protocol compliance needs hardening",
                        evidence=evidence,
                        repair_plan=repair_plan,
                        escalation_prompt=(
                            "Task discipline is drifting in live runtime data. NEXO is still skipping verification, change logging, or correction capture."
                        ) if status != "healthy" else "",
                    )
    except Exception:
        pass

    summary = _latest_periodic_summary("weekly")
    if not summary:
        return DoctorCheck(
            id="runtime.protocol_compliance",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="No weekly Deep Sleep protocol summary found",
            repair_plan=[
                "Run the Deep Sleep pipeline so weekly summaries include protocol compliance again",
            ],
            escalation_prompt=(
                "NEXO cannot verify heartbeat / guard_check / change_log compliance because the latest weekly Deep Sleep summary is missing."
            ),
        )

    protocol = summary.get("protocol_summary") or {}
    overall = protocol.get("overall_compliance_pct")
    guard = protocol.get("guard_check") or {}
    heartbeat = protocol.get("heartbeat") or {}
    change_log = protocol.get("change_log") or {}
    evidence = [f"weekly summary: {summary.get('label', 'unknown')}"]
    if overall is not None:
        evidence.append(f"overall protocol compliance: {overall:.1f}%")
    if guard.get("compliance_pct") is not None:
        evidence.append(
            f"guard_check: {guard.get('executed', 0)}/{guard.get('required', 0)} ({guard['compliance_pct']:.1f}%)"
        )
    if heartbeat.get("compliance_pct") is not None:
        evidence.append(
            f"heartbeat with context: {heartbeat.get('with_context', 0)}/{heartbeat.get('total', 0)} ({heartbeat['compliance_pct']:.1f}%)"
        )
    if change_log.get("compliance_pct") is not None:
        evidence.append(
            f"change_log after edits: {change_log.get('logged', 0)}/{change_log.get('edits', 0)} ({change_log['compliance_pct']:.1f}%)"
        )

    status = "healthy"
    severity = "info"
    repair_plan: list[str] = []
    if overall is None:
        status = "degraded"
        severity = "warn"
        repair_plan.append("Ensure Deep Sleep extractions keep writing protocol_summary data")
    elif overall < 45:
        status = "critical"
        severity = "error"
    elif overall < 70:
        status = "degraded"
        severity = "warn"

    if status != "healthy":
        repair_plan.extend(
            [
                "Reinforce heartbeat discipline on every user message",
                "Call nexo_guard_check before production/shared edits",
                "Record production changes with nexo_change_log after editing",
            ]
        )

    return DoctorCheck(
        id="runtime.protocol_compliance",
        tier="runtime",
        status=status,
        severity=severity,
        summary="Protocol compliance looks healthy" if status == "healthy" else "Protocol compliance needs hardening",
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "Heartbeat / guard_check / change_log discipline is drifting. NEXO is at risk of repeating known errors and hiding change history."
        ) if status != "healthy" else "",
    )


def check_release_artifact_sync() -> DoctorCheck:
    version = _package_version()
    changelog_version = _top_changelog_version()
    evidence = []
    status = "healthy"
    severity = "info"
    repair_plan: list[str] = []

    if version:
        evidence.append(f"package version: {version}")
    if changelog_version:
        evidence.append(f"top changelog version: {changelog_version}")

    if version and changelog_version and version != changelog_version:
        status = "critical"
        severity = "error"
        evidence.append("package/changelog release version mismatch")
        repair_plan.append("Bump or align CHANGELOG.md before publishing")

    release_root = _release_root()
    sync_script = release_root / "scripts" / "sync_release_artifacts.py"
    if not sync_script.is_file():
        status = "critical"
        severity = "error"
        evidence.append(f"missing release artifact sync script at {sync_script}")
        repair_plan.append("Restore scripts/sync_release_artifacts.py")
    else:
        try:
            result = subprocess.run(
                [sys.executable, str(sync_script), "--check"],
                cwd=str(release_root),
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            status = "degraded" if status == "healthy" else status
            severity = "warn" if severity == "info" else severity
            evidence.append(f"artifact sync check failed to run: {exc}")
            repair_plan.append("Run scripts/sync_release_artifacts.py manually and inspect the local environment")
        else:
            if result.returncode != 0:
                status = "degraded" if status == "healthy" else status
                severity = "warn" if severity == "info" else severity
                detail = result.stderr.strip() or result.stdout.strip() or "artifact sync check failed"
                evidence.append(detail.splitlines()[0])
                repair_plan.append("Run scripts/sync_release_artifacts.py before publishing")
            else:
                evidence.append("release artifacts in sync")

    return DoctorCheck(
        id="runtime.release_artifacts",
        tier="runtime",
        status=status,
        severity=severity,
        summary="Release artifact discipline OK" if status == "healthy" else "Release artifact discipline needs attention",
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "Release-facing artifacts drifted away from the source version contract. Publishing now risks another hotfix release."
        ) if status != "healthy" else "",
    )


def check_state_watchers() -> DoctorCheck:
    db_path = NEXO_HOME / "data" / "nexo.db"
    summary_path = NEXO_HOME / "operations" / "state-watchers-status.json"
    active_watchers = 0
    if db_path.is_file():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM state_watchers WHERE status = 'active'"
                ).fetchone()
                active_watchers = int(row[0] or 0) if row else 0
            finally:
                conn.close()
        except Exception:
            active_watchers = 0

    if active_watchers == 0:
        return DoctorCheck(
            id="runtime.state_watchers",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No active state watchers configured",
            evidence=[],
            repair_plan=[],
            escalation_prompt="",
        )

    if not summary_path.is_file():
        return DoctorCheck(
            id="runtime.state_watchers",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="State watchers configured but no fresh summary exists",
            evidence=[f"active_watchers={active_watchers}", str(summary_path)],
            repair_plan=["Run nexo_state_watcher_run or wait for daily self-audit to refresh watcher status"],
            escalation_prompt="State watchers exist but their health summary is missing, so drift and expiry signals may be going dark.",
        )

    try:
        payload = json.loads(summary_path.read_text())
    except Exception as exc:
        return DoctorCheck(
            id="runtime.state_watchers",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="State watchers summary is unreadable",
            evidence=[str(exc)],
            repair_plan=["Re-run nexo_state_watcher_run to regenerate operations/state-watchers-status.json"],
            escalation_prompt="State watcher health cannot be trusted until the summary is readable again.",
        )

    generated_at = payload.get("generated_at")
    evidence = [f"active_watchers={active_watchers}", f"generated_at={generated_at or 'missing'}"]
    counts = payload.get("counts") or {}
    if counts:
        evidence.append(
            "counts="
            + ",".join(f"{key}:{int(value)}" for key, value in sorted(counts.items()))
        )

    status = "healthy"
    severity = "info"
    repair_plan: list[str] = []
    generated_dt = None
    if generated_at:
        try:
            generated_dt = dt.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        except Exception:
            generated_dt = None
    if not generated_dt or (dt.datetime.now(dt.timezone.utc) - generated_dt).total_seconds() > 36 * 3600:
        status = "degraded"
        severity = "warn"
        repair_plan.append("Refresh state watchers daily so repo/API/expiry drift stays explicit")

    if int(counts.get("critical") or 0) > 0:
        status = "critical"
        severity = "error"
        repair_plan.append("Resolve the critical state watchers immediately")
    elif int(counts.get("degraded") or 0) > 0 and status == "healthy":
        status = "degraded"
        severity = "warn"
        repair_plan.append("Resolve degraded state watchers before they become hard failures")

    return DoctorCheck(
        id="runtime.state_watchers",
        tier="runtime",
        status=status,
        severity=severity,
        summary="State watchers look healthy" if status == "healthy" else "State watchers need attention",
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "State watchers detected live drift or expiry risk across repo/cron/API/environment surfaces."
        ) if status != "healthy" else "",
    )


def check_automation_telemetry(days: int = 7) -> DoctorCheck:
    db_path = NEXO_HOME / "data" / "nexo.db"
    if not db_path.is_file():
        return DoctorCheck(
            id="runtime.automation_telemetry",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Automation telemetry DB is missing",
            evidence=[str(db_path)],
            repair_plan=["Run NEXO once so migrations create the shared runtime DB"],
            escalation_prompt="Cost and parity telemetry cannot be trusted until the runtime DB exists.",
        )

    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='automation_runs'"
            ).fetchone()
            if not table:
                return DoctorCheck(
                    id="runtime.automation_telemetry",
                    tier="runtime",
                    status="degraded",
                    severity="warn",
                    summary="Automation telemetry schema is missing",
                    evidence=["table automation_runs not found"],
                    repair_plan=["Run NEXO migrations before trusting automation cost/parity metrics"],
                    escalation_prompt="Shared automation runs are happening without the telemetry table that release metrics depend on.",
                )

            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS runs,
                    SUM(CASE WHEN (input_tokens + cached_input_tokens + output_tokens) > 0 THEN 1 ELSE 0 END) AS usage_runs,
                    SUM(CASE WHEN total_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS cost_runs,
                    SUM(CASE WHEN cost_source = 'pricing_unavailable' THEN 1 ELSE 0 END) AS pricing_gaps,
                    GROUP_CONCAT(DISTINCT backend) AS backends
                FROM automation_runs
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return DoctorCheck(
            id="runtime.automation_telemetry",
            tier="runtime",
            status="degraded",
            severity="warn",
            summary="Automation telemetry is unreadable",
            evidence=[str(exc)],
            repair_plan=["Inspect the runtime DB and restore the automation_runs table"],
            escalation_prompt="Automation cost and parity metrics are unreadable, so release numbers may be lying by omission.",
        )

    total_runs = int((row["runs"] if row else 0) or 0)
    if total_runs == 0:
        return DoctorCheck(
            id="runtime.automation_telemetry",
            tier="runtime",
            status="healthy",
            severity="info",
            summary="No recent automation runs to score",
            evidence=[f"window={days}d", "runs=0"],
            repair_plan=[],
            escalation_prompt="",
        )

    usage_runs = int((row["usage_runs"] if row else 0) or 0)
    cost_runs = int((row["cost_runs"] if row else 0) or 0)
    pricing_gaps = int((row["pricing_gaps"] if row else 0) or 0)
    usage_coverage = round((usage_runs / total_runs) * 100, 1)
    cost_coverage = round((cost_runs / total_runs) * 100, 1)
    evidence = [
        f"window={days}d",
        f"runs={total_runs}",
        f"usage_coverage={usage_coverage}%",
        f"cost_coverage={cost_coverage}%",
        f"pricing_gaps={pricing_gaps}",
    ]
    backends = str((row["backends"] if row else "") or "").strip()
    if backends:
        evidence.append(f"backends={backends}")

    status = "healthy"
    severity = "info"
    repair_plan: list[str] = []
    if usage_coverage < 100.0:
        status = "degraded"
        severity = "warn"
        repair_plan.append("Restore backend usage parsing so automation runs always emit token telemetry")
    if cost_coverage < 90.0:
        status = "critical" if total_runs >= 3 else "degraded"
        severity = "error" if status == "critical" else "warn"
        repair_plan.append("Restore explicit backend cost or pricing coverage before trusting cost-per-task metrics")
    if pricing_gaps:
        status = "critical" if status != "critical" and total_runs >= 3 else status
        severity = "error" if status == "critical" else severity
        repair_plan.append("Add pricing coverage for new automation models or switch to backend-reported cost")

    return DoctorCheck(
        id="runtime.automation_telemetry",
        tier="runtime",
        status=status,
        severity=severity,
        summary="Automation telemetry looks healthy" if status == "healthy" else "Automation telemetry needs attention",
        evidence=evidence,
        repair_plan=repair_plan,
        escalation_prompt=(
            "Shared automation is running without enough telemetry coverage to defend parity/cost claims."
        ) if status != "healthy" else "",
    )


def run_runtime_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all runtime-tier checks. Read-only by default."""
    return [
        safe_check(check_immune_status),
        safe_check(check_watchdog_status),
        safe_check(check_stale_sessions),
        safe_check(check_cron_freshness),
        safe_check(check_client_backend_preferences),
        safe_check(check_client_bootstrap_parity, fix=fix),
        safe_check(check_codex_session_parity),
        safe_check(check_codex_conditioned_file_discipline),
        safe_check(check_claude_desktop_shared_brain),
        safe_check(check_transcript_source_parity),
        safe_check(check_client_assumption_regressions),
        safe_check(check_protocol_compliance),
        safe_check(check_automation_telemetry),
        safe_check(check_state_watchers),
        safe_check(check_release_artifact_sync),
        safe_check(check_launchagent_inventory),
        safe_check(check_launchagent_integrity, fix=fix),
        safe_check(check_personal_script_registry, fix=fix),
        safe_check(check_skill_health, fix=fix),
    ]
