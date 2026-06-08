"""NEXO Script Registry — discovery, metadata, validation for personal scripts.

Scripts live in NEXO_HOME/personal/scripts/. Core scripts (from manifest) are filtered by default.
Personal scripts use CLI as stable interface, never direct DB access.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
import paths

from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))

# Internal artifacts to always ignore
_IGNORED_FILES = {
    ".watchdog-hashes",
    ".watchdog-fails",
    ".watchdog-nexo-repair.lock",
    "nexo-cron-wrapper.sh",
    "nexo-dashboard.sh",
    "nexo-prevent-sleep.sh",
    "nexo-proactive-dashboard.py",
    "nexo-tcc-approve.sh",
}
_IGNORED_DIRS = {"deep-sleep", "__pycache__"}

_LEGACY_WAKE_RECOVERY_METADATA = [
    "# nexo: name=nexo-wake-recovery",
    "# nexo: description=Recover interval LaunchAgents after macOS sleep/wake gaps",
    "# nexo: runtime=shell",
    "# nexo: cron_id=wake-recovery",
    "# nexo: schedule_required=true",
    "# nexo: recovery_policy=restart_daemon",
    "# nexo: run_on_boot=true",
]

_LEGACY_CORE_RUNTIME_FILES = {
    "capture-tool-logs.sh",
    "daily-briefing-check.sh",
    "heartbeat-enforcement.py",
    "heartbeat-posttool.sh",
    "heartbeat-user-msg.sh",
    "nexo-memory-precompact.sh",
    "nexo-memory-stop.sh",
    "nexo-postcompact.sh",
    "nexo-session-briefing.sh",
}

# Forbidden patterns — direct DB access from personal scripts
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bsqlite3\b"),
    re.compile(r"\bnexo\.db\b"),
    re.compile(r"\bcognitive\.db\b"),
    re.compile(r"/data/nexo\.db"),
    re.compile(r"/data/cognitive\.db"),
    re.compile(r"\bimport\s+db\b"),
    re.compile(r"\bfrom\s+db\s+import\b"),
    re.compile(r"\bimport\s+cognitive\b"),
    re.compile(r"\bfrom\s+cognitive\s+import\b"),
]

METADATA_KEYS = {
    "name",
    "description",
    "runtime",
    "timeout",
    "requires",
    "tools",
    "hidden",
    "category",
    "cron_id",
    "schedule",
    "schedule_freq",
    "schedule_at",
    "schedule_day",
    "interval_seconds",
    "schedule_required",
    "recovery_policy",
    "run_on_boot",
    "run_on_wake",
    "idempotent",
    "max_catchup_age",
    "doctor_allow_db",
    "agent",
    "agent_title",
    "agent_description",
    "agent_conversation_id",
    "agent_created_from",
    "agent_archived",
    "agent_enabled_before_archive",
    "agent_icon",
}
AGENT_METADATA_KEYS = {
    "agent",
    "agent_title",
    "agent_description",
    "agent_conversation_id",
    "agent_created_from",
    "agent_archived",
    "agent_enabled_before_archive",
    "agent_icon",
}
METADATA_WRITE_ORDER = [
    "name",
    "description",
    "runtime",
    "agent",
    "agent_title",
    "agent_description",
    "agent_conversation_id",
    "agent_created_from",
    "agent_archived",
    "agent_enabled_before_archive",
    "agent_icon",
    "cron_id",
    "schedule_required",
    "schedule",
    "schedule_freq",
    "schedule_at",
    "schedule_day",
    "interval_seconds",
    "recovery_policy",
    "run_on_boot",
    "run_on_wake",
    "idempotent",
    "max_catchup_age",
    "timeout",
    "requires",
    "tools",
    "hidden",
    "category",
    "doctor_allow_db",
]
SUPPORTED_RUNTIMES = {"python", "shell", "node", "php", "unknown"}
PERSONAL_SCHEDULE_MANAGED_ENV = "NEXO_MANAGED_PERSONAL_CRON"
SUPPORTED_RECOVERY_POLICIES = {"none", "run_once_on_wake", "catchup", "restart", "restart_daemon"}
PERSONAL_SCRIPT_FILENAME_PREFIX = "ps-"
_RUNTIME_METADATA_ALIASES = {
    "bash": "shell",
    "sh": "shell",
    "zsh": "shell",
    "shellscript": "shell",
    "python3": "python",
    "py": "python",
    "nodejs": "node",
    "javascript": "node",
}
_SCHEDULE_WEEKDAY_SUFFIX_RE = re.compile(
    r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+weekday\s*=\s*(?P<weekday>\d)$",
    re.IGNORECASE,
)
_SCHEDULE_WEEKDAY_PREFIX_RE = re.compile(
    r"^weekday\s*=\s*(?P<weekday>\d)\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})$",
    re.IGNORECASE,
)
_SCHEDULE_AT_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
SUPPORTED_ANCHORED_SCHEDULE_FREQS = {"daily", "weekly", "monthly", "every_n_days"}
_LEGACY_CORE_SCRIPT_ALIASES = {
    "nexo-postcompact.sh": "post-compact.sh",
    "nexo-memory-precompact.sh": "pre-compact.sh",
    "nexo-memory-stop.sh": "session-stop.sh",
    "nexo-session-briefing.sh": "session-start.sh",
}
PRODUCT_AUTOMATION_NAMES = (
    "email-monitor",
    "followup-runner",
    "morning-agent",
)


def get_nexo_home() -> Path:
    return NEXO_HOME


def get_scripts_dir() -> Path:
    return paths.personal_scripts_dir()


def _apply_legacy_personal_script_backfills() -> None:
    """Backfill metadata for known legacy personal scripts shipped before the registry existed."""
    scripts_dir = get_scripts_dir()
    wake_recovery = scripts_dir / "nexo-wake-recovery.sh"
    if not wake_recovery.is_file():
        return

    try:
        text = wake_recovery.read_text()
    except Exception:
        return

    if "# nexo:" in "\n".join(text.splitlines()[:25]):
        return
    if "Wake Recovery" not in text:
        return

    lines = text.splitlines(keepends=True)
    head: list[str] = []
    start = 0
    if lines and lines[0].startswith("#!"):
        head.append(lines[0])
        start = 1
    head.extend([line + "\n" for line in _LEGACY_WAKE_RECOVERY_METADATA])
    wake_recovery.write_text("".join(head + lines[start:]))


def _add_runtime_artifact_names(names: set[str], artifact_path: Path) -> None:
    try:
        data = json.loads(artifact_path.read_text())
    except Exception:
        return
    for key in ("script_names", "hook_names"):
        for item in data.get(key, []):
            if isinstance(item, str) and item.strip():
                names.add(Path(item).name)


def _add_filenames_from_dir(names: set[str], directory: Path, *, skip_if_scripts_dir: bool = False) -> None:
    if not directory.is_dir():
        return
    if skip_if_scripts_dir:
        try:
            if directory.resolve() == get_scripts_dir().resolve():
                return
        except Exception:
            pass
    for item in directory.iterdir():
        if item.is_file() and not item.name.startswith("."):
            names.add(item.name)


def _find_packaged_core_source_dir() -> Path | None:
    repo_root = NEXO_CODE.parent
    if (repo_root / ".git").exists() or (repo_root / ".git").is_file():
        return None

    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "nexo-brain" / "src"
            if candidate.is_dir():
                return candidate
    return None


def load_core_script_names() -> set[str]:
    """Load every core-managed runtime artifact name that must never be treated as personal."""
    names: set[str] = set()
    packaged_src = _find_packaged_core_source_dir()

    manifest_candidates = []
    if packaged_src is not None:
        manifest_candidates.append(packaged_src / "crons" / "manifest.json")
    manifest_candidates.extend([NEXO_CODE / "crons" / "manifest.json", paths.crons_dir() / "manifest.json"])

    for manifest_path in manifest_candidates:
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                for cron in data.get("crons", []):
                    script = cron.get("script", "")
                    # script is like "scripts/nexo-immune.py" — extract filename
                    names.add(Path(script).name)
                break
            except Exception:
                continue

    if packaged_src is not None:
        _add_filenames_from_dir(names, packaged_src / "hooks")
        _add_filenames_from_dir(names, packaged_src / "scripts")
    else:
        for artifact_path in (
            paths.config_dir() / "runtime-core-artifacts.json",
            NEXO_CODE / "config" / "runtime-core-artifacts.json",
            NEXO_CODE.parent / "config" / "runtime-core-artifacts.json",
        ):
            if artifact_path.exists():
                _add_runtime_artifact_names(names, artifact_path)

        _add_filenames_from_dir(names, paths.core_hooks_dir())
        _add_filenames_from_dir(names, NEXO_CODE / "hooks")
        _add_filenames_from_dir(names, paths.core_scripts_dir(), skip_if_scripts_dir=True)
        _add_filenames_from_dir(names, NEXO_CODE / "scripts", skip_if_scripts_dir=True)

    for legacy_name, canonical_name in _LEGACY_CORE_SCRIPT_ALIASES.items():
        if canonical_name in names:
            names.add(legacy_name)
    names.update(_LEGACY_CORE_RUNTIME_FILES)
    return names


def _add_script_identity_variants(tokens: set[str], value: str | Path | None) -> None:
    queue = [str(value or "").strip().lower()]
    seen: set[str] = set()
    while queue:
        raw = queue.pop()
        candidate = raw.strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        tokens.add(candidate)

        stem = Path(candidate).stem.strip().lower()
        if stem and stem not in seen:
            queue.append(stem)
        if candidate.startswith(PERSONAL_SCRIPT_FILENAME_PREFIX):
            stripped = candidate[len(PERSONAL_SCRIPT_FILENAME_PREFIX):].strip()
            if stripped and stripped not in seen:
                queue.append(stripped)
        if candidate.startswith("nexo-"):
            stripped = candidate[len("nexo-"):].strip()
            if stripped and stripped not in seen:
                queue.append(stripped)


def _script_identity_tokens(path: Path, meta: dict | None = None) -> set[str]:
    meta = meta or {}
    tokens: set[str] = set()
    _add_script_identity_variants(tokens, path.name)
    _add_script_identity_variants(tokens, path.stem)
    _add_script_identity_variants(tokens, meta.get("name", ""))
    return tokens


def _add_script_identities_from_dir(identities: set[str], directory: Path, *, skip_if_scripts_dir: bool = False) -> None:
    if not directory.is_dir():
        return
    if skip_if_scripts_dir:
        try:
            if directory.resolve() == get_scripts_dir().resolve():
                return
        except Exception:
            pass
    for item in directory.iterdir():
        if not item.is_file() or item.name.startswith("."):
            continue
        identities.update(_script_identity_tokens(item, parse_inline_metadata(item)))


def load_core_script_identities() -> set[str]:
    """Load every logical identifier reserved by core-managed scripts."""
    identities: set[str] = set()
    for name in load_core_script_names():
        _add_script_identity_variants(identities, name)

    packaged_src = _find_packaged_core_source_dir()
    if packaged_src is not None:
        _add_script_identities_from_dir(identities, packaged_src / "hooks")
        _add_script_identities_from_dir(identities, packaged_src / "scripts")
    else:
        _add_script_identities_from_dir(identities, paths.core_hooks_dir())
        _add_script_identities_from_dir(identities, NEXO_CODE / "hooks")
        _add_script_identities_from_dir(identities, paths.core_scripts_dir(), skip_if_scripts_dir=True)
        _add_script_identities_from_dir(identities, NEXO_CODE / "scripts", skip_if_scripts_dir=True)

    for legacy_name, canonical_name in _LEGACY_CORE_SCRIPT_ALIASES.items():
        _add_script_identity_variants(identities, legacy_name)
        _add_script_identity_variants(identities, canonical_name)
    return identities


def _script_collides_with_core_identity(path: Path, meta: dict | None = None, *, core_identities: set[str] | None = None) -> bool:
    identities = core_identities if core_identities is not None else load_core_script_identities()
    return bool(_script_identity_tokens(path, meta) & identities)


def _logical_name_collides_with_core_identity(name: str, *, core_identities: set[str] | None = None) -> bool:
    identities = core_identities if core_identities is not None else load_core_script_identities()
    probe = Path(f"{name}.py")
    return _script_collides_with_core_identity(probe, {"name": name}, core_identities=identities)


def parse_inline_metadata(path: Path) -> dict:
    """Parse inline metadata from first 25 lines.

    Supported comment prefixes:
    - # nexo:
    - // nexo:
    """
    meta: dict[str, str] = {}
    try:
        lines = path.read_text(errors="ignore").splitlines()[:25]
    except Exception:
        return meta

    for line in lines:
        stripped = line.strip()
        payload = ""
        if stripped.startswith("# nexo:"):
            payload = stripped[len("# nexo:"):].strip()
        elif stripped.startswith("// nexo:"):
            payload = stripped[len("// nexo:"):].strip()
        else:
            continue
        if "=" not in payload:
            continue
        key, value = payload.split("=", 1)
        k = key.strip()
        if k in METADATA_KEYS:
            meta[k] = _normalize_metadata_value(k, value.strip())
    return meta


def _normalize_metadata_value(key: str, value: str) -> str:
    if key == "runtime":
        return _normalize_runtime_metadata(value)
    if key == "schedule":
        return _normalize_schedule_metadata(value)
    if key == "schedule_freq":
        return str(value or "").strip().lower().replace("-", "_")
    if key == "schedule_at":
        return _normalize_schedule_at_metadata(value)
    if key == "schedule_day":
        return str(value or "").strip()
    return value


def _normalize_runtime_metadata(value: str) -> str:
    candidate = str(value or "").strip().lower()
    return _RUNTIME_METADATA_ALIASES.get(candidate, candidate)


def _normalize_schedule_metadata(value: str) -> str:
    candidate = re.sub(r"\s+", " ", str(value or "").strip())
    for pattern in (_SCHEDULE_WEEKDAY_SUFFIX_RE, _SCHEDULE_WEEKDAY_PREFIX_RE):
        match = pattern.match(candidate)
        if not match:
            continue
        try:
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            weekday = int(match.group("weekday"))
        except ValueError:
            return candidate
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= weekday <= 6:
            return f"{hour:02d}:{minute:02d}:{weekday}"
    return candidate


def _normalize_schedule_at_metadata(value: str) -> str:
    candidate = str(value or "").strip()
    match = _SCHEDULE_AT_RE.match(candidate)
    if not match:
        return candidate
    try:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
    except ValueError:
        return candidate
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return candidate


def _parse_schedule_at(value: str) -> tuple[int, int] | None:
    match = _SCHEDULE_AT_RE.match(str(value or "").strip())
    if not match:
        return None
    try:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _detect_shebang(path: Path) -> str | None:
    """Read first line for shebang."""
    try:
        first = path.read_text(errors="ignore").split("\n", 1)[0]
        if first.startswith("#!"):
            return first
    except Exception:
        pass
    return None


def classify_runtime(path: Path, metadata: dict) -> str:
    """Detect script runtime: python, shell, node, php, or unknown."""
    # 1. Metadata
    rt = metadata.get("runtime", "").lower()
    if rt in ("python", "shell", "node", "php"):
        return rt

    # 2. Shebang
    shebang = _detect_shebang(path)
    if shebang:
        if "python" in shebang:
            return "python"
        if "bash" in shebang or "/sh" in shebang:
            return "shell"
        if "node" in shebang:
            return "node"
        if "php" in shebang:
            return "php"

    # 3. Extension
    ext = path.suffix.lower()
    if ext == ".py":
        return "python"
    if ext == ".sh":
        return "shell"
    if ext == ".js":
        return "node"
    if ext == ".php":
        return "php"

    return "unknown"


def _is_ignored(path: Path) -> bool:
    """Check if file should be ignored entirely."""
    if path.name in _IGNORED_FILES:
        return True
    if re.search(r"\.bak(?:-[\w.-]+)?$", path.name, re.IGNORECASE):
        return True
    if path.name.endswith("~"):
        return True
    if path.name.startswith("."):
        return True
    try:
        relative_path = path.resolve().relative_to(get_scripts_dir().resolve())
    except Exception:
        return False
    for parent in relative_path.parents:
        if parent.name in _IGNORED_DIRS:
            return True
    return False


def _is_script_candidate(path: Path, metadata: dict | None = None) -> bool:
    metadata = metadata or {}
    runtime = classify_runtime(path, metadata)
    if runtime != "unknown":
        return True
    if _detect_shebang(path):
        return True
    try:
        return os.access(path, os.X_OK)
    except Exception:
        return False


def _truthy(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_slug(value: str) -> str:
    chars: list[str] = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {"-", "_", " "}:
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug or "script"


def has_personal_script_filename_prefix(value: str) -> bool:
    return _safe_slug(value).startswith(PERSONAL_SCRIPT_FILENAME_PREFIX)


def _logical_personal_script_name(name: str) -> str:
    slug = _safe_slug(name)
    if slug.startswith(PERSONAL_SCRIPT_FILENAME_PREFIX):
        slug = slug[len(PERSONAL_SCRIPT_FILENAME_PREFIX):]
    if slug.startswith("nexo-"):
        slug = slug[len("nexo-"):]
    return slug or "personal-script"


def _resolved_script_name(path: Path, metadata: dict | None = None, *, classification: str = "") -> str:
    metadata = metadata or {}
    raw_name = str(metadata.get("name", "") or "").strip()
    if classification == "personal":
        return _logical_personal_script_name(raw_name or path.stem)
    return raw_name or path.stem


def get_declared_schedule(metadata: dict, default_name: str = "") -> dict:
    """Parse desired schedule metadata from inline script metadata."""
    explicit_name = metadata.get("name", "").strip()
    explicit_runtime = metadata.get("runtime", "").strip().lower()
    explicit_cron_id = metadata.get("cron_id", "").strip()
    cron_id = explicit_cron_id or _safe_slug(default_name or explicit_name or "script")
    interval_raw = metadata.get("interval_seconds", "").strip()
    schedule_raw = metadata.get("schedule", "").strip()
    schedule_freq_raw = metadata.get("schedule_freq", "").strip().lower().replace("-", "_")
    schedule_at_raw = metadata.get("schedule_at", "").strip()
    schedule_day_raw = metadata.get("schedule_day", "").strip()
    anchored_raw_present = bool(schedule_freq_raw or schedule_at_raw or schedule_day_raw)
    schedule_required = _truthy(metadata.get("schedule_required"))
    recovery_policy_raw = metadata.get("recovery_policy", "").strip().lower()
    run_on_boot = _truthy(metadata.get("run_on_boot"))
    run_on_wake = _truthy(metadata.get("run_on_wake"))
    idempotent = _truthy(metadata.get("idempotent"))
    max_catchup_age_raw = metadata.get("max_catchup_age", "").strip()
    required = schedule_required or bool(interval_raw or schedule_raw or anchored_raw_present)

    if recovery_policy_raw and recovery_policy_raw not in SUPPORTED_RECOVERY_POLICIES:
        return {
            "required": required,
            "valid": False,
            "error": f"Invalid recovery_policy: {recovery_policy_raw}",
            "cron_id": cron_id,
        }

    max_catchup_age = 0
    if max_catchup_age_raw:
        try:
            max_catchup_age = int(max_catchup_age_raw)
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid max_catchup_age: {max_catchup_age_raw}",
                "cron_id": cron_id,
            }
        if max_catchup_age < 0:
            return {
                "required": required,
                "valid": False,
                "error": f"max_catchup_age must be >= 0 (got {max_catchup_age_raw})",
                "cron_id": cron_id,
            }

    if required:
        missing = []
        if not explicit_name:
            missing.append("name")
        if not explicit_runtime:
            missing.append("runtime")
        elif explicit_runtime not in SUPPORTED_RUNTIMES - {"unknown"}:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid runtime metadata for scheduled script: {explicit_runtime}",
                "cron_id": cron_id,
            }
        if not explicit_cron_id:
            missing.append("cron_id")
        if not schedule_required:
            missing.append("schedule_required=true")
        if missing:
            return {
                "required": required,
                "valid": False,
                "error": f"Scheduled scripts must declare {', '.join(missing)}",
                "cron_id": cron_id,
            }

    def _effective_run_on_boot(policy: str) -> bool:
        if "run_on_boot" in metadata:
            return run_on_boot
        return policy == "restart_daemon"

    def _effective_run_on_wake(policy: str) -> bool:
        if "run_on_wake" in metadata:
            return run_on_wake
        return policy in {"catchup", "run_once_on_wake"}

    def _effective_idempotent(policy: str) -> bool:
        if "idempotent" in metadata:
            return idempotent
        return policy in {"catchup", "run_once_on_wake", "restart", "restart_daemon"}

    configured_modes = sum(bool(value) for value in (interval_raw, schedule_raw, anchored_raw_present))
    if configured_modes > 1:
        return {
            "required": required,
            "valid": False,
            "error": "Choose only one schedule mode: schedule, interval_seconds, or schedule_freq/schedule_at.",
            "cron_id": cron_id,
        }

    if interval_raw:
        try:
            interval = int(interval_raw)
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid interval_seconds: {interval_raw}",
                "cron_id": cron_id,
            }
        if interval <= 0:
            return {
                "required": required,
                "valid": False,
                "error": f"interval_seconds must be > 0 (got {interval_raw})",
                "cron_id": cron_id,
            }
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "interval",
            "schedule_value": str(interval),
            "schedule_label": f"every {interval}s",
            "schedule": "",
            "interval_seconds": interval,
            "recovery_policy": recovery_policy_raw or "run_once_on_wake",
            "run_on_boot": run_on_boot,
            "run_on_wake": _effective_run_on_wake(recovery_policy_raw or "run_once_on_wake"),
            "idempotent": _effective_idempotent(recovery_policy_raw or "run_once_on_wake"),
            "max_catchup_age": max_catchup_age or max(interval * 4, interval + 900),
        }

    if anchored_raw_present:
        freq = schedule_freq_raw
        if freq not in SUPPORTED_ANCHORED_SCHEDULE_FREQS:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule_freq: {schedule_freq_raw or '(missing)'}",
                "cron_id": cron_id,
            }
        parsed_at = _parse_schedule_at(schedule_at_raw)
        if parsed_at is None:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule_at: {schedule_at_raw or '(missing)'}",
                "cron_id": cron_id,
            }
        hour, minute = parsed_at
        day: int | None = None
        if freq in {"weekly", "monthly", "every_n_days"}:
            try:
                day = int(schedule_day_raw)
            except ValueError:
                return {
                    "required": required,
                    "valid": False,
                    "error": f"Invalid schedule_day: {schedule_day_raw or '(missing)'}",
                    "cron_id": cron_id,
                }
        if freq == "weekly" and not (day is not None and 0 <= day <= 6):
            return {
                "required": required,
                "valid": False,
                "error": f"schedule_day for weekly schedules must be 0-6 (got {schedule_day_raw})",
                "cron_id": cron_id,
            }
        if freq == "monthly" and not (day is not None and 1 <= day <= 28):
            return {
                "required": required,
                "valid": False,
                "error": f"schedule_day for monthly schedules must be 1-28 (got {schedule_day_raw})",
                "cron_id": cron_id,
            }
        if freq == "every_n_days" and not (day is not None and 1 <= day <= 31):
            return {
                "required": required,
                "valid": False,
                "error": f"schedule_day for every_n_days schedules must be 1-31 (got {schedule_day_raw})",
                "cron_id": cron_id,
            }
        at = f"{hour:02d}:{minute:02d}"
        payload = {
            "freq": freq,
            "at": at,
            "hour": hour,
            "minute": minute,
        }
        if freq == "weekly":
            payload["weekday"] = day
            schedule = f"{at}:{day}"
            label = f"weekly weekday={day} {at}"
            max_age = 14 * 86400
        elif freq == "monthly":
            payload["day"] = day
            schedule = at
            label = f"monthly day={day} {at}"
            max_age = 45 * 86400
        elif freq == "every_n_days":
            payload["every_days"] = day
            schedule = at
            label = f"every {day}d {at}"
            max_age = max((day or 1) * 86400 + 48 * 3600, 72 * 3600)
        else:
            schedule = at
            label = f"{at} daily"
            max_age = 48 * 3600
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "calendar",
            "schedule_value": json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            "schedule_label": label,
            "schedule": schedule,
            "schedule_freq": freq,
            "schedule_at": at,
            "schedule_day": day or 0,
            "interval_seconds": 0,
            "recovery_policy": recovery_policy_raw or "catchup",
            "run_on_boot": _effective_run_on_boot(recovery_policy_raw or "catchup"),
            "run_on_wake": _effective_run_on_wake(recovery_policy_raw or "catchup"),
            "idempotent": _effective_idempotent(recovery_policy_raw or "catchup"),
            "max_catchup_age": max_catchup_age or max_age,
        }

    if schedule_raw:
        parts = schedule_raw.split(":")
        if len(parts) not in {2, 3}:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule format: {schedule_raw}",
                "cron_id": cron_id,
            }
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            weekday = int(parts[2]) if len(parts) == 3 else None
        except ValueError:
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule format: {schedule_raw}",
                "cron_id": cron_id,
            }
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule time: {schedule_raw}",
                "cron_id": cron_id,
            }
        if weekday is not None and not (0 <= weekday <= 6):
            return {
                "required": required,
                "valid": False,
                "error": f"Invalid schedule weekday: {schedule_raw}",
                "cron_id": cron_id,
            }
        label = f"{hour:02d}:{minute:02d}"
        if weekday is not None:
            label += f" weekday={weekday}"
        else:
            label += " daily"
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "calendar",
            "schedule_value": schedule_raw,
            "schedule_label": label,
            "schedule": schedule_raw,
            "interval_seconds": 0,
            "recovery_policy": recovery_policy_raw or "catchup",
            "run_on_boot": _effective_run_on_boot(recovery_policy_raw or "catchup"),
            "run_on_wake": _effective_run_on_wake(recovery_policy_raw or "catchup"),
            "idempotent": _effective_idempotent(recovery_policy_raw or "catchup"),
            "max_catchup_age": max_catchup_age or (14 * 86400 if weekday is not None else 48 * 3600),
        }

    if required and recovery_policy_raw == "restart_daemon":
        return {
            "required": required,
            "valid": True,
            "cron_id": cron_id,
            "schedule_type": "keep_alive",
            "schedule_value": "true",
            "schedule_label": "keep alive",
            "schedule": "",
            "interval_seconds": 0,
            "recovery_policy": "restart_daemon",
            "run_on_boot": _effective_run_on_boot("restart_daemon"),
            "run_on_wake": _effective_run_on_wake("restart_daemon"),
            "idempotent": _effective_idempotent("restart_daemon"),
            "max_catchup_age": max_catchup_age,
        }

    return {
        "required": required,
        "valid": not required,
        "error": "" if not required else "schedule_required=true but no schedule metadata was provided.",
        "cron_id": cron_id,
        "recovery_policy": recovery_policy_raw or "none",
        "run_on_boot": run_on_boot,
        "run_on_wake": run_on_wake,
        "idempotent": idempotent,
        "max_catchup_age": max_catchup_age,
    }


def _script_entry(path: Path, meta: dict, *, is_core: bool, classification: str, reason: str = "") -> dict:
    runtime = classify_runtime(path, meta)
    name = _resolved_script_name(path, meta, classification=classification)
    entry = {
        "name": name,
        "runtime": runtime,
        "description": meta.get("description", ""),
        "path": str(path),
        "core": is_core,
        "metadata": meta,
        "classification": classification,
        "reason": reason,
        "declared_schedule": get_declared_schedule(meta, name),
        "filename_prefixed": has_personal_script_filename_prefix(path.stem),
    }
    if classification == "personal":
        entry["naming_policy"] = "preferred" if entry["filename_prefixed"] else "legacy-nonprefixed"
    return entry


def classify_scripts_dir() -> dict:
    """Classify every file in scripts dirs into personal/core/ignored/non-script buckets.

    Plan F0.6 — scans every dir returned by paths.all_scripts_dirs():
    core/scripts, personal/scripts, core-dev/scripts. Falls back to the
    legacy ~/.nexo/scripts/ if the new layout has no entries (transition
    safety; remove the fallback once F0.6 has been validated for a week).
    """
    _apply_legacy_personal_script_backfills()
    import paths
    candidate_dirs = list(paths.all_scripts_dirs())
    legacy = get_scripts_dir()
    if legacy.is_dir() and legacy not in candidate_dirs:
        candidate_dirs.append(legacy)

    core_names = load_core_script_names()
    core_identities = load_core_script_identities()
    entries: list[dict] = []
    # Dedup by resolved real path so the same physical file surfaced
    # from two candidate dirs (e.g. core/scripts/foo.sh + the F0.6
    # legacy fallback ~/.nexo/scripts/foo.sh pointing at the same inode
    # via symlink) appears once. Two distinct files that happen to share
    # a filename resolve to different paths and both survive — preserves
    # the D2 audit fix without the cosmetic duplicate the AUDITOR-V700-
    # PASS2 §5 transitional window flagged.
    seen_real_paths: set[str] = set()
    for sdir in candidate_dirs:
        if not sdir.is_dir():
            continue
        # Decide default classification per directory: a script dropped
        # in core/scripts is core unless its name says otherwise; same
        # for personal and core-dev.
        if "core-dev" in sdir.parts:
            dir_classification = "core-dev"
        elif "personal" in sdir.parts:
            dir_classification = "personal"
        elif "core" in sdir.parts:
            dir_classification = "core"
        else:
            dir_classification = None  # legacy — fall back to name-based
        for f in sorted(sdir.iterdir()):
            if not f.is_file():
                continue
            try:
                real_path = str(f.resolve(strict=False))
            except OSError:
                real_path = str(f)
            if real_path in seen_real_paths:
                continue
            seen_real_paths.add(real_path)
            meta = parse_inline_metadata(f)
            if _is_ignored(f):
                entries.append(_script_entry(f, meta, is_core=False, classification="ignored", reason="internal or hidden artifact"))
                continue
            if not _is_script_candidate(f, meta):
                entries.append(_script_entry(f, meta, is_core=False, classification="non-script", reason="not an executable/script candidate"))
                continue
            if dir_classification:
                cls = dir_classification
                is_core = cls in ("core", "core-dev")
            else:
                is_core = f.name in core_names
                cls = "core" if is_core else "personal"
            if not is_core and _script_collides_with_core_identity(f, meta, core_identities=core_identities):
                entries.append(
                    _script_entry(
                        f,
                        meta,
                        is_core=False,
                        classification="ignored",
                        reason="shadowed by core script identity",
                    )
                )
                continue
            entries.append(_script_entry(f, meta, is_core=is_core, classification=cls))

    summary: dict[str, int] = {}
    for entry in entries:
        summary[entry["classification"]] = summary.get(entry["classification"], 0) + 1
    return {"scripts_dir": str(candidate_dirs[0]) if candidate_dirs else "", "entries": entries, "summary": summary}


def list_scripts(include_core: bool = False) -> list[dict]:
    """List scripts in NEXO_HOME/personal/scripts/.

    By default only personal scripts. With include_core=True, also shows core/cron scripts.

    Plan F0.2.4 fix — every entry now carries an `enabled` field
    hydrated from the `personal_scripts` table. Core entries default
    to True (they ship enabled and are not toggleable from this entry
    point); personal entries default to True when no row exists yet
    (sync hasn't run) so the Desktop toggle has a stable starting
    state. The flag is what powers the Settings -> Automatizaciones
    toggle's round-trip.
    """
    # Build a path -> enabled map once so we don't open a transaction
    # per entry. Personal_scripts rows that don't match anything in
    # classify_scripts_dir() are simply ignored.
    row_map: dict[str, dict] = {}
    if include_core:
        # Only the Desktop panel (include_core=True) needs the toggle
        # round-trip. Gating the DB read this way avoids triggering
        # init_db() in default callers (eg `nexo scripts list`), which
        # would surface pre-existing test fixture pollution.
        try:
            from db import init_db
            from db._personal_scripts import list_personal_scripts
            init_db()
            for row in list_personal_scripts(include_disabled=True, include_core=True):
                p = row.get("path")
                if p:
                    row_map[str(p)] = row
        except Exception:
            # Missing table (older runtime), locked DB, anything else:
            # fall through to enabled=True (the cron wrapper gate is
            # the source of truth at run time).
            row_map = {}

    entries = classify_scripts_dir()["entries"]
    results = []
    cron_ids: list[str] = []
    cron_id_by_path: dict[str, str] = {}
    for entry in entries:
        if entry["classification"] not in {"personal", "core"}:
            continue
        if entry["core"] and not include_core:
            continue
        hidden = _truthy(entry.get("metadata", {}).get("hidden"))
        if hidden and not include_core:
            continue
        row = row_map.get(entry["path"]) or {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        try:
            from automation_controls import get_script_runtime_contract
            contract = get_script_runtime_contract(entry["name"])
        except Exception:
            contract = {
                "toggleable_core": False,
                "supports_extra_instructions": False,
                "requires_email_account": False,
                "available": True,
                "blocked_reason": "",
                "blocked_reason_code": "",
                "eligible_labels": [],
            }
        entry["enabled"] = bool(row.get("enabled", True))
        entry["can_toggle"] = (
            entry["classification"] == "personal"
            or bool(contract.get("toggleable_core"))
        )
        entry["supports_extra_instructions"] = bool(contract.get("supports_extra_instructions"))
        entry["supports_automation_preferences"] = bool(contract.get("supports_automation_preferences"))
        entry["operator_extra_instructions"] = str(metadata.get("operator_extra_instructions") or "")
        entry["runtime_contract"] = contract
        entry["available"] = bool(contract.get("available", True))
        entry["blocked_reason"] = str(contract.get("blocked_reason") or "")
        entry["blocked_reason_code"] = str(contract.get("blocked_reason_code") or "")
        entry["eligible_labels"] = list(contract.get("eligible_labels") or [])
        entry["schedule_configurable"] = bool(contract.get("schedule_configurable"))
        entry["schedule_type"] = str(contract.get("schedule_type") or "")
        entry["schedule_source"] = str(contract.get("schedule_source") or "")
        entry["effective_schedule_label"] = str(contract.get("effective_schedule_label") or "")
        entry["schedule"] = contract.get("schedule")
        entry["default_schedule"] = contract.get("default_schedule")
        entry["interval_seconds"] = int(contract.get("interval_seconds", 0) or 0)
        entry["default_interval_seconds"] = int(contract.get("default_interval_seconds", 0) or 0)
        entry["minimum_interval_seconds"] = int(contract.get("minimum_interval_seconds", 0) or 0)
        entry["maximum_interval_seconds"] = int(contract.get("maximum_interval_seconds", 0) or 0)
        entry["interval_step_seconds"] = int(contract.get("interval_step_seconds", 0) or 0)
        if entry["effective_schedule_label"]:
            entry["schedules"] = [{
                "schedule_label": entry["effective_schedule_label"],
                "schedule_source": entry["schedule_source"],
                "schedule_type": entry["schedule_type"],
                "interval_seconds": entry["interval_seconds"],
            }]
        declared = entry.get("declared_schedule") if isinstance(entry.get("declared_schedule"), dict) else {}
        cron_id = str(declared.get("cron_id") or entry.get("name") or "").strip()
        if cron_id:
            cron_ids.append(cron_id)
            cron_id_by_path[entry["path"]] = cron_id
        results.append(entry)

    latest_runs = {}
    if include_core and cron_ids:
        try:
            from db import init_db
            from db._core import get_db

            init_db()
            conn = get_db()
            placeholders = ",".join("?" for _ in cron_ids)
            rows = conn.execute(
                f"""
                SELECT c1.cron_id, c1.started_at, c1.exit_code, c1.summary
                FROM cron_runs c1
                JOIN (
                    SELECT cron_id, MAX(id) AS max_id
                    FROM cron_runs
                    WHERE cron_id IN ({placeholders})
                    GROUP BY cron_id
                ) latest ON latest.max_id = c1.id
                """,
                tuple(cron_ids),
            ).fetchall()
            latest_runs = {
                str(row["cron_id"]): {
                    "started_at": row["started_at"],
                    "exit_code": row["exit_code"],
                    "summary": row["summary"],
                }
                for row in rows
            }
        except Exception:
            latest_runs = {}

    for entry in results:
        cron_id = cron_id_by_path.get(entry["path"], "")
        latest = latest_runs.get(cron_id)
        if latest:
            entry["last_run_at"] = latest.get("started_at")
            entry["last_exit_code"] = latest.get("exit_code")
            entry["last_summary"] = str(latest.get("summary") or "")
    return results


def _product_automation_sort_key(row: dict) -> tuple[int, str]:
    name = str((row or {}).get("name") or "")
    try:
        index = PRODUCT_AUTOMATION_NAMES.index(name)
    except ValueError:
        index = len(PRODUCT_AUTOMATION_NAMES)
    return (index, name)


def list_operator_automations(*, include_all: bool = False) -> list[dict]:
    """Return the Desktop/operator-facing automation catalog."""
    from db import init_db

    init_db()
    sync_personal_scripts()
    rows = list_scripts(include_core=True)
    if not include_all:
        allowed = set(PRODUCT_AUTOMATION_NAMES)
        rows = [row for row in rows if str(row.get("name") or "") in allowed]
    rows.sort(key=_product_automation_sort_key)
    return rows


def _within_scripts_dir(path: Path) -> bool:
    try:
        path.resolve().relative_to(get_scripts_dir().resolve())
        return True
    except Exception:
        return False


def resolve_script(name: str) -> dict | None:
    """Find a script by name (metadata name or filename stem)."""
    for entry in classify_scripts_dir()["entries"]:
        if entry.get("classification") not in {"personal", "core", "core-dev"}:
            continue
        path = Path(entry["path"])
        script_name = entry.get("name", path.stem)
        if script_name == name or path.stem == name or path.name == name:
            return {
                "name": script_name,
                "runtime": entry.get("runtime", "unknown"),
                "description": entry.get("description", ""),
                "path": entry["path"],
                "core": bool(entry.get("core")),
                "metadata": entry.get("metadata", {}),
                "classification": entry.get("classification", ""),
            }
    return None


def resolve_script_reference(ref: str) -> dict | None:
    """Resolve a script by name or by direct filesystem path."""
    direct = Path(ref)
    if direct.is_file():
        meta = parse_inline_metadata(direct)
        return {
            "name": meta.get("name", direct.stem),
            "runtime": classify_runtime(direct, meta),
            "description": meta.get("description", ""),
            "path": str(direct),
            "core": direct.name in load_core_script_names(),
            "metadata": meta,
        }
    return resolve_script(ref)


def _extract_script_path_from_program_args(program_args: list) -> Path | None:
    candidate = _extract_script_path_candidate(program_args)
    if candidate is None:
        return None
    if not candidate.is_file():
        return None
    if not _within_scripts_dir(candidate):
        return None
    if _is_ignored(candidate):
        return None
    return candidate


def _extract_script_path_candidate(program_args: list) -> Path | None:
    candidates: list[Path] = []
    for arg in program_args or []:
        if not isinstance(arg, str):
            continue
        candidate = Path(arg).expanduser()
        if not str(candidate).startswith("/") and not str(arg).startswith("~"):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    return candidates[-1]


def _format_schedule_from_plist(plist_data: dict) -> tuple[str, str, str]:
    if plist_data.get("KeepAlive") is True:
        return "keep_alive", "true", "keep alive"
    if plist_data.get("RunAtLoad") is True and "StartInterval" not in plist_data and "StartCalendarInterval" not in plist_data:
        return "run_at_load", "true", "run at load"

    if "StartInterval" in plist_data:
        interval = int(plist_data["StartInterval"])
        return "interval", str(interval), f"every {interval}s"

    cal = plist_data.get("StartCalendarInterval")
    if cal:
        if isinstance(cal, list):
            value = json.dumps(cal, ensure_ascii=False)
            return "calendar", value, "calendar"
        hour = cal.get("Hour")
        minute = cal.get("Minute")
        weekday = cal.get("Weekday")
        day = cal.get("Day")
        if day is not None and hour is not None and minute is not None:
            return "calendar", json.dumps(cal, ensure_ascii=False), f"{hour:02d}:{minute:02d} day={day}"
        if weekday is not None and hour is not None and minute is not None:
            return "calendar", json.dumps(cal, ensure_ascii=False), f"{hour:02d}:{minute:02d} weekday={weekday}"
        if hour is not None and minute is not None:
            return "calendar", json.dumps(cal, ensure_ascii=False), f"{hour:02d}:{minute:02d} daily"
        return "calendar", json.dumps(cal, ensure_ascii=False), "calendar"

    return "manual", "", ""


def _anchored_schedule_payload(schedule_value: str | dict | list | None) -> dict | None:
    if isinstance(schedule_value, dict):
        payload = schedule_value
    elif isinstance(schedule_value, str) and schedule_value.lstrip().startswith("{"):
        try:
            payload = json.loads(schedule_value)
        except Exception:
            return None
    else:
        return None
    if not isinstance(payload, dict):
        return None
    freq = str(payload.get("freq") or "").strip()
    if freq not in SUPPORTED_ANCHORED_SCHEDULE_FREQS:
        return None
    at = str(payload.get("at") or "").strip()
    if _parse_schedule_at(at) is None:
        return None
    return payload


def _calendar_payload_from_declared(schedule_value: str) -> dict | list | None:
    if not schedule_value:
        return None
    if schedule_value.lstrip().startswith("{") or schedule_value.lstrip().startswith("["):
        try:
            parsed = json.loads(schedule_value)
        except Exception:
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

    parts = schedule_value.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        weekday = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None

    payload = {"Hour": hour, "Minute": minute}
    if weekday is not None:
        payload["Weekday"] = weekday
    return payload


def _canonical_schedule_value(schedule_type: str, schedule_value: str | dict | list) -> str:
    if schedule_type == "calendar":
        payload = _calendar_payload_from_declared(str(schedule_value)) if isinstance(schedule_value, str) else schedule_value
        if payload is None:
            return str(schedule_value or "")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(schedule_value or "")


def _metadata_comment_prefix(path: Path) -> str:
    return "//" if path.suffix.lower() == ".js" else "#"


def _compact_schedule_from_record(record: dict) -> str:
    raw = str(record.get("schedule_value") or "").strip()
    if not raw:
        return ""
    payload = None
    if raw.lstrip().startswith("{") or raw.lstrip().startswith("["):
        with contextlib.suppress(Exception):
            payload = json.loads(raw)
    anchored = _anchored_schedule_payload(payload)
    if anchored:
        return str(anchored.get("at") or "")
    if isinstance(payload, list):
        if len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]
        else:
            return ""
    if isinstance(payload, dict):
        hour = payload.get("Hour")
        minute = payload.get("Minute")
        weekday = payload.get("Weekday")
        try:
            hour_i = int(hour)
            minute_i = int(minute)
            weekday_i = int(weekday) if weekday is not None else None
        except (TypeError, ValueError):
            return ""
        if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
            return ""
        if weekday_i is not None:
            if not (0 <= weekday_i <= 6):
                return ""
            return f"{hour_i:02d}:{minute_i:02d}:{weekday_i}"
        return f"{hour_i:02d}:{minute_i:02d}"

    normalized = _normalize_schedule_metadata(raw)
    if _calendar_payload_from_declared(normalized) is not None:
        return normalized

    label = str(record.get("schedule_label") or "").strip()
    match = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s+weekday=(?P<weekday>\d))?", label)
    if not match:
        return ""
    weekday_part = f":{match.group('weekday')}" if match.group("weekday") is not None else ""
    return f"{int(match.group('hour')):02d}:{int(match.group('minute')):02d}{weekday_part}"


def _inferred_schedule_metadata_lines(path: Path, metadata: dict, record: dict) -> list[str] | None:
    schedule_type = str(record.get("schedule_type") or "")
    if schedule_type not in {"interval", "calendar", "keep_alive"}:
        return None

    name = _logical_personal_script_name(str(metadata.get("name") or path.stem))
    description = str(metadata.get("description") or "Personal automation managed by NEXO").strip()
    runtime = classify_runtime(path, metadata)
    if runtime == "unknown":
        runtime = "shell" if path.suffix.lower() == ".sh" else "python"
    cron_id = _safe_slug(str(record.get("cron_id") or metadata.get("cron_id") or name))
    prefix = _metadata_comment_prefix(path)

    lines = [
        f"{prefix} nexo: name={name}",
        f"{prefix} nexo: description={description}",
        f"{prefix} nexo: runtime={runtime}",
        f"{prefix} nexo: cron_id={cron_id}",
        f"{prefix} nexo: schedule_required=true",
    ]
    if schedule_type == "interval":
        try:
            interval = int(str(record.get("schedule_value") or "").strip())
        except ValueError:
            return None
        if interval <= 0:
            return None
        lines.append(f"{prefix} nexo: interval_seconds={interval}")
        lines.append(f"{prefix} nexo: recovery_policy=run_once_on_wake")
    elif schedule_type == "calendar":
        anchored = _anchored_schedule_payload(record.get("schedule_value"))
        if anchored:
            freq = str(anchored.get("freq") or "")
            at = str(anchored.get("at") or "")
            if freq == "weekly":
                day = anchored.get("weekday")
            elif freq == "every_n_days":
                day = anchored.get("every_days")
            else:
                day = anchored.get("day")
            lines.append(f"{prefix} nexo: schedule_freq={freq}")
            lines.append(f"{prefix} nexo: schedule_at={at}")
            if freq in {"weekly", "monthly", "every_n_days"}:
                lines.append(f"{prefix} nexo: schedule_day={day}")
        else:
            compact_schedule = _compact_schedule_from_record(record)
            if not compact_schedule:
                return None
            lines.append(f"{prefix} nexo: schedule={compact_schedule}")
        lines.append(f"{prefix} nexo: recovery_policy=catchup")
    elif schedule_type == "keep_alive":
        lines.append(f"{prefix} nexo: recovery_policy=restart_daemon")

    if record.get("run_at_load"):
        lines.append(f"{prefix} nexo: run_on_boot=true")
    return lines


def _write_metadata_block(path: Path, metadata_lines: list[str]) -> None:
    raw = path.read_text(errors="ignore")
    lines = raw.splitlines(keepends=True)
    insert_at = 1 if lines and lines[0].startswith("#!") else 0
    filtered: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if index >= insert_at and index < 25 and (
            stripped.startswith("# nexo:") or stripped.startswith("// nexo:")
        ):
            continue
        filtered.append(line)
    block = [line.rstrip("\n") + "\n" for line in metadata_lines]
    filtered[insert_at:insert_at] = block
    path.write_text("".join(filtered))


def _metadata_lines_from_values(path: Path, metadata: dict) -> list[str]:
    prefix = _metadata_comment_prefix(path)
    keys = [key for key in METADATA_WRITE_ORDER if key in metadata]
    keys.extend(sorted(key for key in metadata if key in METADATA_KEYS and key not in keys))
    lines: list[str] = []
    for key in keys:
        value = str(metadata.get(key) or "").strip()
        if not value:
            continue
        lines.append(f"{prefix} nexo: {key}={value}")
    return lines


def _unknown_inline_metadata_lines(path: Path) -> list[str]:
    try:
        raw = path.read_text(errors="ignore")
    except Exception:
        return []
    lines = raw.splitlines(keepends=True)
    insert_at = 1 if lines and lines[0].startswith("#!") else 0
    preserved: list[str] = []
    for index, line in enumerate(lines):
        if index < insert_at or index >= 25:
            continue
        stripped = line.strip()
        payload = ""
        if stripped.startswith("# nexo:"):
            payload = stripped[len("# nexo:"):].strip()
        elif stripped.startswith("// nexo:"):
            payload = stripped[len("// nexo:"):].strip()
        if "=" not in payload:
            continue
        key, _value = payload.split("=", 1)
        if key.strip() not in METADATA_KEYS:
            preserved.append(line.rstrip("\n") + "\n")
    return preserved


def _update_script_metadata(path: Path, updates: dict, *, remove: set[str] | None = None) -> dict:
    metadata = dict(parse_inline_metadata(path))
    for key in remove or set():
        metadata.pop(key, None)
    for key, value in updates.items():
        if key not in METADATA_KEYS:
            continue
        text = str(value or "").strip()
        if text:
            metadata[key] = _normalize_metadata_value(key, text)
        else:
            metadata.pop(key, None)
    _write_metadata_block(path, _unknown_inline_metadata_lines(path) + _metadata_lines_from_values(path, metadata))
    return parse_inline_metadata(path)


def _is_agent_metadata(metadata: dict | None) -> bool:
    return _truthy((metadata or {}).get("agent"))


def _is_agent_archived(metadata: dict | None) -> bool:
    return _truthy((metadata or {}).get("agent_archived"))


def _format_agent_calendar_value(raw: str) -> tuple[str, str]:
    payload = _anchored_schedule_payload(raw)
    if payload:
        at = str(payload.get("at") or "")
        freq = str(payload.get("freq") or "")
        if freq == "weekly":
            return at, f"weekly weekday={int(payload.get('weekday'))} {at}"
        if freq == "monthly":
            return at, f"monthly day={int(payload.get('day'))} {at}"
        if freq == "every_n_days":
            return at, f"every {int(payload.get('every_days'))}d {at}"
        return at, f"{at} daily"

    compact = _compact_schedule_from_record({
        "schedule_type": "calendar",
        "schedule_value": raw,
        "schedule_label": raw,
    })
    if not compact:
        return "", ""
    parts = compact.split(":")
    if len(parts) not in {2, 3}:
        return "", "calendar"
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        weekday = int(parts[2]) if len(parts) == 3 else None
    except (TypeError, ValueError):
        return "", "calendar"
    if len(parts) == 3:
        return compact, f"{hour:02d}:{minute:02d} weekday={weekday}"
    return compact, f"{hour:02d}:{minute:02d} daily"


def _agent_anchored_schedule_fields(schedule_value: str | dict | list | None) -> dict:
    payload = _anchored_schedule_payload(schedule_value)
    if not payload:
        return {
            "schedule_freq": "",
            "schedule_at": "",
            "schedule_day": 0,
        }
    freq = str(payload.get("freq") or "")
    if freq == "weekly":
        day = int(payload.get("weekday"))
    elif freq == "monthly":
        day = int(payload.get("day"))
    elif freq == "every_n_days":
        day = int(payload.get("every_days"))
    else:
        day = 0
    return {
        "schedule_freq": freq,
        "schedule_at": str(payload.get("at") or ""),
        "schedule_day": day,
    }


def _agent_schedule_from_script(script: dict) -> dict:
    schedules = script.get("schedules") if isinstance(script.get("schedules"), list) else []
    if schedules:
        schedule = dict(schedules[0])
        schedule_type = str(schedule.get("schedule_type") or "")
        schedule_value = str(schedule.get("schedule_value") or "")
        label = str(schedule.get("schedule_label") or schedule_value or schedule_type)
        interval_seconds = 0
        daily_at = ""
        if schedule_type == "interval":
            with contextlib.suppress(Exception):
                interval_seconds = int(schedule_value)
        elif schedule_type == "calendar":
            daily_at, formatted = _format_agent_calendar_value(schedule_value)
            label = formatted or label
        anchored_fields = _agent_anchored_schedule_fields(schedule_value)
        return {
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "schedule_label": label,
            "effective_schedule_label": label,
            "interval_seconds": interval_seconds,
            "daily_at": daily_at,
            **anchored_fields,
            "cron_id": str(schedule.get("cron_id") or ""),
            "schedule_source": "runtime",
            "schedules": schedules,
        }

    metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
    declared = get_declared_schedule(metadata, str(script.get("name") or ""))
    if declared.get("valid") and declared.get("required"):
        return {
            "schedule_type": str(declared.get("schedule_type") or ""),
            "schedule_value": str(declared.get("schedule_value") or ""),
            "schedule_label": str(declared.get("schedule_label") or ""),
            "effective_schedule_label": str(declared.get("schedule_label") or ""),
            "interval_seconds": int(declared.get("interval_seconds", 0) or 0),
            "daily_at": str(declared.get("schedule") or ""),
            "schedule_freq": str(declared.get("schedule_freq") or ""),
            "schedule_at": str(declared.get("schedule_at") or ""),
            "schedule_day": int(declared.get("schedule_day", 0) or 0),
            "cron_id": str(declared.get("cron_id") or ""),
            "schedule_source": "metadata",
            "schedules": [],
        }

    return {
        "schedule_type": "manual",
        "schedule_value": "",
        "schedule_label": "",
        "effective_schedule_label": "",
        "interval_seconds": 0,
        "daily_at": "",
        "schedule_freq": "",
        "schedule_at": "",
        "schedule_day": 0,
        "cron_id": str(metadata.get("cron_id") or script.get("name") or ""),
        "schedule_source": "",
        "schedules": [],
    }


def _agent_health(script: dict) -> str:
    metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
    if _is_agent_archived(metadata):
        return "archived"
    if not bool(script.get("enabled", True)):
        return "disabled"
    exit_code = script.get("last_exit_code")
    if exit_code is None:
        return "unknown"
    return "ok" if exit_code == 0 else "failing"


def _agent_row(script: dict) -> dict:
    metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
    schedule = _agent_schedule_from_script(script)
    title = str(metadata.get("agent_title") or script.get("name") or "").strip()
    description = str(
        metadata.get("agent_description")
        or script.get("description")
        or metadata.get("description")
        or ""
    ).strip()
    return {
        "name": script.get("name", ""),
        "title": title,
        "description": description,
        "path": script.get("path", ""),
        "runtime": script.get("runtime", "unknown"),
        "enabled": bool(script.get("enabled", True)),
        "archived": _is_agent_archived(metadata),
        "health": _agent_health(script),
        "last_run_at": script.get("last_run_at", ""),
        "last_exit_code": script.get("last_exit_code"),
        "conversation_id": str(metadata.get("agent_conversation_id") or ""),
        "created_from": str(metadata.get("agent_created_from") or ""),
        "icon": str(metadata.get("agent_icon") or "automation"),
        "metadata": metadata,
        "schedule_configurable": True,
        **schedule,
    }


def list_agents(*, include_archived: bool = False) -> list[dict]:
    """List personal scripts explicitly marked as NEXO agents."""
    from db import init_db, list_personal_scripts

    init_db()
    sync_personal_scripts()
    rows = []
    for script in list_personal_scripts(include_disabled=True):
        metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
        if not _is_agent_metadata(metadata):
            continue
        if _is_agent_archived(metadata) and not include_archived:
            continue
        rows.append(_agent_row(script))
    rows.sort(key=lambda row: (bool(row.get("archived")), str(row.get("title") or row.get("name") or "").lower()))
    return rows


def get_agent_status(name_or_path: str) -> dict:
    from db import init_db, get_personal_script, list_personal_scripts

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path)
    if not script:
        return {"ok": False, "error": f"Agent not found: {name_or_path}"}
    metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
    if not _is_agent_metadata(metadata):
        return {"ok": False, "error": f"Personal script is not marked as an agent: {name_or_path}"}
    for row in list_personal_scripts(include_disabled=True):
        if row.get("path") == script.get("path"):
            script = row
            break
    return {"ok": True, "agent": _agent_row(script)}


def create_agent_script(name: str, *, description: str = "", runtime: str = "python", force: bool = False) -> dict:
    created = create_script(name, description=description, runtime=runtime, force=force)
    path = Path(created["path"])
    metadata = parse_inline_metadata(path)
    updates = {
        "agent": "true",
        "agent_title": name,
        "agent_description": description or metadata.get("description") or f"Agent: {created['name']}",
    }
    _update_script_metadata(path, updates)
    sync_result = sync_personal_scripts()
    return {
        **created,
        "agent": True,
        "sync": sync_result,
    }


def set_agent_enabled(name_or_path: str, enabled: bool) -> dict:
    status = get_agent_status(name_or_path)
    if not status.get("ok"):
        return status
    agent = status["agent"]
    if enabled and agent.get("archived"):
        _update_script_metadata(Path(agent["path"]), {"agent_archived": "false"})
    result = set_personal_script_enabled(agent["path"], enabled)
    if not result.get("ok"):
        return result
    refreshed = get_agent_status(agent["path"])
    return {
        "ok": True,
        "name": agent["name"],
        "enabled": enabled,
        "changed": bool(result.get("changed")),
        "agent": refreshed.get("agent") if refreshed.get("ok") else agent,
    }


def archive_agent(name_or_path: str, *, archived: bool = True) -> dict:
    status = get_agent_status(name_or_path)
    if not status.get("ok"):
        return status
    agent = status["agent"]
    path = Path(agent["path"])
    metadata = parse_inline_metadata(path)
    if archived:
        _update_script_metadata(path, {
            "agent_archived": "true",
            "agent_enabled_before_archive": "true" if agent.get("enabled", True) else "false",
        })
        next_enabled = False
    else:
        previous_enabled = metadata.get("agent_enabled_before_archive")
        next_enabled = _truthy(previous_enabled) if previous_enabled else True
        _update_script_metadata(path, {"agent_archived": "false"}, remove={"agent_enabled_before_archive"})
    toggle = set_personal_script_enabled(agent["path"], next_enabled)
    refreshed = get_agent_status(agent["path"])
    return {
        "ok": bool(toggle.get("ok", True)) and bool(refreshed.get("ok", True)),
        "name": agent["name"],
        "archived": archived,
        "agent": refreshed.get("agent") if refreshed.get("ok") else agent,
    }


def _agent_schedule_ensure_error(result: dict, *, cron_id: str, path: Path) -> str:
    if not isinstance(result, dict):
        return "schedule ensure returned an invalid response"
    if result.get("ok") is False:
        return str(result.get("error") or "schedule ensure failed")
    path_text = str(path)
    for item in result.get("invalid", []) if isinstance(result.get("invalid"), list) else []:
        if item.get("path") == path_text or item.get("cron_id") == cron_id:
            return str(item.get("error") or "invalid schedule metadata")
    for bucket in ("created", "repaired"):
        for item in result.get(bucket, []) if isinstance(result.get(bucket), list) else []:
            if item.get("cron_id") != cron_id:
                continue
            response = str(item.get("result") or "")
            if response.upper().startswith("ERROR:"):
                return response
    return ""


def set_agent_schedule(
    name_or_path: str,
    *,
    interval_seconds: int | None = None,
    daily_at: str | None = None,
    schedule_freq: str | None = None,
    schedule_at: str | None = None,
    schedule_day: int | str | None = None,
    clear: bool = False,
) -> dict:
    status = get_agent_status(name_or_path)
    if not status.get("ok"):
        return status
    agent = status["agent"]
    path = Path(agent["path"])
    metadata = parse_inline_metadata(path)
    cron_id = _safe_slug(str(metadata.get("cron_id") or agent.get("name") or path.stem))
    runtime = classify_runtime(path, metadata)
    if runtime == "unknown":
        runtime = "shell" if path.suffix.lower() == ".sh" else "python"

    remove = {
        "schedule",
        "schedule_freq",
        "schedule_at",
        "schedule_day",
        "interval_seconds",
        "recovery_policy",
        "run_on_boot",
        "run_on_wake",
        "idempotent",
        "max_catchup_age",
    }
    updates = {
        "agent": "true",
        "name": metadata.get("name") or agent.get("name") or _logical_personal_script_name(path.stem),
        "description": metadata.get("description") or agent.get("description") or f"Agent: {agent.get('name')}",
        "runtime": runtime,
        "cron_id": cron_id,
    }
    if clear:
        remove.add("schedule_required")
        _update_script_metadata(path, updates, remove=remove)
        removed = unschedule_personal_script(str(path))
        sync_result = sync_personal_scripts()
        refreshed = get_agent_status(str(path))
        return {
            "ok": bool(removed.get("ok", True)),
            "name": agent["name"],
            "cleared": True,
            "removed": removed,
            "sync": sync_result,
            "agent": refreshed.get("agent") if refreshed.get("ok") else agent,
        }

    anchored_requested = bool(schedule_freq or schedule_at or schedule_day is not None)
    mode_count = sum(bool(value) for value in (interval_seconds is not None, bool(daily_at), anchored_requested))
    if mode_count > 1:
        return {"ok": False, "error": "Choose interval_seconds, daily_at, or schedule_freq/schedule_at."}

    if interval_seconds is not None:
        try:
            interval = int(interval_seconds)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"Invalid interval_seconds: {interval_seconds}"}
        if interval <= 0:
            return {"ok": False, "error": "interval_seconds must be > 0"}
        updates.update({
            "schedule_required": "true",
            "interval_seconds": str(interval),
            "recovery_policy": "run_once_on_wake",
        })
    elif anchored_requested:
        freq = str(schedule_freq or "").strip().lower().replace("-", "_")
        at = _normalize_schedule_at_metadata(str(schedule_at or "").strip())
        updates.update({
            "schedule_required": "true",
            "schedule_freq": freq,
            "schedule_at": at,
            "recovery_policy": "catchup",
        })
        if schedule_day is not None:
            updates["schedule_day"] = str(schedule_day)
    elif daily_at:
        schedule_value = _normalize_schedule_metadata(str(daily_at).strip())
        updates.update({
            "schedule_required": "true",
            "schedule": schedule_value,
            "recovery_policy": "catchup",
        })
    else:
        return {"ok": False, "error": "Choose interval_seconds, daily_at, schedule_freq/schedule_at, or clear=true"}

    candidate_metadata = dict(metadata)
    for key in remove:
        candidate_metadata.pop(key, None)
    for key, value in updates.items():
        if key in METADATA_KEYS:
            candidate_metadata[key] = _normalize_metadata_value(key, str(value or ""))
    declared = get_declared_schedule(candidate_metadata, str(agent.get("name") or path.stem))
    if not declared.get("valid"):
        return {
            "ok": False,
            "error": str(declared.get("error") or "invalid schedule metadata"),
            "cron_id": cron_id,
        }

    _update_script_metadata(path, updates, remove=remove)
    removed = unschedule_personal_script(str(path))
    ensured = ensure_personal_schedules(dry_run=False)
    ensure_error = _agent_schedule_ensure_error(ensured, cron_id=cron_id, path=path)
    refreshed = get_agent_status(str(path))
    return {
        "ok": not ensure_error,
        "name": agent["name"],
        "cron_id": cron_id,
        "declared_schedule": declared,
        "error": ensure_error,
        "removed": removed,
        "ensure_schedules": ensured,
        "agent": refreshed.get("agent") if refreshed.get("ok") else agent,
    }


def repair_orphan_personal_schedule_metadata(*, dry_run: bool = False) -> dict:
    """Infer inline metadata for personal LaunchAgents that predate the registry.

    This powers ``nexo doctor --fix`` and ``nexo scripts reconcile`` for the
    legacy case where a user-owned LaunchAgent exists but the script has no
    declared schedule metadata. It never touches scripts outside the personal
    scripts directory.
    """
    classification = classify_scripts_dir()
    personal_scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    scripts_by_path = {
        str(Path(entry["path"]).expanduser().resolve(strict=False)): entry
        for entry in personal_scripts
    }
    report = {
        "ok": True,
        "dry_run": dry_run,
        "repaired": [],
        "skipped": [],
        "errors": [],
    }
    for record in _discover_personal_schedule_records():
        script_path = str(record.get("script_path") or "")
        if not script_path:
            report["skipped"].append({"cron_id": record.get("cron_id", ""), "reason": "missing script path"})
            continue
        resolved_path = str(Path(script_path).expanduser().resolve(strict=False))
        script = scripts_by_path.get(resolved_path)
        if not script:
            report["skipped"].append({"cron_id": record.get("cron_id", ""), "path": script_path, "reason": "not a registered personal script"})
            continue
        declared = script.get("declared_schedule", {})
        if declared.get("required") and declared.get("valid"):
            report["skipped"].append({"cron_id": record.get("cron_id", ""), "path": script_path, "reason": "already has valid schedule metadata"})
            continue
        path = Path(script["path"])
        metadata_lines = _inferred_schedule_metadata_lines(path, script.get("metadata", {}), record)
        if not metadata_lines:
            report["skipped"].append({"cron_id": record.get("cron_id", ""), "path": script_path, "reason": "unsupported schedule metadata inference"})
            continue
        entry = {
            "cron_id": str(record.get("cron_id") or ""),
            "path": str(path),
            "schedule_type": str(record.get("schedule_type") or ""),
        }
        if dry_run:
            entry["dry_run"] = True
            report["repaired"].append(entry)
            continue
        try:
            _write_metadata_block(path, metadata_lines)
            report["repaired"].append(entry)
        except Exception as exc:
            report["errors"].append({"path": str(path), "error": str(exc)})
    if report["errors"]:
        report["ok"] = False
    return report


def _extract_launchctl_value(output: str, prefixes: str | tuple[str, ...]) -> str | None:
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    for raw_line in output.splitlines():
        line = raw_line.strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return line[len(prefix):].strip()
    return None


def _launchctl_service_state(label: str) -> dict:
    state = {
        "loaded": None,
        "pid": "",
        "state": "",
        "last_exit_status": "",
        "error": "",
    }
    if platform.system() != "Darwin":
        return state

    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception as exc:
        return {**state, "loaded": False, "error": str(exc)}

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Could not find service" in output:
        return {**state, "loaded": False, "error": output.strip() or "not loaded"}

    return {
        "loaded": True,
        "pid": _extract_launchctl_value(output, ("pid = ", "PID = ")) or "",
        "state": _extract_launchctl_value(output, "state = ") or "",
        "last_exit_status": _extract_launchctl_value(
            output,
            ("last exit code = ", "last exit status = ", "LastExitStatus = "),
        ) or "",
        "error": "",
    }


def _keep_alive_runtime_snapshot(record: dict) -> dict:
    if record.get("schedule_type") != "keep_alive":
        return {
            "runtime_state": "unknown",
            "runtime_summary": "",
            "runtime_problems": [],
        }

    label = record.get("launchd_label") or f"com.nexo.{record.get('cron_id', '')}"
    service = _launchctl_service_state(str(label))
    problems: list[str] = []

    if service.get("loaded") is False:
        problems.append("keep_alive service not loaded in launchd")
        return {
            "runtime_state": "stale",
            "runtime_summary": "keep_alive service not loaded",
            "runtime_problems": problems,
        }

    pid = str(service.get("pid", "") or "").strip()
    service_state = str(service.get("state", "") or "").strip().lower()
    last_exit = str(service.get("last_exit_status", "") or "").strip()
    if pid:
        return {
            "runtime_state": "alive",
            "runtime_summary": f"running with pid {pid}",
            "runtime_problems": [],
        }
    if service_state in {"running", "spawned"}:
        return {
            "runtime_state": "alive",
            "runtime_summary": f"launchd state {service_state}",
            "runtime_problems": [],
        }
    if last_exit and last_exit != "0":
        problems.append(f"keep_alive daemon exited with status {last_exit}")
        return {
            "runtime_state": "degraded",
            "runtime_summary": f"last exit {last_exit}",
            "runtime_problems": problems,
        }

    problems.append("keep_alive service is loaded but has no active pid")
    return {
        "runtime_state": "degraded",
        "runtime_summary": "loaded but not running",
        "runtime_problems": problems,
    }


def _discover_personal_schedule_records() -> list[dict]:
    """Inspect macOS LaunchAgents and return raw personal schedule records."""
    if platform.system() != "Darwin":
        return []

    results = []
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    if not launch_agents_dir.is_dir():
        return results

    core_names = load_core_script_names()
    for plist_path in sorted(launch_agents_dir.glob("com.nexo.*.plist")):
        try:
            with plist_path.open("rb") as fh:
                plist_data = plistlib.load(fh)
        except Exception:
            continue

        env = plist_data.get("EnvironmentVariables") or {}
        if env.get("NEXO_MANAGED_CORE_CRON") == "1":
            continue

        program_args = plist_data.get("ProgramArguments") or []
        candidate = _extract_script_path_candidate(program_args)
        label = str(plist_data.get("Label", plist_path.stem))
        cron_id = label.replace("com.nexo.", "", 1)
        script_path = candidate.expanduser() if candidate is not None else None
        in_scripts_dir = bool(script_path and _within_scripts_dir(script_path))
        exists = bool(script_path and script_path.is_file())
        ignored = bool(script_path and in_scripts_dir and _is_ignored(script_path))
        is_core = bool(script_path and exists and script_path.name in core_names)
        if is_core or ignored:
            continue

        schedule_type, schedule_value, schedule_label = _format_schedule_from_plist(plist_data)
        managed_marker = env.get(PERSONAL_SCHEDULE_MANAGED_ENV) == "1"
        if managed_marker and exists and script_path is not None:
            with contextlib.suppress(Exception):
                meta = parse_inline_metadata(script_path)
                declared = get_declared_schedule(meta, meta.get("name", script_path.stem))
                if declared.get("valid") and declared.get("cron_id") == cron_id:
                    schedule_type = str(declared.get("schedule_type") or schedule_type)
                    schedule_value = str(declared.get("schedule_value") or schedule_value)
                    schedule_label = str(declared.get("schedule_label") or schedule_label)
        results.append({
            "cron_id": cron_id,
            "script_path": str(script_path) if script_path else "",
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "schedule_label": schedule_label,
            "run_at_load": bool(plist_data.get("RunAtLoad")),
            "launchd_label": label,
            "plist_path": str(plist_path),
            "enabled": True,
            "description": "",
            "managed_marker": managed_marker,
            "script_exists": exists,
            "script_within_scripts_dir": in_scripts_dir,
        })

    return results


def audit_personal_schedules() -> dict:
    """Return semantic schedule audit for personal LaunchAgents.

    Only schedules created/repaired through the official flow count as managed.
    Manual plists are discovered for visibility and repair, but never blessed.
    """
    classification = classify_scripts_dir()
    personal_scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    scripts_by_path = {
        str(Path(entry["path"]).expanduser().resolve(strict=False)): entry
        for entry in personal_scripts
    }

    audited: list[dict] = []
    summary = {
        "declared_managed": 0,
        "discovered_manual": 0,
        "orphan_schedule": 0,
        "healthy": 0,
        "problems": 0,
        "managed_registered": 0,
        "keep_alive": 0,
        "runtime_alive": 0,
        "runtime_degraded": 0,
        "runtime_duplicated": 0,
        "runtime_stale": 0,
        "runtime_unknown": 0,
    }

    for record in _discover_personal_schedule_records():
        script_path = record.get("script_path", "")
        resolved_path = str(Path(script_path).expanduser().resolve(strict=False)) if script_path else ""
        script = scripts_by_path.get(resolved_path)
        declared = script.get("declared_schedule", {}) if script else {}
        declared_valid = bool(script and declared.get("required") and declared.get("valid"))
        matches = declared_valid and _schedule_matches(record, declared)

        if record.get("managed_marker") and declared_valid:
            schedule_origin = "declared_managed"
        elif declared_valid:
            schedule_origin = "discovered_manual"
        else:
            schedule_origin = "orphan_schedule"

        problems: list[str] = []
        if not record.get("script_within_scripts_dir"):
            problems.append("schedule points outside NEXO_HOME/personal/scripts")
        elif not record.get("script_path"):
            problems.append("schedule does not resolve a script path")
        elif not record.get("script_exists"):
            problems.append(f"scheduled script missing: {record['script_path']}")
        elif not script:
            problems.append("schedule points to a script that is not a registered personal script")

        if script and not declared.get("required"):
            problems.append("personal schedule exists without declared inline metadata")
        elif script and declared.get("required") and not declared.get("valid"):
            problems.append(declared.get("error", "invalid declared schedule metadata"))
        elif declared_valid and not matches:
            problems.append(
                f"schedule drift: actual {record.get('schedule_label') or record.get('schedule_value') or record.get('schedule_type')} "
                f"!= declared {declared.get('schedule_label') or declared.get('cron_id')}"
            )

        if declared_valid and not record.get("managed_marker"):
            problems.append("schedule was discovered manually and must be recreated via nexo scripts reconcile")

        schedule_managed = bool(schedule_origin == "declared_managed" and matches and not problems)
        if schedule_managed:
            schedule_state = "healthy"
        elif schedule_origin == "declared_managed":
            schedule_state = "drifted"
        elif schedule_origin == "discovered_manual" and matches:
            schedule_state = "manual_matching_declared"
        elif schedule_origin == "discovered_manual":
            schedule_state = "manual_drift"
        else:
            schedule_state = "orphaned"

        audited_record = dict(record)
        runtime_snapshot = _keep_alive_runtime_snapshot(record)
        audited_record.update({
            "schedule_origin": schedule_origin,
            "schedule_declared": declared_valid,
            "schedule_managed": schedule_managed,
            "schedule_matches_declared": matches,
            "schedule_state": schedule_state,
            "problems": problems,
            "script_name": script.get("name", "") if script else "",
            "declared_schedule": declared if script else {},
            **runtime_snapshot,
        })
        audited.append(audited_record)
        summary[schedule_origin] += 1
        if schedule_managed:
            summary["healthy"] += 1
            summary["managed_registered"] += 1
        else:
            summary["problems"] += 1

    duplicate_cron_ids: dict[str, int] = {}
    duplicate_script_paths: dict[str, int] = {}
    for record in audited:
        if record.get("schedule_type") != "keep_alive":
            continue
        cron_id = str(record.get("cron_id", "") or "")
        script_path = str(record.get("script_path", "") or "")
        if cron_id:
            duplicate_cron_ids[cron_id] = duplicate_cron_ids.get(cron_id, 0) + 1
        if script_path:
            duplicate_script_paths[script_path] = duplicate_script_paths.get(script_path, 0) + 1

    for record in audited:
        if record.get("schedule_type") == "keep_alive":
            cron_id = str(record.get("cron_id", "") or "")
            script_path = str(record.get("script_path", "") or "")
            duplicated = (
                (cron_id and duplicate_cron_ids.get(cron_id, 0) > 1)
                or (script_path and duplicate_script_paths.get(script_path, 0) > 1)
            )
            if duplicated:
                runtime_problems = list(record.get("runtime_problems", []))
                runtime_problems.append("duplicate keep_alive schedules discovered for the same cron/script")
                record["runtime_state"] = "duplicated"
                record["runtime_summary"] = "multiple keep_alive schedules discovered"
                record["runtime_problems"] = runtime_problems

        if record.get("schedule_type") == "keep_alive":
            summary["keep_alive"] += 1
            runtime_state = str(record.get("runtime_state", "unknown") or "unknown")
            key = f"runtime_{runtime_state}"
            if key not in summary:
                summary[key] = 0
            summary[key] += 1

    return {
        "schedules": audited,
        "summary": summary,
    }


def discover_personal_schedules() -> list[dict]:
    """Return only healthy managed personal schedules."""
    managed: list[dict] = []
    for record in audit_personal_schedules()["schedules"]:
        if record.get("schedule_managed"):
            managed.append({
                "cron_id": record["cron_id"],
                "script_path": record["script_path"],
                "schedule_type": record["schedule_type"],
                "schedule_value": record["schedule_value"],
                "schedule_label": record["schedule_label"],
                "launchd_label": record["launchd_label"],
                "plist_path": record["plist_path"],
                "enabled": record.get("enabled", True),
                "description": record.get("description", ""),
            })
    return managed


def sync_personal_scripts(prune_missing: bool = True) -> dict:
    """Sync filesystem + scheduler state into the DB-backed personal scripts registry."""
    from db import init_db, sync_personal_scripts_registry

    init_db()
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    schedule_audit = audit_personal_schedules()
    schedules = [record for record in schedule_audit["schedules"] if record.get("schedule_managed")]
    result = sync_personal_scripts_registry(scripts, schedules, prune_missing=prune_missing)
    result["classification"] = classification["summary"]
    missing_declared = []
    managed_by_path: dict[str, list[dict]] = {}
    for schedule in schedules:
        managed_by_path.setdefault(schedule["script_path"], []).append(schedule)
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in schedule_audit["schedules"]:
        schedules_by_path.setdefault(schedule["script_path"], []).append(schedule)
    for script in scripts:
        declared = script.get("declared_schedule", {})
        if not declared.get("required"):
            continue
        healthy = managed_by_path.get(script["path"], [])
        if healthy:
            continue
        attached = schedules_by_path.get(script["path"], [])
        if not attached:
            missing_declared.append({
                "name": script["name"],
                "path": script["path"],
                "declared_schedule": declared,
                "reason": "no schedule discovered",
            })
            continue
        attached_states = [item.get("schedule_state", item.get("schedule_origin", "unknown")) for item in attached]
        missing_declared.append({
            "name": script["name"],
            "path": script["path"],
            "declared_schedule": declared,
            "reason": f"schedule discovered but not managed ({', '.join(attached_states)})",
        })
    result["schedule_audit"] = schedule_audit
    result["missing_declared_schedules"] = missing_declared
    result["marker_warnings"] = _schedule_marker_warnings(schedule_audit)
    return result


def _schedule_marker_warnings(schedule_audit: dict) -> list[dict]:
    """Report LaunchAgent marker drift without silently blessing it.

    Managed personal schedules must be both declared in inline metadata and
    carry the managed marker written by the official schedule flow.
    """
    warnings: list[dict] = []
    for record in (schedule_audit or {}).get("schedules", []) or []:
        marker = bool(record.get("managed_marker"))
        declared = bool(record.get("schedule_declared"))
        matches = bool(record.get("schedule_matches_declared"))
        if marker and not declared:
            reason = "managed marker present but no valid declared schedule"
        elif marker and declared and not matches:
            reason = "managed marker present but schedule drifts from declaration"
        elif not marker and declared:
            reason = "declared schedule discovered without managed marker"
        else:
            continue
        warnings.append({
            "cron_id": str(record.get("cron_id") or ""),
            "script_path": str(record.get("script_path") or ""),
            "plist_path": str(record.get("plist_path") or ""),
            "reason": reason,
        })
    return warnings


def _schedule_matches(existing: dict, declared: dict) -> bool:
    if not existing or not declared.get("valid"):
        return False
    if existing.get("cron_id") != declared.get("cron_id"):
        return False
    if existing.get("schedule_type") != declared.get("schedule_type"):
        return False
    existing_value = _canonical_schedule_value(existing.get("schedule_type", ""), existing.get("schedule_value", ""))
    declared_value = _canonical_schedule_value(declared.get("schedule_type", ""), declared.get("schedule_value", ""))
    if existing_value != declared_value:
        return False
    if bool(existing.get("run_at_load")) != bool(declared.get("run_on_boot")):
        return False
    return True


def _remove_schedule_file(*, cron_id: str, plist_path: str) -> dict:
    removed = {
        "cron_id": cron_id,
        "plist_path": plist_path,
        "deleted": False,
    }
    plist = Path(plist_path) if plist_path else None
    if plist and platform.system() == "Darwin" and plist.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
            capture_output=True,
        )
        with contextlib.suppress(FileNotFoundError):
            plist.unlink()
            removed["deleted"] = True
    return removed


def ensure_personal_schedules(*, dry_run: bool = False) -> dict:
    """Create or repair personal schedules declared in inline script metadata."""
    classification = classify_scripts_dir()
    scripts = [entry for entry in classification["entries"] if entry["classification"] == "personal"]
    schedule_audit = audit_personal_schedules()
    schedules_by_path: dict[str, list[dict]] = {}
    for schedule in schedule_audit["schedules"]:
        schedules_by_path.setdefault(schedule["script_path"], []).append(schedule)

    report = {
        "ok": True,
        "dry_run": dry_run,
        "created": [],
        "repaired": [],
        "already_present": [],
        "skipped": [],
        "invalid": [],
    }

    for script in scripts:
        declared = script.get("declared_schedule", {})
        if not declared.get("required"):
            report["skipped"].append({
                "name": script["name"],
                "reason": "no declared schedule",
            })
            continue
        if not declared.get("valid"):
            report["invalid"].append({
                "name": script["name"],
                "path": script["path"],
                "error": declared.get("error", "invalid schedule metadata"),
            })
            continue

        existing = schedules_by_path.get(script["path"], [])
        matching = next((item for item in existing if item.get("schedule_managed") and _schedule_matches(item, declared)), None)
        if matching:
            entry = {
                "name": script["name"],
                "cron_id": matching["cron_id"],
                "schedule_label": matching.get("schedule_label", ""),
            }
            plist_path = matching.get("plist_path", "")
            if plist_path and platform.system() == "Darwin" and Path(plist_path).exists():
                label = matching.get("launchd_label") or f"com.nexo.{matching['cron_id']}"
                svc = _launchctl_service_state(label)
                if not svc.get("loaded"):
                    if not dry_run:
                        result = subprocess.run(
                            ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist_path],
                            capture_output=True, timeout=5,
                        )
                        if result.returncode == 0:
                            entry["reloaded"] = True
                            entry["reason"] = "plist on disk but not loaded in launchd"
                        else:
                            entry["reload_failed"] = True
                            entry["reason"] = result.stderr.decode(errors="replace").strip() or "bootstrap failed"
                    else:
                        entry["reloaded"] = True
                        entry["reason"] = "plist on disk but not loaded in launchd (dry_run)"
            report["already_present"].append(entry)
            continue

        repair_reasons = [item.get("schedule_state", item.get("schedule_origin", "unknown")) for item in existing]
        if dry_run:
            report["repaired" if existing else "created"].append({
                "name": script["name"],
                "cron_id": declared["cron_id"],
                "schedule_label": declared["schedule_label"],
                "dry_run": True,
                "reason": ", ".join(repair_reasons) if repair_reasons else "missing schedule",
            })
            continue

        removed = []
        if existing:
            for item in existing:
                removed.append(_remove_schedule_file(cron_id=item["cron_id"], plist_path=item.get("plist_path", "")))
            from db import delete_personal_script_schedule

            for item in existing:
                delete_personal_script_schedule(item["cron_id"])

        from plugins.schedule import handle_schedule_add

        response = handle_schedule_add(
            cron_id=declared["cron_id"],
            script=script["path"],
            schedule=declared.get("schedule", ""),
            interval_seconds=declared.get("interval_seconds", 0),
            description=script.get("description", ""),
            script_type=script.get("runtime", "auto"),
            keep_alive=declared.get("schedule_type") == "keep_alive",
        )
        target = report["repaired" if existing else "created"]
        target.append({
            "name": script["name"],
            "cron_id": declared["cron_id"],
            "schedule_label": declared["schedule_label"],
            "reason": ", ".join(repair_reasons) if repair_reasons else "missing schedule",
            "removed": removed,
            "result": response,
        })

    sync_result = sync_personal_scripts()
    report["sync"] = sync_result
    report["classification"] = classification["summary"]
    return report


def reconcile_personal_scripts(*, dry_run: bool = False) -> dict:
    """Full lifecycle reconciliation: classify, sync registry, ensure declared schedules."""
    renamed_result = rename_legacy_personal_script_filenames(dry_run=dry_run)
    orphan_metadata_result = repair_orphan_personal_schedule_metadata(dry_run=dry_run)
    sync_result = sync_personal_scripts()
    ensure_result = ensure_personal_schedules(dry_run=dry_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "renamed_legacy_filenames": renamed_result,
        "repaired_orphan_schedule_metadata": orphan_metadata_result,
        "sync": sync_result,
        "marker_warnings": sync_result.get("marker_warnings", []),
        "ensure_schedules": ensure_result,
        "classification": ensure_result.get("classification", sync_result.get("classification", {})),
    }


def retire_superseded_personal_scripts(*, dry_run: bool = False) -> dict:
    """Archive personal scripts that now collide with reserved core identities.

    This keeps fresh/update installs free of legacy residues after scripts are
    promoted from personal to core. Files are never deleted outright: they are
    moved into runtime/backups so operator data can still be recovered manually.
    """
    scripts_dir = get_scripts_dir()
    report = {
        "ok": True,
        "dry_run": dry_run,
        "candidates": [],
        "archived": [],
        "unscheduled": [],
        "errors": [],
    }
    if not scripts_dir.is_dir():
        return report

    core_identities = load_core_script_identities()
    candidates: list[tuple[Path, dict]] = []
    for path in sorted(scripts_dir.iterdir()):
        if not path.is_file():
            continue
        meta = parse_inline_metadata(path)
        if _is_ignored(path) or not _is_script_candidate(path, meta):
            continue
        if _script_collides_with_core_identity(path, meta, core_identities=core_identities):
            candidates.append((path, meta))

    if not candidates:
        return report

    schedule_records = _discover_personal_schedule_records()
    backup_root: Path | None = None
    for path, meta in candidates:
        name = meta.get("name", path.stem)
        report["candidates"].append({"name": _resolved_script_name(path, meta, classification="personal"), "path": str(path)})
        resolved_path = str(path.expanduser().resolve(strict=False))
        matching_schedules = [
            record for record in schedule_records
            if str(Path(record.get("script_path", "")).expanduser().resolve(strict=False)) == resolved_path
        ]
        if dry_run:
            continue

        for record in matching_schedules:
            removed = _remove_schedule_file(
                cron_id=str(record.get("cron_id", "")),
                plist_path=str(record.get("plist_path", "")),
            )
            report["unscheduled"].append(removed)

        if backup_root is None:
            backup_root = paths.create_backup_dir("retired-personal-scripts")
        target = backup_root / path.name
        suffix = 2
        while target.exists():
            target = backup_root / f"{path.stem}-{suffix}{path.suffix}"
            suffix += 1
        try:
            shutil.move(str(path), str(target))
            report["archived"].append({
                "name": name,
                "path": str(path),
                "backup_path": str(target),
            })
        except Exception as exc:
            report["errors"].append({"path": str(path), "error": str(exc)})
    if backup_root is not None:
        paths.finalize_backup_snapshot(backup_root)
    return report


def _template_path(filename: str) -> Path | None:
    candidates = [
        NEXO_HOME / "templates" / filename,
        NEXO_CODE.parent / "templates" / filename,
        NEXO_CODE / "templates" / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _script_filename_from_name(name: str, runtime: str) -> str:
    stem = _safe_slug(name) or "personal-script"
    ext = {
        "python": ".py",
        "shell": ".sh",
        "node": ".js",
        "php": ".php",
    }.get(runtime, ".py")
    return stem + ext


def _personal_script_filename_from_name(name: str, runtime: str) -> str:
    logical_name = _logical_personal_script_name(name)
    return _script_filename_from_name(f"{PERSONAL_SCRIPT_FILENAME_PREFIX}{logical_name}", runtime)


def _legacy_personal_script_target_path(path: Path, metadata: dict | None = None) -> Path | None:
    metadata = metadata or {}
    if not path.name.startswith("nexo-"):
        return None
    runtime = classify_runtime(path, metadata)
    target_name = _personal_script_filename_from_name(
        metadata.get("name", "") or path.stem,
        runtime,
    )
    target = path.with_name(target_name)
    if target.name == path.name:
        return None
    return target


def _legacy_personal_script_reference_hits(path: Path) -> list[str]:
    """Return stable operator-owned artifacts that still reference the legacy filename."""
    hits: list[str] = []
    needle = path.name
    search_paths = [paths.brain_dir() / "project-atlas.json"]

    for candidate in search_paths:
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(errors="replace")
        except Exception:
            continue
        if needle in content:
            hits.append(str(candidate))

    scripts_dir = get_scripts_dir()
    if scripts_dir.is_dir():
        for sibling in sorted(scripts_dir.iterdir()):
            if sibling == path or not sibling.is_file():
                continue
            try:
                content = sibling.read_text(errors="replace")
            except Exception:
                continue
            if needle in content:
                hits.append(str(sibling))
    return hits


def _normalize_legacy_personal_script_metadata_name(content: str) -> str:
    lines = content.splitlines(keepends=True)
    updated: list[str] = []
    changed = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("# nexo: name="):
            prefix = line[: len(line) - len(stripped)]
            raw_value = stripped.split("=", 1)[1].strip()
            normalized_name = _logical_personal_script_name(raw_value)
            newline = "\n" if line.endswith("\n") else ""
            updated.append(f"{prefix}# nexo: name={normalized_name}{newline}")
            changed = True
            continue
        updated.append(line)
    return "".join(updated) if changed else content


def rename_legacy_personal_script_filenames(*, dry_run: bool = False) -> dict:
    """Move legacy personal filenames from ``nexo-*`` to canonical ``ps-*`` names."""
    from db import delete_personal_script_schedule

    scripts_dir = get_scripts_dir()
    report = {
        "ok": True,
        "dry_run": dry_run,
        "candidates": [],
        "renamed": [],
        "unscheduled": [],
        "skipped": [],
        "errors": [],
    }
    if not scripts_dir.is_dir():
        return report

    schedule_records = _discover_personal_schedule_records()
    core_identities = load_core_script_identities()

    for path in sorted(scripts_dir.iterdir()):
        if not path.is_file():
            continue
        metadata = parse_inline_metadata(path)
        if _is_ignored(path) or not _is_script_candidate(path, metadata):
            continue
        if _script_collides_with_core_identity(path, metadata, core_identities=core_identities):
            continue

        target = _legacy_personal_script_target_path(path, metadata)
        if target is None:
            continue

        entry = {
            "name": _logical_personal_script_name(metadata.get("name", "") or path.stem),
            "old_path": str(path),
            "new_path": str(target),
        }
        report["candidates"].append(entry)

        if target.exists():
            report["skipped"].append({
                **entry,
                "reason": f"target already exists: {target.name}",
            })
            continue

        reference_hits = _legacy_personal_script_reference_hits(path)
        if reference_hits:
            report["skipped"].append({
                **entry,
                "reason": "legacy filename still referenced by operator-owned artifacts",
                "references": reference_hits,
            })
            continue

        if dry_run:
            continue

        resolved_old = str(path.expanduser().resolve(strict=False))
        matching_schedules = [
            record for record in schedule_records
            if str(Path(record.get("script_path", "")).expanduser().resolve(strict=False)) == resolved_old
        ]
        for record in matching_schedules:
            removed = _remove_schedule_file(
                cron_id=str(record.get("cron_id", "")),
                plist_path=str(record.get("plist_path", "")),
            )
            delete_personal_script_schedule(str(record.get("cron_id", "")))
            report["unscheduled"].append(removed)

        try:
            shutil.move(str(path), str(target))
            try:
                original = target.read_text(errors="replace")
                normalized = _normalize_legacy_personal_script_metadata_name(original)
                if normalized != original:
                    target.write_text(normalized)
            except Exception as exc:
                report["errors"].append({
                    **entry,
                    "error": f"renamed but failed to normalize inline metadata: {exc}",
                })
            report["renamed"].append(entry)
        except Exception as exc:
            report["errors"].append({
                **entry,
                "error": str(exc),
            })

    if report["errors"]:
        report["ok"] = False
    return report


def create_script(name: str, *, description: str = "", runtime: str = "python", force: bool = False) -> dict:
    runtime = runtime if runtime in SUPPORTED_RUNTIMES else "python"
    if runtime == "unknown":
        runtime = "python"

    scripts_dir = get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    logical_name = _logical_personal_script_name(name)
    if _logical_name_collides_with_core_identity(logical_name):
        raise ValueError(f"Personal script name collides with a reserved core script identity: {logical_name}")
    filename = _personal_script_filename_from_name(name, runtime)
    path = scripts_dir / filename
    if path.exists() and not force:
        raise FileExistsError(f"Script already exists: {path}")

    if runtime == "shell":
        template_path = _template_path("script-template.sh")
    else:
        template_path = _template_path("script-template.py")

    if template_path:
        content = template_path.read_text()
    elif runtime == "shell":
        content = (
            "#!/usr/bin/env bash\n"
            "# nexo: name=example-script\n"
            "# nexo: description=Example shell script using NEXO\n"
            "# nexo: runtime=shell\n"
            "set -euo pipefail\n"
            "echo \"Hello from NEXO personal script\"\n"
        )
    else:
        content = (
            "#!/usr/bin/env python3\n"
            "# nexo: name=example-script\n"
            "# nexo: description=Example personal script using NEXO\n"
            "# nexo: runtime=python\n"
            "print('hello')\n"
        )

    content = content.replace("example-script", logical_name)
    content = content.replace("Example personal script using the stable NEXO CLI", description or f"Personal script: {logical_name}")
    content = content.replace("Example shell script using NEXO", description or f"Personal script: {logical_name}")

    path.write_text(content)
    if runtime in {"shell", "python"}:
        path.chmod(0o755)
    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "name": logical_name,
        "requested_name": name,
        "path": str(path),
        "filename": filename,
        "runtime": runtime,
        "description": description,
        "sync": sync_result,
    }


def unschedule_personal_script(name_or_path: str) -> dict:
    """Remove all personal schedules attached to a script and prune registry entries."""
    from db import (
        init_db,
        get_personal_script,
        delete_personal_script_schedule,
    )

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path)
    if not script:
        resolved = resolve_script(name_or_path)
        if not resolved or resolved.get("core"):
            return {"ok": False, "error": f"Personal script not found: {name_or_path}"}
        script = resolved

    removed: list[dict] = []
    audited = audit_personal_schedules()
    discovered = [
        item for item in audited["schedules"]
        if item.get("script_path") == script.get("path")
    ]
    for schedule in discovered:
        removed.append(_remove_schedule_file(cron_id=schedule["cron_id"], plist_path=schedule.get("plist_path", "")))

    for schedule in script.get("schedules", []):
        delete_personal_script_schedule(schedule["cron_id"])
        if not any(item["cron_id"] == schedule["cron_id"] for item in removed):
            removed.append({
                "cron_id": schedule["cron_id"],
                "plist_path": schedule.get("plist_path", ""),
                "deleted": False,
            })

    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "script": script["name"],
        "removed_schedules": removed,
        "sync": sync_result,
    }


def remove_personal_script(name_or_path: str, *, keep_file: bool = False) -> dict:
    """Remove a personal script from the runtime and registry."""
    from db import init_db, get_personal_script, delete_personal_script

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path)
    if not script:
        resolved = resolve_script(name_or_path)
        if not resolved or resolved.get("core"):
            return {"ok": False, "error": f"Personal script not found: {name_or_path}"}
        script = resolved

    if script.get("core"):
        return {"ok": False, "error": "Refusing to remove a core script via personal scripts lifecycle."}

    unschedule_result = unschedule_personal_script(script["path"])
    deleted_file = False
    path = Path(script["path"])
    if not keep_file and path.is_file() and _within_scripts_dir(path):
        path.unlink()
        deleted_file = True
    delete_personal_script(script["path"])
    sync_result = sync_personal_scripts()
    return {
        "ok": True,
        "script": script["name"],
        "path": script["path"],
        "deleted_file": deleted_file,
        "keep_file": keep_file,
        "unschedule": unschedule_result,
        "sync": sync_result,
    }


def set_personal_script_enabled(name_or_path: str, enabled: bool) -> dict:
    """Plan F0.2.2 — flip the `enabled` flag on a personal script.

    Returns ``{ok: bool, name, enabled, changed: bool}``. Refuses to
    flip packaged core scripts (they ship enabled and the operator
    should `nexo scripts unschedule` if they want one to stop).

    The cron wrapper (`nexo-cron-wrapper.sh`, F0.2.4) reads this flag
    on every tick and exits 0 with `summary='[disabled]'` when the
    script is disabled, so the LaunchAgent can stay loaded but the
    script itself is dormant.
    """
    from automation_controls import get_script_runtime_contract
    from db import init_db
    from db._core import get_db
    from db._personal_scripts import get_personal_script, upsert_personal_script

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path, include_core=True) or resolve_script(name_or_path)
    if not script:
        return {"ok": False, "error": f"Script not found: {name_or_path}"}
    contract = get_script_runtime_contract(script.get("name", ""))
    toggleable_core = bool(contract.get("toggleable_core"))
    script_origin = "core" if (
        bool(script.get("core"))
        or str(script.get("origin") or "") == "core"
        or toggleable_core
    ) else "user"
    if script.get("core") and not toggleable_core and not _within_scripts_dir(Path(script.get("path", ""))):
        return {
            "ok": False,
            "error": "Refusing to toggle a packaged core script via this entry point — "
                     "use `nexo scripts unschedule` to stop it instead.",
        }
    if enabled and not bool(contract.get("available", True)):
        return {
            "ok": False,
            "error": str(contract.get("blocked_reason") or "Script prerequisites are not satisfied."),
            "blocked_reason_code": str(contract.get("blocked_reason_code") or ""),
        }
    if script_origin == "core" and toggleable_core:
        existing = get_personal_script(script.get("path", ""), include_core=True)
        if not existing:
            upsert_personal_script(
                name=script.get("name", name_or_path),
                path=script.get("path", ""),
                description=script.get("description", ""),
                runtime=script.get("runtime", "unknown"),
                metadata=script.get("metadata", {}),
                created_by="nexo-core",
                source="core-toggle",
                origin="core",
                enabled=True,
                has_inline_metadata=bool(script.get("metadata")),
            )
    target = 1 if enabled else 0
    conn = get_db()
    cur = conn.execute(
        "UPDATE personal_scripts SET enabled = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE path = ? OR name = ?",
        (target, script["path"], script.get("name", name_or_path)),
    )
    conn.commit()
    changed = bool(cur.rowcount)
    return {
        "ok": True,
        "name": script.get("name", name_or_path),
        "path": script.get("path", ""),
        "enabled": bool(target),
        "changed": changed,
        "origin": script_origin,
        "runtime_contract": contract,
    }


def set_automation_enabled(name_or_path: str, enabled: bool) -> dict:
    """Stable contract wrapper for operator-facing automation toggles."""
    return set_personal_script_enabled(name_or_path, enabled)


def get_personal_script_status(name_or_path: str) -> dict:
    """Plan F0.2.2 — read-only view of one personal script for the
    Desktop panel and the `nexo scripts status` CLI verb."""
    from automation_controls import get_script_runtime_contract
    from db import init_db
    from db._core import get_db
    from db._personal_scripts import get_personal_script

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path, include_core=True) or resolve_script(name_or_path)
    if not script:
        return {"ok": False, "error": f"Script not found: {name_or_path}"}
    conn = get_db()
    last = conn.execute(
        "SELECT exit_code, started_at, ended_at, summary FROM cron_runs "
        "WHERE cron_id = ? ORDER BY id DESC LIMIT 1",
        (script.get("name") or "",),
    ).fetchone()
    last_run = dict(last) if last else None
    contract = get_script_runtime_contract(script.get("name", ""))
    return {
        "ok": True,
        "name": script.get("name"),
        "path": script.get("path"),
        "enabled": bool(script.get("enabled", True)),
        "core": bool(script.get("core")),
        "classification": script.get("classification", "user"),
        "last_run": last_run,
        "runtime_contract": contract,
        "blocked_reason": str(contract.get("blocked_reason") or ""),
        "supports_extra_instructions": bool(contract.get("supports_extra_instructions")),
        "operator_extra_instructions": str((script.get("metadata") or {}).get("operator_extra_instructions") or ""),
        "schedule_configurable": bool(contract.get("schedule_configurable")),
        "schedule_type": str(contract.get("schedule_type") or ""),
        "schedule_source": str(contract.get("schedule_source") or ""),
        "effective_schedule_label": str(contract.get("effective_schedule_label") or ""),
        "interval_seconds": int(contract.get("interval_seconds", 0) or 0),
        "default_interval_seconds": int(contract.get("default_interval_seconds", 0) or 0),
        "minimum_interval_seconds": int(contract.get("minimum_interval_seconds", 0) or 0),
        "maximum_interval_seconds": int(contract.get("maximum_interval_seconds", 0) or 0),
        "interval_step_seconds": int(contract.get("interval_step_seconds", 0) or 0),
    }


def get_automation_status(name_or_path: str) -> dict:
    """Stable contract wrapper for operator-facing automation status."""
    return get_personal_script_status(name_or_path)


def _script_execution_command(script: dict) -> list[str]:
    path = str(script.get("path") or "").strip()
    runtime = str(script.get("runtime") or "").strip().lower()
    if runtime == "python" or path.endswith(".py"):
        return [sys.executable, path]
    if runtime == "shell" or path.endswith((".sh", ".bash", ".zsh")):
        return ["/bin/bash", path]
    return [path]


def reactivate_automation(name_or_path: str, *, test_run: bool = False, timeout_seconds: int = 180) -> dict:
    """Enable an operator automation and optionally run its built-in check."""
    enable_result = set_automation_enabled(name_or_path, True)
    if not enable_result.get("ok"):
        return enable_result

    status_result = get_automation_status(name_or_path)
    result = {
        "ok": bool(status_result.get("ok", True)),
        "name": enable_result.get("name") or name_or_path,
        "enabled": True,
        "changed": bool(enable_result.get("changed")),
        "status": status_result,
    }
    if not test_run:
        return result

    resolved = resolve_script_reference(str(result["name"])) or resolve_script_reference(name_or_path)
    if not resolved:
        result.update({"ok": False, "error": "Automation enabled, but the test run could not be started."})
        return result
    if str(resolved.get("name") or "").strip() != "morning-agent":
        result.update({"ok": False, "error": "Test run is available for morning-agent only."})
        return result

    command = _script_execution_command(resolved) + ["--dry-run"]
    env = os.environ.copy()
    env["NEXO_HEADLESS"] = "1"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_seconds)),
            env=env,
        )
    except Exception as exc:
        result.update({
            "ok": False,
            "error": "Test run could not start.",
            "test_run": {"ok": False, "error": str(exc)},
        })
        return result

    test_payload = {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
    }
    result["test_run"] = test_payload
    if completed.returncode != 0:
        result["ok"] = False
        result["error"] = "Test run did not complete."
    return result


def set_script_extra_instructions(name_or_path: str, instructions: str) -> dict:
    """Persist operator-side prompt additions without touching the core prompt."""
    from automation_controls import supports_operator_extra_instructions
    from db import init_db
    from db._personal_scripts import get_personal_script, upsert_personal_script

    init_db()
    sync_personal_scripts()
    script = get_personal_script(name_or_path, include_core=True) or resolve_script(name_or_path)
    if not script:
        return {"ok": False, "error": f"Script not found: {name_or_path}"}
    if not supports_operator_extra_instructions(script.get("name", "")):
        return {
            "ok": False,
            "error": "This automation does not support operator extra instructions.",
        }

    existing = get_personal_script(script.get("path", ""), include_core=True)
    metadata = dict((existing or script).get("metadata") or {})
    text = str(instructions or "").strip()
    script_origin = "core" if (
        bool(script.get("core"))
        or str(script.get("origin") or "") == "core"
    ) else "user"
    if text:
        metadata["operator_extra_instructions"] = text
    else:
        metadata.pop("operator_extra_instructions", None)

    upsert_personal_script(
        name=script.get("name", name_or_path),
        path=script.get("path", ""),
        description=script.get("description", ""),
        runtime=script.get("runtime", "unknown"),
        metadata=metadata,
        created_by="nexo-core" if script_origin == "core" else "manual",
        source="core-toggle" if script_origin == "core" else "filesystem",
        origin=script_origin,
        enabled=bool((existing or script).get("enabled", True)),
        has_inline_metadata=bool(script.get("metadata")),
    )

    return {
        "ok": True,
        "name": script.get("name", name_or_path),
        "path": script.get("path", ""),
        "supports_extra_instructions": True,
        "operator_extra_instructions": text,
        "cleared": not bool(text),
    }


def set_automation_instructions(name_or_path: str, instructions: str) -> dict:
    """Stable contract wrapper for automation operator notes."""
    return set_script_extra_instructions(name_or_path, instructions)


def get_automation_preference_contract(name_or_path: str) -> dict:
    """Return schema + current structured preferences for a product automation."""
    from automation_preferences import get_automation_preferences

    return get_automation_preferences(name_or_path)


def set_automation_preference_contract(name_or_path: str, payload: dict) -> dict:
    """Persist structured preferences without touching extra instructions."""
    from automation_preferences import set_automation_preferences

    return set_automation_preferences(name_or_path, payload)


def set_script_schedule_override(
    name_or_path: str,
    *,
    interval_seconds: int | None = None,
    daily_at: str | None = None,
    weekdays=None,
    clear: bool = False,
) -> dict:
    from automation_controls import set_core_automation_schedule
    from db import init_db

    init_db()
    sync_personal_scripts()
    script = resolve_script(name_or_path)
    if not script:
        return {"ok": False, "error": f"Script not found: {name_or_path}"}
    return set_core_automation_schedule(
        script.get("name", name_or_path),
        interval_seconds=interval_seconds,
        daily_at=daily_at,
        weekdays=weekdays,
        clear=clear,
    )


def set_automation_schedule(
    name_or_path: str,
    *,
    interval_seconds: int | None = None,
    daily_at: str | None = None,
    weekdays=None,
    clear: bool = False,
) -> dict:
    """Stable contract wrapper for automation cadence overrides."""
    return set_script_schedule_override(
        name_or_path,
        interval_seconds=interval_seconds,
        daily_at=daily_at,
        weekdays=weekdays,
        clear=clear,
    )


def doctor_script(path_or_name: str) -> dict:
    """Validate a single script. Returns dict with pass/warn/fail items."""
    # Resolve
    p = Path(path_or_name)
    if not p.is_file():
        info = resolve_script(path_or_name)
        if not info:
            return {"status": "fail", "items": [{"level": "fail", "msg": f"Script not found: {path_or_name}"}]}
        p = Path(info["path"])

    items: list[dict] = []
    meta = parse_inline_metadata(p)
    runtime = classify_runtime(p, meta)
    core_names = load_core_script_names()
    core_identities = load_core_script_identities()
    is_core = p.name in core_names

    # File exists
    if p.is_file():
        items.append({"level": "pass", "msg": f"File exists: {p.name}"})
    else:
        items.append({"level": "fail", "msg": f"File missing: {p.name}"})
        return {"status": "fail", "items": items}

    # Name collision with core
    name = _resolved_script_name(p, meta, classification="personal")
    if not is_core and _script_collides_with_core_identity(p, meta, core_identities=core_identities):
        colliding = sorted(_script_identity_tokens(p, meta) & core_identities)
        surface = colliding[0] if colliding else name
        items.append({"level": "fail", "msg": f"Name collision with core script identity: {surface}"})

    # Runtime recognized
    if runtime == "unknown":
        items.append({"level": "warn", "msg": "Runtime not recognized (no shebang, no extension match)"})
    else:
        items.append({"level": "pass", "msg": f"Runtime: {runtime}"})

    # Shebang for shell scripts
    if runtime == "shell":
        shebang = _detect_shebang(p)
        if not shebang:
            items.append({"level": "warn", "msg": "Shell script without shebang"})
        else:
            items.append({"level": "pass", "msg": f"Shebang: {shebang}"})

    # Executable bit for shell scripts
    if runtime == "shell":
        mode = p.stat().st_mode
        if not (mode & stat.S_IXUSR):
            items.append({"level": "warn", "msg": "Shell script missing executable bit"})
        else:
            items.append({"level": "pass", "msg": "Executable bit set"})

    # Timeout parse
    timeout_str = meta.get("timeout", "")
    if timeout_str:
        try:
            int(timeout_str)
            items.append({"level": "pass", "msg": f"Timeout: {timeout_str}s"})
        except ValueError:
            items.append({"level": "fail", "msg": f"Invalid timeout value: {timeout_str}"})

    declared = get_declared_schedule(meta, name)
    if declared.get("required"):
        if declared.get("valid"):
            items.append({"level": "pass", "msg": f"Declared schedule: {declared['schedule_label']}"})
        else:
            items.append({"level": "fail", "msg": declared.get("error", "Invalid declared schedule metadata")})

    if runtime == "node" and not shutil.which("node"):
        items.append({"level": "fail", "msg": "Node runtime not found in PATH"})
    if runtime == "php" and not shutil.which("php"):
        items.append({"level": "fail", "msg": "PHP runtime not found in PATH"})

    # Requires check
    requires = meta.get("requires", "")
    if requires:
        for cmd in requires.split(","):
            cmd = cmd.strip()
            if cmd and not shutil.which(cmd):
                items.append({"level": "fail", "msg": f"Required command not in PATH: {cmd}"})
            elif cmd:
                items.append({"level": "pass", "msg": f"Required command found: {cmd}"})

    allow_db_access = str(meta.get("doctor_allow_db", "")).strip().lower() in {"1", "true", "yes", "on"}
    if allow_db_access:
        items.append({"level": "pass", "msg": "Doctor DB access explicitly allowed"})

    # Forbidden patterns (only for personal scripts)
    if not is_core:
        try:
            content = p.read_text(errors="ignore")
            if not allow_db_access:
                for pat in _FORBIDDEN_PATTERNS:
                    match = pat.search(content)
                    if match:
                        items.append({"level": "fail", "msg": f"Forbidden DB pattern found: {match.group()}"})
        except Exception:
            pass

    # Determine overall status
    levels = [i["level"] for i in items]
    if "fail" in levels:
        status = "fail"
    elif "warn" in levels:
        status = "warn"
    else:
        status = "pass"

    return {"status": status, "items": items, "name": name, "path": str(p)}


def doctor_all_scripts() -> list[dict]:
    """Run doctor on all personal scripts."""
    results = []
    for script in list_scripts(include_core=False):
        result = doctor_script(script["path"])
        results.append(result)
    return results
