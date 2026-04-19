"""Automation control contracts for toggleable core scripts.

Centralises two product-level behaviours:

1. Which packaged core automations are operator-toggleable.
2. Which runtime prerequisites they need before they can be enabled.

This lets Brain and Desktop expose the same truth without duplicating
ad-hoc checks in JS and Python.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any


TOGGLEABLE_CORE_SCRIPT_NAMES: frozenset[str] = frozenset({
    "email-monitor",
    "followup-runner",
    "morning-agent",
})
CORE_AUTOMATION_OVERRIDES_KEY = "core_automation_overrides"

_CORE_AUTOMATION_SCHEDULES: dict[str, dict[str, Any]] = {
    "email-monitor": {
        "kind": "interval",
        "minimum_interval_seconds": 60,
        "maximum_interval_seconds": 24 * 3600,
        "interval_step_seconds": 60,
    },
    "followup-runner": {
        "kind": "interval",
        "minimum_interval_seconds": 300,
        "maximum_interval_seconds": 24 * 3600,
        "interval_step_seconds": 300,
    },
    "morning-agent": {
        "kind": "calendar",
    },
}

EXTRA_INSTRUCTIONS_METADATA_KEY = "operator_extra_instructions"


_EMAIL_REQUIRED_SCRIPTS: dict[str, dict[str, Any]] = {
    "email-monitor": {
        "kind": "agent_email_account",
        "required_roles": ("both",),
        "summary": (
            "Requires an enabled agent email account with read + send access "
            "(role `both`) so it can read and reply to incoming mail."
        ),
    },
    "followup-runner": {
        "kind": "agent_email_account",
        "required_roles": ("both",),
        "summary": (
            "Requires an enabled agent email account with read + send access "
            "(role `both`) so it can continue threads and send operator-facing outcomes."
        ),
    },
    "morning-agent": {
        "kind": "agent_email_account",
        "required_roles": ("both",),
        "summary": (
            "Requires an enabled agent email account with read + send access "
            "(role `both`) plus a configured operator recipient so it can "
            "send the daily briefing email."
        ),
    },
}


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def is_toggleable_core_script(name: str) -> bool:
    return _normalize_name(name) in TOGGLEABLE_CORE_SCRIPT_NAMES


def supports_operator_extra_instructions(name: str) -> bool:
    return _normalize_name(name) in TOGGLEABLE_CORE_SCRIPT_NAMES


def supports_schedule_override(name: str) -> bool:
    return _normalize_name(name) in _CORE_AUTOMATION_SCHEDULES


def _schedule_config_path() -> Path:
    try:
        from paths import config_dir

        return config_dir() / "schedule.json"
    except Exception:
        home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        return home / "config" / "schedule.json"


def _load_schedule_config_payload() -> dict[str, Any]:
    path = _schedule_config_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_schedule_config_payload(payload: dict[str, Any]) -> Path:
    path = _schedule_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    tmp_path.replace(path)
    return path


def load_core_automation_overrides() -> dict[str, dict[str, Any]]:
    payload = _load_schedule_config_payload()
    raw = payload.get(CORE_AUTOMATION_OVERRIDES_KEY)
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_override in raw.items():
        name = _normalize_name(raw_name)
        if not name or not isinstance(raw_override, dict):
            continue
        result[name] = dict(raw_override)
    return result


def _save_core_automation_overrides(overrides: dict[str, dict[str, Any]]) -> Path:
    payload = _load_schedule_config_payload()
    if overrides:
        payload[CORE_AUTOMATION_OVERRIDES_KEY] = overrides
    else:
        payload.pop(CORE_AUTOMATION_OVERRIDES_KEY, None)
    return _save_schedule_config_payload(payload)


def _manifest_candidates() -> list[Path]:
    candidates: list[Path] = []
    try:
        from paths import crons_dir

        candidates.append(crons_dir() / "manifest.json")
    except Exception:
        pass
    candidates.extend([
        Path(__file__).resolve().parent / "crons" / "manifest.json",
        Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent))) / "crons" / "manifest.json",
    ])
    seen: set[str] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            ordered.append(candidate)
    return ordered


def load_core_manifest_crons() -> list[dict[str, Any]]:
    for candidate in _manifest_candidates():
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text())
        except Exception:
            continue
        crons = payload.get("crons")
        if isinstance(crons, list):
            result: list[dict[str, Any]] = []
            for cron in crons:
                if isinstance(cron, dict):
                    result.append(dict(cron))
            return result
    return []


def get_core_manifest_cron(name: str) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    for cron in load_core_manifest_crons():
        if _normalize_name(cron.get("id", "")) == clean_name:
            return dict(cron)
    return {}


def _format_interval_label(interval_seconds: int) -> str:
    interval = max(1, int(interval_seconds or 0))
    if interval % 3600 == 0:
        hours = interval // 3600
        return f"every {hours}h"
    if interval % 60 == 0:
        minutes = interval // 60
        return f"every {minutes}m"
    return f"every {interval}s"


def _format_calendar_label(schedule: dict[str, Any]) -> str:
    try:
        hour = int(schedule.get("hour", 0))
        minute = int(schedule.get("minute", 0))
    except Exception:
        return ""
    label = f"{hour:02d}:{minute:02d}"
    if "weekday" in schedule:
        try:
            weekday = int(schedule.get("weekday", 0))
        except Exception:
            weekday = 0
        label += f" weekday={weekday}"
    else:
        label += " daily"
    return label


def _decorate_schedule_state(*, base_cron: dict[str, Any], effective_cron: dict[str, Any], source: str) -> dict[str, Any]:
    if effective_cron.get("interval_seconds"):
        return {
            "schedule_type": "interval",
            "interval_seconds": int(effective_cron.get("interval_seconds", 0) or 0),
            "default_interval_seconds": int(base_cron.get("interval_seconds", 0) or 0),
            "effective_schedule_label": _format_interval_label(int(effective_cron.get("interval_seconds", 0) or 0)),
            "schedule_source": source,
        }
    schedule = effective_cron.get("schedule")
    if isinstance(schedule, dict) and schedule:
        return {
            "schedule_type": "calendar",
            "interval_seconds": 0,
            "default_interval_seconds": 0,
            "schedule": dict(schedule),
            "default_schedule": dict(base_cron.get("schedule") or {}),
            "effective_schedule_label": _format_calendar_label(schedule),
            "schedule_source": source,
        }
    return {
        "schedule_type": "manual",
        "interval_seconds": 0,
        "default_interval_seconds": 0,
        "effective_schedule_label": "",
        "schedule_source": source,
    }


def _parse_daily_at(value: str) -> dict[str, int] | None:
    raw = str(value or "").strip()
    if not raw or ":" not in raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except Exception:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return {"hour": hour, "minute": minute}


def apply_core_automation_override(cron: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cron, dict):
        return {}
    clean_name = _normalize_name(cron.get("id", ""))
    effective = dict(cron)
    overrides = load_core_automation_overrides()
    override = overrides.get(clean_name) if clean_name else None
    if not override:
        effective["_schedule_source"] = "manifest"
        return effective

    schedule_meta = _CORE_AUTOMATION_SCHEDULES.get(clean_name) or {}
    if schedule_meta.get("kind") == "interval":
        try:
            interval = int(override.get("interval_seconds", 0) or 0)
        except Exception:
            interval = 0
        if interval > 0:
            effective["interval_seconds"] = interval
            effective.pop("schedule", None)
            effective["_schedule_source"] = "override"
            return effective
    if schedule_meta.get("kind") == "calendar":
        override_schedule = override.get("schedule")
        if isinstance(override_schedule, dict) and override_schedule:
            effective["schedule"] = dict(override_schedule)
            effective.pop("interval_seconds", None)
            effective["_schedule_source"] = "override"
            return effective
    effective["_schedule_source"] = "manifest"
    return effective


def apply_core_automation_overrides(crons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_core_automation_override(cron) for cron in crons]


def get_core_automation_schedule_state(name: str) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    base_cron = get_core_manifest_cron(clean_name)
    schedule_meta = _CORE_AUTOMATION_SCHEDULES.get(clean_name) or {}
    if not base_cron:
        return {
            "schedule_configurable": bool(schedule_meta),
            "schedule_type": "",
            "effective_schedule_label": "",
            "schedule_source": "",
            "interval_seconds": 0,
            "default_interval_seconds": 0,
            "minimum_interval_seconds": int(schedule_meta.get("minimum_interval_seconds", 0) or 0),
            "maximum_interval_seconds": int(schedule_meta.get("maximum_interval_seconds", 0) or 0),
            "interval_step_seconds": int(schedule_meta.get("interval_step_seconds", 0) or 0),
        }

    effective = apply_core_automation_override(base_cron)
    schedule_state = _decorate_schedule_state(
        base_cron=base_cron,
        effective_cron=effective,
        source=str(effective.get("_schedule_source") or "manifest"),
    )
    schedule_state.update({
        "schedule_configurable": bool(schedule_meta),
        "minimum_interval_seconds": int(schedule_meta.get("minimum_interval_seconds", 0) or 0),
        "maximum_interval_seconds": int(schedule_meta.get("maximum_interval_seconds", 0) or 0),
        "interval_step_seconds": int(schedule_meta.get("interval_step_seconds", 0) or 0),
    })
    return schedule_state


def _sync_core_crons_runtime() -> dict[str, Any]:
    try:
        from crons import sync as cron_sync

        if platform.system() == "Linux":
            cron_sync.sync_linux()
            return {"ok": True, "method": "systemd"}
        cron_sync.sync()
        return {"ok": True, "method": "launchagent"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def set_core_automation_schedule(
    name: str,
    *,
    interval_seconds: int | None = None,
    daily_at: str | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    schedule_meta = _CORE_AUTOMATION_SCHEDULES.get(clean_name)
    if not schedule_meta:
        return {
            "ok": False,
            "error": f"This automation does not support schedule overrides: {clean_name or name}",
        }

    base_cron = get_core_manifest_cron(clean_name)
    if not base_cron:
        return {"ok": False, "error": f"Core automation not found in manifest: {clean_name}"}

    overrides = load_core_automation_overrides()
    changed = False
    if clear:
        changed = clean_name in overrides
        overrides.pop(clean_name, None)
    else:
        try:
            kind = str(schedule_meta.get("kind") or "").strip().lower()
        except Exception:
            kind = ""
        if kind == "calendar":
            parsed_daily_at = _parse_daily_at(daily_at or "")
            if not parsed_daily_at:
                return {"ok": False, "error": "daily_at must use HH:MM (24h) format"}
            default_schedule = dict(base_cron.get("schedule") or {})
            next_schedule = dict(default_schedule)
            next_schedule.update(parsed_daily_at)
            next_override = {} if next_schedule == default_schedule else {"schedule": next_schedule}
        else:
            try:
                normalized_interval = int(interval_seconds or 0)
            except Exception:
                return {"ok": False, "error": "interval_seconds must be an integer > 0"}
            minimum = int(schedule_meta.get("minimum_interval_seconds", 0) or 0)
            maximum = int(schedule_meta.get("maximum_interval_seconds", 0) or 0)
            if normalized_interval <= 0:
                return {"ok": False, "error": "interval_seconds must be > 0"}
            if minimum and normalized_interval < minimum:
                return {"ok": False, "error": f"interval_seconds must be >= {minimum}"}
            if maximum and normalized_interval > maximum:
                return {"ok": False, "error": f"interval_seconds must be <= {maximum}"}
            default_interval = int(base_cron.get("interval_seconds", 0) or 0)
            next_override = {} if normalized_interval == default_interval else {"interval_seconds": normalized_interval}
        previous_override = dict(overrides.get(clean_name) or {})
        changed = previous_override != next_override
        if next_override:
            overrides[clean_name] = next_override
        else:
            overrides.pop(clean_name, None)

    config_path = _save_core_automation_overrides(overrides)
    state = get_core_automation_schedule_state(clean_name)
    sync_result = _sync_core_crons_runtime()
    return {
        "ok": True,
        "name": clean_name,
        "changed": changed,
        "config_path": str(config_path),
        "schedule_configurable": bool(state.get("schedule_configurable")),
        "schedule_type": str(state.get("schedule_type") or ""),
        "schedule_source": str(state.get("schedule_source") or ""),
        "effective_schedule_label": str(state.get("effective_schedule_label") or ""),
        "interval_seconds": int(state.get("interval_seconds", 0) or 0),
        "default_interval_seconds": int(state.get("default_interval_seconds", 0) or 0),
        "minimum_interval_seconds": int(state.get("minimum_interval_seconds", 0) or 0),
        "maximum_interval_seconds": int(state.get("maximum_interval_seconds", 0) or 0),
        "interval_step_seconds": int(state.get("interval_step_seconds", 0) or 0),
        "runtime_sync": sync_result,
    }


def _list_email_accounts(*, account_type: str | None = None) -> list[dict]:
    try:
        from db import init_db
        from db._email_accounts import list_email_accounts

        init_db()
        return list_email_accounts(include_disabled=True, account_type=account_type)
    except Exception:
        return []


def _legacy_email_config_available() -> bool:
    try:
        from email_config import load_email_config

        cfg = load_email_config()
    except Exception:
        cfg = None
    if not isinstance(cfg, dict):
        return False
    return bool(
        cfg.get("email")
        and cfg.get("imap_host")
        and cfg.get("smtp_host")
        and cfg.get("password")
    )


def _account_has_runtime_credentials(account: dict) -> bool:
    return bool(account.get("credential_service") and account.get("credential_key"))


def get_agent_email_account_status() -> dict[str, Any]:
    """Return whether an enabled `role=both` agent mailbox exists."""
    accounts = _list_email_accounts(account_type="agent")
    if not accounts:
        if _legacy_email_config_available():
            return {
                "available": True,
                "reason_code": "",
                "reason": "",
                "accounts": [],
                "eligible_labels": ["legacy-config"],
            }
        return {
            "available": False,
            "reason_code": "missing_account",
            "reason": (
                "No agent email account is configured yet. "
                "Add an enabled mailbox with role `both`."
            ),
            "accounts": [],
            "eligible_labels": [],
        }

    enabled_both: list[dict] = []
    enabled_wrong_role: list[dict] = []
    disabled_both: list[dict] = []
    incomplete_both: list[dict] = []
    missing_creds_both: list[dict] = []

    for account in accounts:
        row = {
            "label": str(account.get("label") or ""),
            "email": str(account.get("email") or ""),
            "role": str(account.get("role") or "both"),
            "enabled": bool(account.get("enabled", True)),
            "has_credential": _account_has_runtime_credentials(account),
            "imap_host": str(account.get("imap_host") or ""),
            "smtp_host": str(account.get("smtp_host") or ""),
        }
        role_both = row["role"] == "both"
        if not role_both:
            if row["enabled"]:
                enabled_wrong_role.append(row)
            continue
        if not row["enabled"]:
            disabled_both.append(row)
            continue
        if not (row["email"] and row["imap_host"] and row["smtp_host"]):
            incomplete_both.append(row)
            continue
        if not row["has_credential"]:
            missing_creds_both.append(row)
            continue
        enabled_both.append(row)

    if enabled_both:
        return {
            "available": True,
            "reason_code": "",
            "reason": "",
            "accounts": accounts,
            "eligible_labels": [row["label"] for row in enabled_both if row["label"]],
        }

    if missing_creds_both:
        labels = ", ".join(row["label"] for row in missing_creds_both if row["label"])
        return {
            "available": False,
            "reason_code": "missing_credentials",
            "reason": (
                "There is an agent account with role `both`, but its saved credential is missing"
                + (f" ({labels})." if labels else ".")
            ),
            "accounts": accounts,
            "eligible_labels": [],
        }

    if incomplete_both:
        labels = ", ".join(row["label"] for row in incomplete_both if row["label"])
        return {
            "available": False,
            "reason_code": "incomplete_account",
            "reason": (
                "There is an agent account with role `both`, but it is incomplete"
                + (f" ({labels})." if labels else ".")
            ),
            "accounts": accounts,
            "eligible_labels": [],
        }

    if disabled_both:
        labels = ", ".join(row["label"] for row in disabled_both if row["label"])
        return {
            "available": False,
            "reason_code": "disabled_account",
            "reason": (
                "There is an agent account with role `both`, but it is disabled"
                + (f" ({labels})." if labels else ".")
            ),
            "accounts": accounts,
            "eligible_labels": [],
        }

    if enabled_wrong_role:
        labels = ", ".join(row["label"] for row in enabled_wrong_role if row["label"])
        return {
            "available": False,
            "reason_code": "wrong_role",
            "reason": (
                "There are active email accounts, but none uses role `both`"
                + (f" ({labels})." if labels else ".")
            ),
            "accounts": accounts,
            "eligible_labels": [],
        }

    return {
        "available": False,
        "reason_code": "missing_account",
        "reason": (
            "No agent email account is configured yet. "
            "Add an enabled mailbox with role `both`."
        ),
        "accounts": accounts,
        "eligible_labels": [],
    }


def get_operator_briefing_recipient_status() -> dict[str, Any]:
    """Return whether a briefing recipient can be resolved for morning-agent."""
    try:
        from email_config import load_email_config, load_email_runtime_snapshot

        snapshot = load_email_runtime_snapshot() or {}
        default_operator = snapshot.get("default_operator_account") or {}
        default_email = str(default_operator.get("email") or "").strip()
        if default_email:
            label = (
                str(default_operator.get("label") or "").strip()
                or str(default_operator.get("description") or "").strip()
                or default_email
            )
            return {
                "available": True,
                "reason_code": "",
                "reason": "",
                "recipient_email": default_email,
                "recipient_label": label,
            }

        operator_accounts = list(snapshot.get("operator_accounts") or [])
        enabled_accounts = [
            account
            for account in operator_accounts
            if bool(account.get("enabled", True)) and str(account.get("email") or "").strip()
        ]
        if len(enabled_accounts) == 1:
            fallback = enabled_accounts[0]
            fallback_email = str(fallback.get("email") or "").strip()
            label = (
                str(fallback.get("label") or "").strip()
                or str(fallback.get("description") or "").strip()
                or fallback_email
            )
            return {
                "available": True,
                "reason_code": "",
                "reason": "",
                "recipient_email": fallback_email,
                "recipient_label": label,
            }

        cfg = load_email_config()
        legacy_email = str((cfg or {}).get("default_operator_email") or (cfg or {}).get("operator_email") or "").strip()
        if legacy_email:
            return {
                "available": True,
                "reason_code": "",
                "reason": "",
                "recipient_email": legacy_email,
                "recipient_label": legacy_email,
            }
    except Exception:
        pass

    return {
        "available": False,
        "reason_code": "missing_operator_recipient",
        "reason": (
            "No default operator recipient is configured yet. "
            "Add a default operator inbox/email so the daily briefing knows where to send."
        ),
        "recipient_email": "",
        "recipient_label": "",
    }


def get_script_runtime_contract(name: str) -> dict[str, Any]:
    clean_name = _normalize_name(name)
    email_requirement = _EMAIL_REQUIRED_SCRIPTS.get(clean_name)
    email_status = get_agent_email_account_status() if email_requirement else None
    recipient_status = get_operator_briefing_recipient_status() if clean_name == "morning-agent" else None
    schedule_state = get_core_automation_schedule_state(clean_name)
    available = bool(email_status.get("available", True)) if email_status else True
    blocked_reason = str(email_status.get("reason") or "") if email_status else ""
    blocked_reason_code = str(email_status.get("reason_code") or "") if email_status else ""
    if recipient_status and available and not recipient_status.get("available", False):
        available = False
        blocked_reason = str(recipient_status.get("reason") or "")
        blocked_reason_code = str(recipient_status.get("reason_code") or "")

    return {
        "name": clean_name,
        "toggleable_core": is_toggleable_core_script(clean_name),
        "supports_extra_instructions": supports_operator_extra_instructions(clean_name),
        "schedule_configurable": bool(schedule_state.get("schedule_configurable")),
        "schedule_type": str(schedule_state.get("schedule_type") or ""),
        "schedule_source": str(schedule_state.get("schedule_source") or ""),
        "effective_schedule_label": str(schedule_state.get("effective_schedule_label") or ""),
        "interval_seconds": int(schedule_state.get("interval_seconds", 0) or 0),
        "default_interval_seconds": int(schedule_state.get("default_interval_seconds", 0) or 0),
        "minimum_interval_seconds": int(schedule_state.get("minimum_interval_seconds", 0) or 0),
        "maximum_interval_seconds": int(schedule_state.get("maximum_interval_seconds", 0) or 0),
        "interval_step_seconds": int(schedule_state.get("interval_step_seconds", 0) or 0),
        "requires_email_account": bool(email_requirement),
        "requirement_kind": email_requirement.get("kind", "") if email_requirement else "",
        "required_roles": list(email_requirement.get("required_roles", ())) if email_requirement else [],
        "requirement_summary": str(email_requirement.get("summary", "")) if email_requirement else "",
        "available": available,
        "blocked_reason": blocked_reason,
        "blocked_reason_code": blocked_reason_code,
        "eligible_labels": list(email_status.get("eligible_labels") or []) if email_status else [],
    }


def get_script_extra_instructions(name_or_path: str) -> str:
    try:
        from db import init_db
        from db._personal_scripts import get_personal_script

        init_db()
        row = get_personal_script(name_or_path, include_core=True)
    except Exception:
        row = None
    if not row:
        return ""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get(EXTRA_INSTRUCTIONS_METADATA_KEY) or "").strip()


def format_operator_extra_instructions_block(name_or_path: str) -> str:
    instructions = get_script_extra_instructions(name_or_path)
    if not instructions:
        return ""
    return (
        "\n== ADDITIONAL OPERATOR INSTRUCTIONS FOR THIS AUTOMATION ==\n"
        f"{instructions}\n"
        "These instructions complement the automation's base behavior. "
        "Follow them unless they conflict with real data, safety constraints, "
        "or an explicit runtime rule.\n"
    )


def get_operator_profile() -> dict[str, Any]:
    operator_name = "the operator"
    assistant_name = "Nova"
    language = "en"
    operator_email = ""
    operator_accounts: list[dict] = []

    try:
        from paths import brain_dir

        cal_path = brain_dir() / "calibration.json"
        if cal_path.exists():
            payload = json.loads(cal_path.read_text())
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            operator_name = (
                str(user.get("name") or "").strip()
                or str(payload.get("user_name") or "").strip()
                or str(payload.get("name") or "").strip()
                or operator_name
            )
            assistant_name = (
                str(user.get("assistant_name") or "").strip()
                or str(payload.get("assistant_name") or "").strip()
                or assistant_name
            )
            language = (
                str(user.get("language") or "").strip()
                or str(payload.get("language") or "").strip()
                or str(payload.get("lang") or "").strip()
                or language
            )
    except Exception:
        pass

    aliases: list[str] = []
    try:
        from email_config import load_email_config, load_email_runtime_snapshot

        cfg = load_email_config()
        snapshot = load_email_runtime_snapshot()
        if isinstance(snapshot, dict):
            operator_accounts = list(snapshot.get("operator_accounts") or [])
            default_operator = snapshot.get("default_operator_account") or {}
            operator_email = str(default_operator.get("email") or operator_email).strip()
            for account in operator_accounts:
                value = str(account.get("email") or "").strip().lower()
                if value and value not in aliases:
                    aliases.append(value)
        if isinstance(cfg, dict):
            operator_email = str(cfg.get("operator_email") or operator_email).strip()
            raw_aliases = list(cfg.get("operator_aliases") or cfg.get("francisco_emails") or [])
            for candidate in [operator_email, *raw_aliases]:
                value = str(candidate or "").strip().lower()
                if value and value not in aliases:
                    aliases.append(value)
    except Exception:
        pass

    return {
        "operator_name": operator_name,
        "assistant_name": assistant_name,
        "language": language,
        "operator_email": operator_email,
        "operator_aliases": aliases,
        "operator_accounts": operator_accounts,
    }


def get_send_reply_script_path(*, local_script_dir: str | Path | None = None) -> Path:
    try:
        from paths import core_scripts_dir, personal_scripts_dir

        candidates: list[Path] = []
        if local_script_dir:
            candidates.append(Path(local_script_dir) / "nexo-send-reply.py")
        candidates.extend([
            core_scripts_dir() / "nexo-send-reply.py",
            personal_scripts_dir() / "nexo-send-reply.py",
        ])
        for candidate in candidates:
            if candidate.exists():
                return candidate
        if candidates:
            return candidates[0]
    except Exception:
        pass
    fallback_dir = Path(local_script_dir) if local_script_dir else Path.cwd()
    return fallback_dir / "nexo-send-reply.py"
