from __future__ import annotations
"""Runtime power policy helpers.

Manages the optional "prevent sleep" helper as an explicit, persisted runtime
preference. The policy is stored in config/schedule.json to avoid introducing a
second user-facing config surface.

Important semantic note:
- ``always_on`` means "enable the platform power helper" for best-effort
  background availability.
- It does not replace wake recovery or catchup.
- On laptops, especially with the lid closed, behavior remains platform and
  setup dependent.
"""

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))
CONFIG_DIR = NEXO_HOME / "config"
SCHEDULE_FILE = CONFIG_DIR / "schedule.json"
POWER_POLICY_KEY = "power_policy"
POWER_POLICY_VERSION_KEY = "power_policy_version"
POWER_POLICY_VERSION = 2
POWER_POLICY_ALWAYS_ON = "always_on"
POWER_POLICY_DISABLED = "disabled"
POWER_POLICY_UNSET = "unset"
VALID_POWER_POLICIES = {
    POWER_POLICY_ALWAYS_ON,
    POWER_POLICY_DISABLED,
    POWER_POLICY_UNSET,
}
FULL_DISK_ACCESS_STATUS_KEY = "full_disk_access_status"
FULL_DISK_ACCESS_STATUS_VERSION_KEY = "full_disk_access_status_version"
FULL_DISK_ACCESS_REASONS_KEY = "full_disk_access_reasons"
FULL_DISK_ACCESS_STATUS_VERSION = 1
FULL_DISK_ACCESS_UNSET = "unset"
FULL_DISK_ACCESS_GRANTED = "granted"
FULL_DISK_ACCESS_DECLINED = "declined"
FULL_DISK_ACCESS_LATER = "later"
VALID_FULL_DISK_ACCESS_STATUSES = {
    FULL_DISK_ACCESS_UNSET,
    FULL_DISK_ACCESS_GRANTED,
    FULL_DISK_ACCESS_DECLINED,
    FULL_DISK_ACCESS_LATER,
}
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LINUX_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
MACOS_CAFFEINATE_PATH = Path("/usr/bin/caffeinate")
MACOS_CLOSED_LID_BEHAVIOR = "best_effort"
LINUX_CLOSED_LID_BEHAVIOR = "host_policy"
MACOS_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
MACOS_FDA_PROBE_PATHS = (
    Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db",
    Path.home() / "Library" / "Mail",
    Path.home() / "Library" / "Messages",
    Path.home() / "Library" / "Safari",
    Path.home() / "Library" / "Application Support" / "AddressBook",
)


def _schedule_defaults() -> dict:
    return {
        "timezone": "UTC",
        "auto_update": True,
        POWER_POLICY_KEY: POWER_POLICY_UNSET,
        POWER_POLICY_VERSION_KEY: POWER_POLICY_VERSION,
        FULL_DISK_ACCESS_STATUS_KEY: FULL_DISK_ACCESS_UNSET,
        FULL_DISK_ACCESS_STATUS_VERSION_KEY: FULL_DISK_ACCESS_STATUS_VERSION,
        FULL_DISK_ACCESS_REASONS_KEY: [],
        "processes": {},
    }


def load_schedule_config() -> dict:
    if not SCHEDULE_FILE.is_file():
        return _schedule_defaults()
    try:
        data = json.loads(SCHEDULE_FILE.read_text())
    except Exception:
        return _schedule_defaults()
    if not isinstance(data, dict):
        return _schedule_defaults()
    merged = _schedule_defaults()
    merged.update(data)
    merged.setdefault("processes", {})
    return merged


def save_schedule_config(schedule: dict) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(_schedule_defaults())
    payload.update(schedule or {})
    payload.setdefault("processes", {})
    payload[POWER_POLICY_KEY] = normalize_power_policy(payload.get(POWER_POLICY_KEY))
    payload[POWER_POLICY_VERSION_KEY] = POWER_POLICY_VERSION
    payload[FULL_DISK_ACCESS_STATUS_KEY] = normalize_full_disk_access_status(
        payload.get(FULL_DISK_ACCESS_STATUS_KEY)
    )
    payload[FULL_DISK_ACCESS_STATUS_VERSION_KEY] = FULL_DISK_ACCESS_STATUS_VERSION
    payload[FULL_DISK_ACCESS_REASONS_KEY] = normalize_full_disk_access_reasons(
        payload.get(FULL_DISK_ACCESS_REASONS_KEY)
    )
    SCHEDULE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return SCHEDULE_FILE


def normalize_power_policy(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"enabled", "yes", "on", "true", "1"}:
        return POWER_POLICY_ALWAYS_ON
    if candidate in {"disabled", "no", "off", "false", "0"}:
        return POWER_POLICY_DISABLED
    if candidate in VALID_POWER_POLICIES:
        return candidate
    return POWER_POLICY_UNSET


def _detect_linux_power_helper() -> tuple[str | None, str | None]:
    if shutil.which("systemd-inhibit"):
        return "systemd-inhibit", shutil.which("systemd-inhibit")
    if shutil.which("caffeine"):
        return "caffeine", shutil.which("caffeine")
    return None, None


def describe_power_policy(policy: str | None = None, *, system: str | None = None) -> dict:
    policy = normalize_power_policy(policy or get_power_policy())
    system = system or platform.system()
    base = {
        "policy": policy,
        "platform": system,
        "helper": None,
        "helper_path": None,
        "helper_available": False,
        "closed_lid_behavior": "n/a",
        "requires_wake_recovery": True,
        "summary": "",
        "prompt_note": "",
    }

    if policy != POWER_POLICY_ALWAYS_ON:
        state = "disabled" if policy == POWER_POLICY_DISABLED else "unset"
        base["summary"] = f"Power helper {state}."
        base["prompt_note"] = "Wake recovery and catchup remain available."
        return base

    if system == "Darwin":
        available = MACOS_CAFFEINATE_PATH.is_file()
        base.update({
            "helper": "caffeinate",
            "helper_path": str(MACOS_CAFFEINATE_PATH),
            "helper_available": available,
            "closed_lid_behavior": MACOS_CLOSED_LID_BEHAVIOR,
            "summary": (
                "Enable the native macOS caffeinate helper for best-effort "
                "background availability."
            ),
            "prompt_note": (
                "macOS uses the native caffeinate helper. Closed-lid operation "
                "depends on your hardware/setup, so wake recovery remains active."
            ),
        })
        return base

    if system == "Linux":
        helper, helper_path = _detect_linux_power_helper()
        base.update({
            "helper": helper,
            "helper_path": helper_path,
            "helper_available": bool(helper_path),
            "closed_lid_behavior": LINUX_CLOSED_LID_BEHAVIOR,
            "summary": (
                "Enable the Linux power helper for best-effort background "
                "availability."
            ),
            "prompt_note": (
                "Linux uses systemd-inhibit or caffeine when available. "
                "Closed-lid behavior depends on host power settings, so wake "
                "recovery remains active."
            ),
        })
        return base

    base.update({
        "summary": f"No power helper integration is available on {system}.",
        "prompt_note": "Wake recovery and catchup remain available.",
    })
    return base


def _protected_macos_roots(home: Path | None = None) -> tuple[Path, ...]:
    home = home or Path.home()
    return (
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Library" / "Mobile Documents",
    )


def _is_protected_macos_path(candidate: str | os.PathLike[str] | Path | None) -> bool:
    if not candidate:
        return False
    if platform.system() != "Darwin":
        return False
    resolved = Path(candidate).expanduser().resolve(strict=False)
    return any(resolved == root or root in resolved.parents for root in _protected_macos_roots())


def normalize_full_disk_access_status(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"enabled", "yes", "approved", "ok", "true", "1"}:
        return FULL_DISK_ACCESS_GRANTED
    if candidate in {"no", "disabled", "off", "false", "0"}:
        return FULL_DISK_ACCESS_DECLINED
    if candidate in VALID_FULL_DISK_ACCESS_STATUSES:
        return candidate
    return FULL_DISK_ACCESS_UNSET


def normalize_full_disk_access_reasons(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    reasons: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in reasons:
            reasons.append(text)
    return reasons


def get_full_disk_access_status(schedule: dict | None = None) -> str:
    schedule = schedule or load_schedule_config()
    return normalize_full_disk_access_status(schedule.get(FULL_DISK_ACCESS_STATUS_KEY))


def format_full_disk_access_label(status: str | None = None, *, system: str | None = None) -> str:
    status = normalize_full_disk_access_status(status or get_full_disk_access_status())
    system = system or platform.system()
    if system != "Darwin":
        return "not_applicable"
    if status == FULL_DISK_ACCESS_GRANTED:
        return "granted"
    if status == FULL_DISK_ACCESS_DECLINED:
        return "declined"
    if status == FULL_DISK_ACCESS_LATER:
        return "later"
    return "unset"


def _tail_has_permission_denial(log_file: Path) -> bool:
    if not log_file.is_file():
        return False
    try:
        with log_file.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(size - 4096, 0))
            tail = fh.read().decode("utf-8", errors="ignore")
        return "Operation not permitted" in tail
    except Exception:
        return False


def detect_full_disk_access_reasons(*, system: str | None = None) -> list[str]:
    system = system or platform.system()
    if system != "Darwin":
        return []

    reasons: list[str] = []
    if _is_protected_macos_path(NEXO_HOME):
        reasons.append(
            f"NEXO_HOME is inside a protected macOS folder: {NEXO_HOME}"
        )

    logs_dir = NEXO_HOME / "logs"
    if logs_dir.is_dir():
        for log_file in sorted(logs_dir.glob("*-stderr.log")):
            if _tail_has_permission_denial(log_file):
                reasons.append(
                    f"Recent background job stderr hit 'Operation not permitted' ({log_file.name})"
                )
                break
    return reasons


def _runtime_python_candidates() -> list[str]:
    candidates: list[str] = []
    runtime_python = NEXO_HOME / ".venv" / "bin" / "python3"
    if runtime_python.is_file():
        candidates.append(str(runtime_python))
    if sys.executable:
        candidates.append(sys.executable)
    python3_path = shutil.which("python3")
    if python3_path:
        candidates.append(python3_path)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def full_disk_access_targets() -> list[str]:
    targets = ["/bin/bash", *(_runtime_python_candidates())]
    seen: set[str] = set()
    ordered: list[str] = []
    for item in targets:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def open_full_disk_access_settings() -> dict:
    if platform.system() != "Darwin":
        return {"ok": False, "opened": False, "message": "Full Disk Access setup is macOS-only."}
    try:
        result = subprocess.run(
            ["open", MACOS_FDA_SETTINGS_URL],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ok = result.returncode == 0
        return {
            "ok": ok,
            "opened": ok,
            "message": "" if ok else (result.stderr.strip() or result.stdout.strip()),
        }
    except Exception as e:
        return {"ok": False, "opened": False, "message": str(e)}


def _probe_candidates() -> list[Path]:
    candidates: list[Path] = []
    for path_candidate in MACOS_FDA_PROBE_PATHS:
        expanded = path_candidate.expanduser()
        if expanded.exists():
            candidates.append(expanded)
    if _is_protected_macos_path(NEXO_HOME):
        candidates.append(NEXO_HOME)
    return candidates


def probe_full_disk_access() -> dict:
    if platform.system() != "Darwin":
        return {"checked": False, "granted": None, "probe_path": None, "message": "macOS-only"}

    candidates = _probe_candidates()
    if not candidates:
        return {
            "checked": False,
            "granted": None,
            "probe_path": None,
            "message": "No local probe path available for verification.",
        }

    script = 'TARGET="$1"; if [ -d "$TARGET" ]; then ls "$TARGET" >/dev/null 2>&1; else head -c 1 "$TARGET" >/dev/null 2>&1; fi'
    last_error = ""
    for candidate in candidates:
        try:
            result = subprocess.run(
                ["/bin/bash", "-lc", script, "_", str(candidate)],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as e:
            last_error = str(e)
            continue
        if result.returncode == 0:
            return {
                "checked": True,
                "granted": True,
                "probe_path": str(candidate),
                "message": "",
            }
        last_error = result.stderr.strip() or result.stdout.strip()
    return {
        "checked": True,
        "granted": False,
        "probe_path": str(candidates[0]),
        "message": last_error,
    }


def prompt_for_full_disk_access(
    *,
    reason: str = "install",
    reasons: list[str] | None = None,
    input_fn=input,
    output_fn=print,
    open_fn=open_full_disk_access_settings,
    probe_fn=probe_full_disk_access,
) -> dict:
    reasons = normalize_full_disk_access_reasons(reasons)
    output_fn(
        "[NEXO] Some macOS background automations may need Full Disk Access. "
        "macOS does not allow granting it automatically."
    )
    if reasons:
        output_fn("[NEXO] Reason(s) detected:")
        for item in reasons:
            output_fn(f"[NEXO] - {item}")
    output_fn("[NEXO] If you continue, NEXO will open the correct System Settings screen.")
    output_fn("[NEXO] Add your terminal app and, if needed for background jobs, these binaries:")
    for target in full_disk_access_targets():
        output_fn(f"[NEXO] - {target}")

    prompt = "[NEXO] Open Full Disk Access setup now? [y]es / [n]o / [l]ater: "
    while True:
        answer = str(input_fn(prompt)).strip().lower()
        if answer in {"y", "yes"}:
            open_result = open_fn()
            if open_result.get("opened"):
                output_fn("[NEXO] System Settings opened at Privacy & Security → Full Disk Access.")
            elif open_result.get("message"):
                output_fn(f"[NEXO] Could not open System Settings automatically: {open_result['message']}")
            output_fn("[NEXO] Grant the permission, then press Enter to verify.")
            follow_up = str(
                input_fn("[NEXO] Press Enter after granting it, or type later to skip for now: ")
            ).strip().lower()
            if follow_up in {"later", "l"}:
                return {
                    "status": FULL_DISK_ACCESS_LATER,
                    "settings_opened": bool(open_result.get("opened")),
                    "verified": False,
                    "message": "Full Disk Access setup deferred for later.",
                }
            probe = probe_fn()
            if probe.get("granted") is True:
                return {
                    "status": FULL_DISK_ACCESS_GRANTED,
                    "settings_opened": bool(open_result.get("opened")),
                    "verified": True,
                    "message": f"Full Disk Access verified via {probe.get('probe_path')}.",
                }
            return {
                "status": FULL_DISK_ACCESS_LATER,
                "settings_opened": bool(open_result.get("opened")),
                "verified": False,
                "message": (
                    "Could not verify Full Disk Access yet. NEXO will remind you later if "
                    "background jobs still hit TCC."
                ),
            }
        if answer in {"n", "no"}:
            return {
                "status": FULL_DISK_ACCESS_DECLINED,
                "settings_opened": False,
                "verified": False,
                "message": "Full Disk Access was declined.",
            }
        if answer in {"l", "later", ""}:
            return {
                "status": FULL_DISK_ACCESS_LATER,
                "settings_opened": False,
                "verified": False,
                "message": "Full Disk Access setup deferred for later.",
            }
        output_fn("[NEXO] Reply with yes, no, or later.")


def format_power_policy_label(policy: str | None = None, *, system: str | None = None) -> str:
    details = describe_power_policy(policy=policy, system=system)
    policy = details["policy"]
    if policy == POWER_POLICY_ALWAYS_ON and details["platform"] == "Darwin":
        return "always_on (macOS caffeinate, closed-lid best effort)"
    if policy == POWER_POLICY_ALWAYS_ON and details["platform"] == "Linux":
        helper = details["helper"] or "power helper"
        return f"always_on ({helper}, closed-lid depends on host policy)"
    return policy


def get_power_policy(schedule: dict | None = None) -> str:
    schedule = schedule or load_schedule_config()
    return normalize_power_policy(schedule.get(POWER_POLICY_KEY))


def is_power_policy_configured(schedule: dict | None = None) -> bool:
    return get_power_policy(schedule) != POWER_POLICY_UNSET


def set_power_policy(policy: str) -> dict:
    schedule = load_schedule_config()
    schedule[POWER_POLICY_KEY] = normalize_power_policy(policy)
    schedule[POWER_POLICY_VERSION_KEY] = POWER_POLICY_VERSION
    save_schedule_config(schedule)
    return schedule


def set_full_disk_access_status(status: str, *, reasons: list[str] | None = None) -> dict:
    schedule = load_schedule_config()
    schedule[FULL_DISK_ACCESS_STATUS_KEY] = normalize_full_disk_access_status(status)
    schedule[FULL_DISK_ACCESS_STATUS_VERSION_KEY] = FULL_DISK_ACCESS_STATUS_VERSION
    if reasons is not None:
        schedule[FULL_DISK_ACCESS_REASONS_KEY] = normalize_full_disk_access_reasons(reasons)
    save_schedule_config(schedule)
    return schedule


def prompt_for_power_policy(
    *,
    reason: str = "install",
    system: str | None = None,
    input_fn=input,
    output_fn=print,
) -> str:
    details = describe_power_policy(POWER_POLICY_ALWAYS_ON, system=system)
    prompt = (
        "[NEXO] Enable the background power helper for this machine? "
        "[y]es / [n]o / [l]ater: "
    )
    output_fn(
        "[NEXO] This controls the optional prevent-sleep helper. "
        "It improves background availability but remains opt-in."
    )
    output_fn(f"[NEXO] {details['prompt_note']}")
    while True:
        answer = str(input_fn(prompt)).strip().lower()
        if answer in {"y", "yes"}:
            return POWER_POLICY_ALWAYS_ON
        if answer in {"n", "no"}:
            return POWER_POLICY_DISABLED
        if answer in {"l", "later", ""}:
            return POWER_POLICY_UNSET
        output_fn("[NEXO] Reply with yes, no, or later.")


def ensure_power_policy_choice(
    *,
    interactive: bool,
    reason: str = "update",
    input_fn=input,
    output_fn=print,
) -> dict:
    schedule = load_schedule_config()
    policy = get_power_policy(schedule)
    prompted = False
    if interactive and policy == POWER_POLICY_UNSET:
        prompted = True
        policy = prompt_for_power_policy(
            reason=reason,
            system=platform.system(),
            input_fn=input_fn,
            output_fn=output_fn,
        )
        schedule[POWER_POLICY_KEY] = policy
        schedule[POWER_POLICY_VERSION_KEY] = POWER_POLICY_VERSION
        save_schedule_config(schedule)
    return {
        "policy": policy,
        "prompted": prompted,
        "schedule_file": str(SCHEDULE_FILE),
    }


def ensure_full_disk_access_choice(
    *,
    interactive: bool,
    reason: str = "update",
    input_fn=input,
    output_fn=print,
    open_fn=open_full_disk_access_settings,
    probe_fn=probe_full_disk_access,
) -> dict:
    schedule = load_schedule_config()
    system = platform.system()
    status = get_full_disk_access_status(schedule)
    reasons = detect_full_disk_access_reasons(system=system)
    prompted = False
    verified = False
    settings_opened = False
    message = ""

    if system != "Darwin":
        return {
            "status": status,
            "prompted": False,
            "verified": False,
            "settings_opened": False,
            "reasons": [],
            "schedule_file": str(SCHEDULE_FILE),
            "message": "",
            "relevant": False,
        }

    schedule[FULL_DISK_ACCESS_REASONS_KEY] = reasons
    schedule[FULL_DISK_ACCESS_STATUS_VERSION_KEY] = FULL_DISK_ACCESS_STATUS_VERSION

    if not reasons:
        save_schedule_config(schedule)
        return {
            "status": status,
            "prompted": False,
            "verified": False,
            "settings_opened": False,
            "reasons": [],
            "schedule_file": str(SCHEDULE_FILE),
            "message": "",
            "relevant": False,
        }

    if status == FULL_DISK_ACCESS_GRANTED:
        probe = probe_fn()
        if probe.get("granted") is True:
            verified = True
            message = f"Full Disk Access verified via {probe.get('probe_path')}."
        else:
            status = FULL_DISK_ACCESS_LATER
            message = (
                "Full Disk Access was configured previously but could not be verified. "
                "NEXO will remind you again on the next interactive update."
            )

    elif interactive and status in {FULL_DISK_ACCESS_UNSET, FULL_DISK_ACCESS_LATER}:
        prompted = True
        prompt_result = prompt_for_full_disk_access(
            reason=reason,
            reasons=reasons,
            input_fn=input_fn,
            output_fn=output_fn,
            open_fn=open_fn,
            probe_fn=probe_fn,
        )
        status = normalize_full_disk_access_status(prompt_result.get("status"))
        verified = bool(prompt_result.get("verified"))
        settings_opened = bool(prompt_result.get("settings_opened"))
        message = str(prompt_result.get("message") or "")

    elif status == FULL_DISK_ACCESS_DECLINED:
        message = (
            "Full Disk Access remains declined. Background jobs that touch protected "
            "macOS folders may fail."
        )

    schedule[FULL_DISK_ACCESS_STATUS_KEY] = status
    save_schedule_config(schedule)
    return {
        "status": status,
        "prompted": prompted,
        "verified": verified,
        "settings_opened": settings_opened,
        "reasons": reasons,
        "schedule_file": str(SCHEDULE_FILE),
        "message": message,
        "relevant": True,
    }


def _prevent_sleep_script_path() -> Path:
    runtime_script = NEXO_HOME / "scripts" / "nexo-prevent-sleep.sh"
    if runtime_script.is_file():
        return runtime_script
    source_script = NEXO_CODE / "scripts" / "nexo-prevent-sleep.sh"
    return source_script


def _macos_prevent_sleep_plist() -> tuple[Path, dict]:
    script_path = _prevent_sleep_script_path()
    plist_path = LAUNCH_AGENTS_DIR / "com.nexo.prevent-sleep.plist"
    plist = {
        "Label": "com.nexo.prevent-sleep",
        "ProgramArguments": ["/bin/bash", str(script_path)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(NEXO_HOME / "logs" / "prevent-sleep-stdout.log"),
        "StandardErrorPath": str(NEXO_HOME / "logs" / "prevent-sleep-stderr.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "NEXO_HOME": str(NEXO_HOME),
            "NEXO_CODE": str(NEXO_HOME),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(Path.home() / ".local/bin"),
        },
    }
    return plist_path, plist


def _linux_prevent_sleep_service() -> tuple[Path, str]:
    script_path = _prevent_sleep_script_path()
    service_path = LINUX_SYSTEMD_USER_DIR / "nexo-prevent-sleep.service"
    body = f"""[Unit]
Description=NEXO prevent sleep

[Service]
Type=simple
ExecStart=/bin/bash {script_path}
Environment=HOME={Path.home()}
Environment=NEXO_HOME={NEXO_HOME}
Environment=NEXO_CODE={NEXO_HOME}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
    return service_path, body


def apply_power_policy(policy: str | None = None) -> dict:
    policy = normalize_power_policy(policy or get_power_policy())
    system = platform.system()
    logs_dir = NEXO_HOME / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    details = describe_power_policy(policy=policy, system=system)

    if system == "Darwin":
        return _apply_macos_power_policy(policy, details=details)
    if system == "Linux":
        return _apply_linux_power_policy(policy, details=details)
    return {
        "ok": policy != POWER_POLICY_ALWAYS_ON,
        "policy": policy,
        "platform": system,
        "action": "unsupported",
        "message": f"Unsupported platform for prevent-sleep policy: {system}",
        "details": details,
    }


def _apply_macos_power_policy(policy: str, *, details: dict | None = None) -> dict:
    plist_path, plist = _macos_prevent_sleep_plist()
    label = plist["Label"]
    uid = str(os.getuid())
    if policy == POWER_POLICY_ALWAYS_ON:
        details = details or describe_power_policy(policy, system="Darwin")
        if not details.get("helper_available"):
            return {
                "ok": False,
                "policy": policy,
                "platform": "Darwin",
                "action": "missing-helper",
                "message": f"Required helper not found: {details.get('helper_path') or 'caffeinate'}",
                "details": details,
            }
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        with plist_path.open("wb") as fh:
            plistlib.dump(plist, fh)
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], capture_output=True)
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True,
            text=True,
        )
        ok = result.returncode == 0
        return {
            "ok": ok,
            "policy": policy,
            "platform": "Darwin",
            "action": "enabled",
            "plist_path": str(plist_path),
            "message": "" if ok else (result.stderr.strip() or result.stdout.strip()),
            "details": details,
        }

    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], capture_output=True)
    if plist_path.exists():
        plist_path.unlink()
    subprocess.run(["launchctl", "remove", label], capture_output=True)
    return {
        "ok": True,
        "policy": policy,
        "platform": "Darwin",
        "action": "disabled" if policy == POWER_POLICY_DISABLED else "deferred",
        "plist_path": str(plist_path),
        "details": details or describe_power_policy(policy, system="Darwin"),
    }


def _apply_linux_power_policy(policy: str, *, details: dict | None = None) -> dict:
    service_path, service_body = _linux_prevent_sleep_service()
    if policy == POWER_POLICY_ALWAYS_ON:
        details = details or describe_power_policy(policy, system="Linux")
        if not details.get("helper_available"):
            return {
                "ok": False,
                "policy": policy,
                "platform": "Linux",
                "action": "missing-helper",
                "message": "No Linux power helper found. Install systemd-inhibit or caffeine.",
                "details": details,
            }
        LINUX_SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service_body)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "nexo-prevent-sleep.service"],
            capture_output=True,
            text=True,
        )
        ok = result.returncode == 0
        return {
            "ok": ok,
            "policy": policy,
            "platform": "Linux",
            "action": "enabled",
            "service_path": str(service_path),
            "message": "" if ok else (result.stderr.strip() or result.stdout.strip()),
            "details": details,
        }

    subprocess.run(["systemctl", "--user", "disable", "--now", "nexo-prevent-sleep.service"], capture_output=True)
    if service_path.exists():
        service_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return {
        "ok": True,
        "policy": policy,
        "platform": "Linux",
        "action": "disabled" if policy == POWER_POLICY_DISABLED else "deferred",
        "service_path": str(service_path),
        "details": details or describe_power_policy(policy, system="Linux"),
    }
