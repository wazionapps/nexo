from __future__ import annotations
"""Public contribution preferences and GitHub PR workflow helpers.

This module manages the opt-in "public core evolution" mode:
- user consent and persisted config in schedule.json
- GitHub auth/fork detection
- active Draft PR pause/resume lifecycle
"""

import json
import os
import paths
import platform
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from runtime_power import load_schedule_config, save_schedule_config


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

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
CONTRIB_ROOT = NEXO_HOME / "contrib" / "public-core"
CONTRIB_REPO_DIR = CONTRIB_ROOT / "repo"
CONTRIB_WORKTREES_DIR = CONTRIB_ROOT / "worktrees"
CONTRIB_ARTIFACTS_DIR = paths.operations_dir() / "public-contrib"


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
    schedule = schedule or load_schedule_config()
    return normalize_public_contribution_config(schedule.get(CONFIG_KEY))


def save_public_contribution_config(config: dict) -> dict:
    schedule = load_schedule_config()
    schedule[CONFIG_KEY] = normalize_public_contribution_config(config)
    save_schedule_config(schedule)
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
        return f"draft_prs ({cfg['status']})"
    return cfg["mode"]


def prompt_for_public_contribution(
    *,
    reason: str = "update",
    input_fn=input,
    output_fn=print,
) -> dict:
    output_fn("[NEXO] Public contribution mode is optional and opt-in.")
    output_fn(
        "[NEXO] If enabled, this machine may prepare core improvements in an isolated checkout "
        "and open a Draft PR to the public NEXO repository."
    )
    output_fn("[NEXO] It never auto-merges, and it stays paused while that PR remains open.")
    output_fn("[NEXO] It must never publish personal scripts, local runtime data, logs, prompts, or secrets.")

    while True:
        answer = str(
            input_fn("[NEXO] Enable public contribution via Draft PRs on this machine? [y]es / [n]o / [l]ater: ")
        ).strip().lower()
        if answer in {"y", "yes"}:
            auth = github_auth_status()
            if not auth.get("ok"):
                return {
                    "mode": MODE_PENDING_AUTH,
                    "status": STATUS_PENDING_AUTH,
                    "enabled": False,
                    "message": auth.get("message") or "GitHub authentication is missing.",
                    "github_user": "",
                    "fork_repo": "",
                    "prompted": True,
                }
            fork = ensure_fork(auth.get("login", ""))
            if not fork.get("ok"):
                return {
                    "mode": MODE_PENDING_AUTH,
                    "status": STATUS_PENDING_AUTH,
                    "enabled": False,
                    "message": fork.get("message") or "Could not ensure a GitHub fork.",
                    "github_user": auth.get("login", ""),
                    "fork_repo": "",
                    "prompted": True,
                }
            return {
                "mode": MODE_DRAFT_PRS,
                "status": STATUS_ACTIVE,
                "enabled": True,
                "message": "",
                "github_user": auth.get("login", ""),
                "fork_repo": fork.get("fork_repo", ""),
                "prompted": True,
            }
        if answer in {"n", "no"}:
            return {
                "mode": MODE_OFF,
                "status": STATUS_OFF,
                "enabled": False,
                "message": "",
                "github_user": "",
                "fork_repo": "",
                "prompted": True,
            }
        if answer in {"l", "later", ""}:
            return {
                "mode": MODE_UNSET,
                "status": STATUS_UNSET,
                "enabled": False,
                "message": "",
                "github_user": "",
                "fork_repo": "",
                "prompted": True,
            }
        output_fn("[NEXO] Reply with yes, no, or later.")


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
        config["message"] = ""
    config["prompted"] = prompted
    return config


def refresh_public_contribution_state(config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    if config["mode"] != MODE_DRAFT_PRS:
        return config

    if config.get("active_pr_number") and config.get("active_pr_url"):
        try:
            result = _gh(
                "pr",
                "view",
                str(config["active_pr_number"]),
                "--repo",
                config["upstream_repo"],
                "--json",
                "state,isDraft,url,mergedAt,closed",
                timeout=20,
            )
        except Exception as e:
            config["last_result"] = f"pr_status_error:{e}"
            save_public_contribution_config(config)
            return config
        if result.returncode == 0:
            payload = json.loads(result.stdout or "{}")
            if payload.get("state") == "OPEN" and payload.get("isDraft", False):
                config["status"] = STATUS_PAUSED_OPEN_PR
                save_public_contribution_config(config)
                return config
            resolution = "merged" if payload.get("mergedAt") else "closed"
            config["active_pr_url"] = ""
            config["active_pr_number"] = None
            config["active_branch"] = ""
            config["cooldown_until"] = ""
            config["status"] = STATUS_ACTIVE
            config["last_result"] = f"resolved_pr:{resolution}:{payload.get('url') or ''}".rstrip(":")
            save_public_contribution_config(config)
            return config
        return _set_pending_auth(
            config,
            f"GitHub Draft PR status check failed: {(result.stderr or result.stdout).strip() or 'unknown gh error'}",
        )

    cooldown_until = _parse_iso(config.get("cooldown_until"))
    if cooldown_until and cooldown_until > _utcnow():
        # Legacy installs used a post-merge/close cooldown that blocked the next
        # public contribution cycle even after maintainers resolved the Draft PR.
        # Public contribution should pause only while the PR is still open.
        config["cooldown_until"] = ""
        config["status"] = STATUS_ACTIVE
        save_public_contribution_config(config)
        return config

    auth = github_auth_status()
    if not auth.get("ok"):
        return _set_pending_auth(
            config,
            auth.get("message") or "GitHub authentication is missing for public contribution.",
        )
    login = str(auth.get("login") or "").strip()
    configured_login = str(config.get("github_user") or "").strip()
    if configured_login and login and configured_login.lower() != login.lower():
        return _set_pending_auth(
            config,
            f"GitHub login drift detected: configured {configured_login}, current {login}. Reconfirm public contribution credentials.",
        )
    if login and not configured_login:
        config["github_user"] = login

    if not str(config.get("fork_repo") or "").strip():
        fork = ensure_fork(login)
        if not fork.get("ok"):
            return _set_pending_auth(
                config,
                fork.get("message") or "GitHub fork setup is missing for public contribution.",
            )
        config["fork_repo"] = str(fork.get("fork_repo") or "").strip()

    if config["mode"] == MODE_PENDING_AUTH:
        config["status"] = STATUS_PENDING_AUTH
    else:
        config["status"] = STATUS_ACTIVE
    save_public_contribution_config(config)
    return config


def can_run_public_contribution(config: dict | None = None) -> tuple[bool, str, dict]:
    config = refresh_public_contribution_state(config)
    if config["mode"] == MODE_PENDING_AUTH or config["status"] == STATUS_PENDING_AUTH:
        detail = str(config.get("message") or config.get("last_result") or "").strip()
        return False, detail or "github authentication or fork setup is pending", config
    if config["mode"] != MODE_DRAFT_PRS or not config.get("enabled"):
        return False, "public contribution is disabled", config
    if config["status"] == STATUS_PAUSED_OPEN_PR:
        return False, "an active Draft PR is already open for this machine", config
    return True, "", config


def mark_public_contribution_result(*, result: str, config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    config["last_run_at"] = _utcnow().isoformat()
    config["last_result"] = str(result or "")
    save_public_contribution_config(config)
    return config


def mark_active_pr(*, pr_url: str, pr_number: int | None, branch: str, config: dict | None = None) -> dict:
    config = normalize_public_contribution_config(config or load_public_contribution_config())
    config["active_pr_url"] = pr_url
    config["active_pr_number"] = pr_number
    config["active_branch"] = branch
    config["status"] = STATUS_PAUSED_OPEN_PR
    config["last_run_at"] = _utcnow().isoformat()
    config["last_result"] = "draft_pr_created"
    save_public_contribution_config(config)
    return config


def disable_public_contribution() -> dict:
    config = load_public_contribution_config()
    config.update({
        "enabled": False,
        "mode": MODE_OFF,
        "status": STATUS_OFF,
        "active_pr_url": "",
        "active_pr_number": None,
        "active_branch": "",
    })
    save_public_contribution_config(config)
    return config
