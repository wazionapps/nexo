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
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LINUX_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
MACOS_CAFFEINATE_PATH = Path("/usr/bin/caffeinate")
MACOS_CLOSED_LID_BEHAVIOR = "best_effort"
LINUX_CLOSED_LID_BEHAVIOR = "host_policy"


def _schedule_defaults() -> dict:
    return {
        "timezone": "UTC",
        "auto_update": True,
        POWER_POLICY_KEY: POWER_POLICY_UNSET,
        POWER_POLICY_VERSION_KEY: POWER_POLICY_VERSION,
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
