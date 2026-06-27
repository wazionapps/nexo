from __future__ import annotations
"""Retired public-contribution preferences.

Legacy configs are preserved only so updates can retire them safely and route
Evolution improvements through anonymized support tickets.
"""

import json
import importlib
import os
import paths
import platform
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_REPO = "wazionapps/nexo"
CONFIG_KEY = "public_contribution"
CONSENT_VERSION = 1
MODE_UNSET = "unset"
MODE_OFF = "off"
MODE_DRAFT_PRS = "draft_prs"
MODE_PENDING_AUTH = "pending_auth"
STATUS_UNSET = "unset"
STATUS_ACTIVE = "active"
STATUS_PENDING_AUTH = "pending_auth"
STATUS_PAUSED_OPEN_PR = "paused_open_pr"
STATUS_COOLDOWN = "cooldown"
STATUS_OFF = "off"
VALID_MODES = {MODE_UNSET, MODE_OFF, MODE_DRAFT_PRS, MODE_PENDING_AUTH}
VALID_STATUSES = {
    STATUS_UNSET,
    STATUS_ACTIVE,
    STATUS_PENDING_AUTH,
    STATUS_PAUSED_OPEN_PR,
    STATUS_COOLDOWN,
    STATUS_OFF,
}
PUBLIC_CONTRIBUTION_RETIRED_MESSAGE = (
    "Public Draft PR contribution is retired; Evolution routes anonymized support tickets instead."
)

# Path resolution moved to lazy functions (AUDITOR-V700-PASS2 §11, B10 item
# 3). The previous module-level constants were evaluated at import time, so
# any caller that monkeypatched NEXO_HOME or ``paths.operations_dir()`` after
# import kept seeing the stale values. PEP 562 ``__getattr__`` below still
# exposes ``public_contribution.NEXO_HOME`` / ``CONTRIB_ROOT`` / etc. for
# legacy callers that access them as module attributes — re-evaluated on
# every access. A call like ``from public_contribution import CONTRIB_ROOT``
# still snapshots at import, which is the intended behaviour for scripts
# whose NEXO_HOME is fixed before they start.


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


# Public-contribution staging lives under ``NEXO_HOME / contrib`` (the mirror
# clone of the public repo plus per-proposal worktrees). It is intentionally
# outside ``paths.operations_dir()`` because it holds an actual git clone, not
# operational artifacts. Artifacts (logs, proposal payloads) live under
# ``_contrib_artifacts_dir()`` below. If this ever relocates, migrate the
# existing clone + open worktrees — do NOT delete and reclone blindly.
def _contrib_root() -> Path:
    return _nexo_home() / "contrib" / "public-core"


def _contrib_repo_dir() -> Path:
    return _contrib_root() / "repo"


def _contrib_worktrees_dir() -> Path:
    return _contrib_root() / "worktrees"


def _contrib_artifacts_dir() -> Path:
    return paths.operations_dir() / "public-contrib"


_LAZY_PATHS = {
    "NEXO_HOME": _nexo_home,
    "CONTRIB_ROOT": _contrib_root,
    "CONTRIB_REPO_DIR": _contrib_repo_dir,
    "CONTRIB_WORKTREES_DIR": _contrib_worktrees_dir,
    "CONTRIB_ARTIFACTS_DIR": _contrib_artifacts_dir,
}


def __getattr__(name: str):
    resolver = _LAZY_PATHS.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return resolver()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _machine_id() -> str:
    raw = socket.gethostname().strip().lower() or "nexo-machine"
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-") or "nexo-machine"


def _default_public_contribution() -> dict:
    return {
        "enabled": False,
        "mode": MODE_UNSET,
        "consent_version": CONSENT_VERSION,
        "github_user": "",
        "upstream_repo": UPSTREAM_REPO,
        "fork_repo": "",
        "machine_id": _machine_id(),
        "active_pr_url": "",
        "active_pr_number": None,
        "active_branch": "",
        "status": STATUS_UNSET,
        "cooldown_until": "",
        "last_run_at": "",
        "last_result": "",
    }


def _runtime_power_module():
    """Resolve runtime_power lazily so reload-heavy tests see the live module."""
    return importlib.import_module("runtime_power")


def normalize_public_contribution_config(config: dict | None) -> dict:
    merged = dict(_default_public_contribution())
    if isinstance(config, dict):
        merged.update(config)
    merged["mode"] = str(merged.get("mode") or MODE_UNSET).strip().lower()
    if merged["mode"] not in VALID_MODES:
        merged["mode"] = MODE_UNSET
    merged["status"] = str(merged.get("status") or STATUS_UNSET).strip().lower()
    if merged["status"] not in VALID_STATUSES:
        merged["status"] = STATUS_UNSET
    merged["enabled"] = bool(merged.get("enabled", False))
    merged["consent_version"] = CONSENT_VERSION
    merged["upstream_repo"] = str(merged.get("upstream_repo") or UPSTREAM_REPO)
    merged["github_user"] = str(merged.get("github_user") or "").strip()
    merged["fork_repo"] = str(merged.get("fork_repo") or "").strip()
    merged["machine_id"] = str(merged.get("machine_id") or _machine_id()).strip() or _machine_id()
    merged["active_pr_url"] = str(merged.get("active_pr_url") or "").strip()
    merged["active_pr_number"] = merged.get("active_pr_number")
    if merged["active_pr_number"] in {"", 0, "0"}:
        merged["active_pr_number"] = None
    merged["active_branch"] = str(merged.get("active_branch") or "").strip()
    merged["cooldown_until"] = str(merged.get("cooldown_until") or "").strip()
    merged["last_run_at"] = str(merged.get("last_run_at") or "").strip()
    merged["last_result"] = str(merged.get("last_result") or "").strip()
    return merged


def load_public_contribution_config(schedule: dict | None = None) -> dict:
    schedule = schedule or _runtime_power_module().load_schedule_config()
    return normalize_public_contribution_config(schedule.get(CONFIG_KEY))


def save_public_contribution_config(config: dict) -> dict:
    runtime_power = _runtime_power_module()
    schedule = runtime_power.load_schedule_config()
    schedule[CONFIG_KEY] = normalize_public_contribution_config(config)
    runtime_power.save_schedule_config(schedule)
    return schedule[CONFIG_KEY]


def _gh(*args: str, cwd: Path | None = None, timeout: int = 20) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    token = (
        str(env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()
        or _github_token_from_credentials()
    )
    if token:
        env["GH_TOKEN"] = token
    return subprocess.run(
        ["gh", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _github_token_from_credentials() -> str:
    try:
        from db import get_credential
    except Exception:
        return ""
    for key in ("token", "gh_token", "github_token"):
        try:
            matches = get_credential("github", key)
        except Exception:
            continue
        for item in matches or []:
            value = str(item.get("value") or "").strip()
            if value:
                return value
    return ""


def github_auth_status() -> dict:
    if not shutil.which("gh"):
        return {"ok": False, "message": "GitHub CLI not found.", "login": "", "code": "gh_missing"}
    try:
        result = _gh("api", "user", timeout=20)
    except Exception as e:
        return {"ok": False, "message": str(e), "login": "", "code": "gh_error"}
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        lowered = message.lower()
        code = "auth_missing"
        if "keychain" in lowered:
            code = "keychain_blocked"
        elif "token" in lowered or "authentication" in lowered or "login" in lowered:
            code = "auth_missing"
        return {"ok": False, "message": message, "login": "", "code": code}
    try:
        payload = json.loads(result.stdout or "{}")
        login = str(payload.get("login") or "").strip()
    except Exception:
        login = ""
    return {"ok": bool(login), "message": "", "login": login, "code": "ok" if login else "auth_missing"}


def ensure_fork(login: str) -> dict:
    if not login:
        return {"ok": False, "message": "Missing GitHub login.", "fork_repo": "", "code": "missing_login"}
    fork_repo = f"{login}/nexo"
    if not shutil.which("gh"):
        return {"ok": False, "message": "GitHub CLI not found.", "fork_repo": "", "code": "gh_missing"}
    try:
        check = _gh("repo", "view", fork_repo, "--json", "nameWithOwner", timeout=20)
        if check.returncode == 0:
            return {"ok": True, "message": "", "fork_repo": fork_repo, "code": "ok"}
        create = _gh("repo", "fork", UPSTREAM_REPO, "--clone=false", "--remote=false", timeout=60)
        if create.returncode == 0:
            return {"ok": True, "message": "", "fork_repo": fork_repo, "code": "ok"}
        return {
            "ok": False,
            "message": (create.stderr or create.stdout or check.stderr or check.stdout).strip(),
            "fork_repo": "",
            "code": "fork_unavailable",
        }
    except Exception as e:
        return {"ok": False, "message": str(e), "fork_repo": "", "code": "fork_error"}


def _set_pending_auth(config: dict, message: str) -> dict:
    config["status"] = STATUS_PENDING_AUTH
    config["last_result"] = f"pending_auth:{message}"
    save_public_contribution_config(config)
    config["message"] = message
    return config


def _parse_iso(ts: str | None) -> datetime | None:
    value = str(ts or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_public_contribution_label(config: dict | None = None) -> str:
    cfg = normalize_public_contribution_config(config)
    if cfg["mode"] == MODE_DRAFT_PRS:
        return "off (GitHub retired; support tickets active)"
    return cfg["mode"]


def _retire_public_contribution_config(config: dict) -> dict:
    config["enabled"] = False
    config["mode"] = MODE_OFF
    config["status"] = STATUS_OFF
    config["github_user"] = ""
    config["fork_repo"] = ""
    config["active_pr_url"] = ""
    config["active_pr_number"] = None
    config["active_branch"] = ""
    config["cooldown_until"] = ""
    config["last_result"] = "retired:support_ticket_channel"
    config["message"] = PUBLIC_CONTRIBUTION_RETIRED_MESSAGE
    return config


def prompt_for_public_contribution(
    *,
    reason: str = "update",
    input_fn=input,
    output_fn=print,
) -> dict:
    output_fn(f"[NEXO] {PUBLIC_CONTRIBUTION_RETIRED_MESSAGE}")
    return {
        "mode": MODE_OFF,
        "status": STATUS_OFF,
        "enabled": False,
        "message": PUBLIC_CONTRIBUTION_RETIRED_MESSAGE,
        "github_user": "",
        "fork_repo": "",
        "prompted": True,
    }


def ensure_public_contribution_choice(
    *,
    interactive: bool,
    reason: str = "update",
    input_fn=input,
    output_fn=print,
    force_prompt: bool = False,
) -> dict:
    config = load_public_contribution_config()
    prompted = False
    if interactive and (force_prompt or config["mode"] == MODE_UNSET):
        prompted = True
        result = prompt_for_public_contribution(reason=reason, input_fn=input_fn, output_fn=output_fn)
        config.update({
            "enabled": result["enabled"],
            "mode": result["mode"],
            "status": result["status"],
            "github_user": result["github_user"],
            "fork_repo": result["fork_repo"],
            "machine_id": config.get("machine_id") or _machine_id(),
        })
        if result["mode"] != MODE_DRAFT_PRS:
            config["active_pr_url"] = ""
            config["active_pr_number"] = None
            config["active_branch"] = ""
        save_public_contribution_config(config)
        config = load_public_contribution_config()
        config["message"] = result.get("message", "")
    else:
        if config["mode"] in {MODE_DRAFT_PRS, MODE_PENDING_AUTH} or config.get("enabled"):
            config = _retire_public_contribution_config(config)
            save_public_contribution_config(config)
        config["message"] = config.get("message", "")
    config["prompted"] = prompted
    return config


def refresh_public_contribution_state(config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    if config["mode"] in {MODE_DRAFT_PRS, MODE_PENDING_AUTH} or config.get("enabled"):
        config = _retire_public_contribution_config(config)
        save_public_contribution_config(config)
        return config
    return config


def can_run_public_contribution(config: dict | None = None) -> tuple[bool, str, dict]:
    config = refresh_public_contribution_state(config)
    return False, PUBLIC_CONTRIBUTION_RETIRED_MESSAGE, config


def mark_public_contribution_result(*, result: str, config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    config["last_run_at"] = _utcnow().isoformat()
    config["last_result"] = str(result or "")
    save_public_contribution_config(config)
    return config


def mark_active_pr(*, pr_url: str, pr_number: int | None, branch: str, config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    config = _retire_public_contribution_config(config)
    config["last_run_at"] = _utcnow().isoformat()
    save_public_contribution_config(config)
    return config


def disable_public_contribution() -> dict:
    config = load_public_contribution_config()
    config = _retire_public_contribution_config(config)
    save_public_contribution_config(config)
    return config
