#!/usr/bin/env python3
# nexo: name=morning-agent
# nexo: description=Generate and send the operator's daily morning briefing email.
# nexo: category=automation
# nexo: runtime=python
# nexo: timeout=1800
# nexo: cron_id=morning-agent
# nexo: schedule=07:00
# nexo: schedule_required=true
# nexo: recovery_policy=catchup
# nexo: run_on_boot=false
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=86400
# nexo: doctor_allow_db=true

"""NEXO Morning Agent — generic operator briefing automation.

This is the productized core counterpart to the older personal-only
morning digest script. It deliberately avoids operator-specific
business logic and builds the briefing from shared-brain state:

- recent diary summaries
- due reminders
- due / active followups
- operator profile + email routing config

The operator can further steer tone/scope through the standard
per-automation extra-instructions surface without editing this file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from automation_preferences import format_automation_preferences_prompt_block, get_automation_preferences
from automation_controls import (
    format_operator_extra_instructions_block,
    get_operator_briefing_recipient_status,
    get_operator_profile,
    get_script_runtime_contract,
    get_send_reply_script_path,
)
from client_preferences import resolve_automation_backend, resolve_client_runtime_profile
from core_prompts import render_core_prompt
from email_presentation import build_email_presentation, normalize_agent_email_payload
from email_sent_events import format_recent_sent_email_block, recent_sent_emails
from morning_briefing import (
    LATEST_MARKDOWN_FILE,
    ensure_morning_briefing_runs_table as _ensure_briefing_schema,
    mark_morning_briefing_sent as _persist_morning_briefing_sent,
    write_latest_briefing_artifacts,
)
import db as nexo_db
from paths import data_dir, logs_dir
from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
LOG_DIR = logs_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "morning-agent.log"
STATE_FILE = data_dir() / "morning-agent-state.json"
LATEST_BRIEFING_FILE = LATEST_MARKDOWN_FILE
CALLER = "morning_agent"
CLI_TIMEOUT = 1500
MAX_DUE_ITEMS = 8
MAX_ACTIVE_ITEMS = 8
MAX_DIARY_ITEMS = 6
HISTORY_SIGNAL_LIMIT = 5
MORNING_BRIEFING_STALE_HOURS = 12
_ACTIVE_CLAIM: dict[str, str] = {}
HTTP_TIMEOUT = 7
NEWS_MAX_HEADLINES = 8
NEWS_MAX_FEEDS = 5
RESOLUTION_SIGNAL_WORDS = (
    "already decided",
    "already resolved",
    "cerrado",
    "closed",
    "completed",
    "covered",
    "cubierto",
    "decidido",
    "descartad",
    "false alarm",
    "falsa alarma",
    "monitor activo",
    "no accionable",
    "resolved",
    "resuelto",
)
APPROVAL_SIGNAL_WORDS = (
    "awaiting approval",
    "espera luz verde",
    "esperando luz verde",
    "needs approval",
    "pending approval",
)
TOPIC_STOPWORDS = {
    "about",
    "after",
    "before",
    "briefing",
    "check",
    "para",
    "pendiente",
    "review",
    "sobre",
    "ticket",
    "update",
    "with",
}
NEWS_INTEREST_QUERY_ES = {
    "business": "empresa economia negocios",
    "technology": "tecnologia inteligencia artificial software",
    "finance": "finanzas economia mercados",
    "local": "actualidad local",
    "health": "salud sanidad medicina",
    "legal": "legal justicia normativa",
    "education": "educacion universidad formacion",
    "real_estate": "vivienda inmobiliario urbanismo",
    "science": "ciencia investigacion innovacion",
    "culture": "cultura sociedad",
    "sports": "deportes",
}
NEWS_INTEREST_QUERY_EN = {
    "business": "business economy companies",
    "technology": "technology artificial intelligence software",
    "finance": "finance economy markets",
    "local": "local news",
    "health": "health medicine healthcare",
    "legal": "law regulation justice",
    "education": "education university training",
    "real_estate": "housing real estate urban planning",
    "science": "science research innovation",
    "culture": "culture society",
    "sports": "sports",
}
NEWS_EXCLUDED_KEYWORDS = {
    "politics": ["politica", "politics", "eleccion", "election", "partido", "congreso", "senado", "parliament"],
    "sports": ["deporte", "sports", "futbol", "football", "liga", "tenis", "basket", "baloncesto"],
    "celebrity": ["famos", "celebrity", "celebrit", "television", "tv", "influencer"],
    "crime": ["crimen", "crime", "asesin", "homicid", "robo", "suceso", "detenido", "arrest"],
    "crypto": ["crypto", "cripto", "bitcoin", "ethereum", "blockchain", "nft"],
    "market_noise": ["bolsa", "stock", "stocks", "market", "mercado", "wall street", "ibex", "nasdaq"],
}


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        payload = json.loads(STATE_FILE.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def _morning_db_connection():
    nexo_db.init_db()
    return nexo_db.get_db()


def _ensure_morning_briefing_runs_table(conn) -> None:
    _ensure_briefing_schema(conn)


def _row_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _briefing_run_is_stale(row: dict) -> bool:
    started_raw = str(row.get("started_at") or "").strip()
    if not started_raw:
        return True
    try:
        started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
        now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
        return (now - started).total_seconds() > (MORNING_BRIEFING_STALE_HOURS * 3600)
    except Exception:
        return True


def _mark_stale_morning_briefing_failed(conn, row: dict, *, now: str) -> None:
    conn.execute(
        """
        UPDATE morning_briefing_runs
        SET status = 'failed',
            error = ?,
            finished_at = COALESCE(finished_at, ?),
            updated_at = ?
        WHERE local_date = ? AND recipient = ? AND status = 'in_progress'
        """,
        (
            "stale in_progress reconciled before retry: parent process likely interrupted before completion",
            now,
            now,
            str(row.get("local_date") or ""),
            str(row.get("recipient") or ""),
        ),
    )
    conn.commit()


def _claim_morning_briefing_send(local_date: str, recipient: str, *, force: bool = False) -> dict:
    clean_date = str(local_date or "").strip()
    clean_recipient = str(recipient or "").strip()
    if not clean_date or not clean_recipient:
        return {"ok": False, "acquired": False, "reason": "missing recipient"}
    now = datetime.now().astimezone().isoformat()
    conn = _morning_db_connection()
    _ensure_morning_briefing_runs_table(conn)
    if force:
        conn.execute(
            """
            INSERT INTO morning_briefing_runs
                (local_date, recipient, status, subject, send_output, error, started_at, finished_at, updated_at)
            VALUES (?, ?, 'in_progress', '', '', '', ?, NULL, ?)
            ON CONFLICT(local_date, recipient) DO UPDATE SET
                status = 'in_progress',
                subject = '',
                body_text = '',
                body_html = '',
                artifact_json = '',
                send_output = '',
                error = '',
                desktop_shown_at = NULL,
                desktop_opened_at = NULL,
                desktop_dismissed_at = NULL,
                started_at = excluded.started_at,
                finished_at = NULL,
                updated_at = excluded.updated_at
            """,
            (clean_date, clean_recipient, now, now),
        )
        conn.commit()
        return {"ok": True, "acquired": True, "reason": "force"}

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO morning_briefing_runs
            (local_date, recipient, status, started_at, updated_at)
        VALUES (?, ?, 'in_progress', ?, ?)
        """,
        (clean_date, clean_recipient, now, now),
    )
    conn.commit()
    if int(cur.rowcount or 0) == 1:
        return {"ok": True, "acquired": True, "reason": "new"}

    row = _row_dict(conn.execute(
        "SELECT * FROM morning_briefing_runs WHERE local_date = ? AND recipient = ?",
        (clean_date, clean_recipient),
    ).fetchone())
    status = str(row.get("status") or "").strip().lower()
    stale_retry = status == "in_progress" and _briefing_run_is_stale(row)
    if stale_retry:
        _mark_stale_morning_briefing_failed(conn, row, now=now)
    if status == "failed" or stale_retry:
        conn.execute(
            """
            UPDATE morning_briefing_runs
            SET status = 'in_progress',
                subject = '',
                body_text = '',
                body_html = '',
                artifact_json = '',
                send_output = '',
                error = '',
                desktop_shown_at = NULL,
                desktop_opened_at = NULL,
                desktop_dismissed_at = NULL,
                started_at = ?,
                finished_at = NULL,
                updated_at = ?
            WHERE local_date = ? AND recipient = ?
            """,
            (now, now, clean_date, clean_recipient),
        )
        conn.commit()
        return {
            "ok": True,
            "acquired": True,
            "reason": "retry_stale" if stale_retry else "retry",
            "previous_run": row,
        }
    return {"ok": True, "acquired": False, "reason": status or "already claimed", "run": row}


def _record_existing_morning_briefing_sent(local_date: str, recipient: str, state: dict) -> None:
    now = datetime.now().astimezone().isoformat()
    conn = _morning_db_connection()
    _ensure_morning_briefing_runs_table(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO morning_briefing_runs
            (local_date, recipient, status, subject, send_output, error, started_at, finished_at, updated_at)
        VALUES (?, ?, 'sent', ?, ?, '', ?, ?, ?)
        """,
        (
            str(local_date or "").strip(),
            str(recipient or "").strip(),
            str(state.get("last_subject") or ""),
            str(state.get("last_send_output") or ""),
            str(state.get("last_sent_at") or now),
            str(state.get("last_sent_at") or now),
            now,
        ),
    )
    conn.commit()


def _mark_morning_briefing_sent(
    local_date: str,
    recipient: str,
    *,
    subject: str,
    body_text: str,
    body_html: str,
    send_output: str,
    artifact_payload: dict | None = None,
) -> None:
    _persist_morning_briefing_sent(
        local_date=local_date,
        recipient=recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        send_output=send_output,
        artifact_payload=artifact_payload,
    )


def _mark_morning_briefing_failed(local_date: str, recipient: str, *, error: str) -> None:
    now = datetime.now().astimezone().isoformat()
    conn = _morning_db_connection()
    _ensure_morning_briefing_runs_table(conn)
    conn.execute(
        """
        UPDATE morning_briefing_runs
        SET status = 'failed',
            error = ?,
            finished_at = ?,
            updated_at = ?
        WHERE local_date = ? AND recipient = ?
        """,
        (str(error or "")[:1000], now, now, str(local_date or ""), str(recipient or "")),
    )
    conn.commit()


def _set_active_claim(local_date: str, recipient: str) -> None:
    _ACTIVE_CLAIM.clear()
    if local_date and recipient:
        _ACTIVE_CLAIM.update({"local_date": str(local_date), "recipient": str(recipient)})


def _clear_active_claim() -> None:
    _ACTIVE_CLAIM.clear()


def _handle_shutdown_signal(signum, _frame) -> None:
    local_date = _ACTIVE_CLAIM.get("local_date", "")
    recipient = _ACTIVE_CLAIM.get("recipient", "")
    signal_name = getattr(signal.Signals(signum), "name", f"SIG{signum}")
    if local_date and recipient:
        try:
            _mark_morning_briefing_failed(
                local_date,
                recipient,
                error=f"interrupted before completion: {signal_name}",
            )
        except Exception as exc:
            log(f"Failed to mark morning briefing interrupted by {signal_name}: {exc}")
    log(f"Morning agent interrupted by {signal_name}.")
    raise SystemExit(128 + int(signum))


def _install_shutdown_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)


def resolve_recipient(profile: dict | None = None, *, explicit_to: str = "") -> str:
    override = str(explicit_to or "").strip()
    if override:
        return override

    recipient_status = get_operator_briefing_recipient_status()
    recipient_email = str(recipient_status.get("recipient_email") or "").strip()
    if recipient_email:
        return recipient_email

    payload = profile or {}
    operator_email = str(payload.get("operator_email") or "").strip()
    if operator_email:
        return operator_email

    for account in list(payload.get("operator_accounts") or []):
        candidate = str(account.get("email") or "").strip()
        if candidate:
            return candidate
    return ""


def _clean_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _item_priority(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean in {"critical", "high", "medium", "low"}:
        return clean
    return ""


def _parse_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text)).astimezone()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _followup_recency_fields(row: dict) -> dict:
    created = _parse_timestamp(row.get("created_at"))
    updated = _parse_timestamp(row.get("updated_at")) or created
    now = datetime.now().astimezone()
    days_open = None
    days_since_activity = None
    if created:
        days_open = max(0, (now.date() - created.date()).days)
    if updated:
        days_since_activity = max(0, (now.date() - updated.date()).days)
    stale = bool(days_since_activity is not None and days_since_activity >= 3)
    return {
        "created_at": created.isoformat() if created else "",
        "last_activity": updated.isoformat() if updated else "",
        "days_open": days_open,
        "days_since_activity": days_since_activity,
        "stale_without_recent_signal": stale,
    }


def _compact_item_history(item_type: str, item_id: str, *, limit: int = HISTORY_SIGNAL_LIMIT) -> list[dict]:
    if not item_id:
        return []
    try:
        rows = nexo_db.get_item_history(item_type, item_id, limit=limit)
    except Exception:
        return []
    result: list[dict] = []
    for row in rows:
        note = _clean_text(row.get("note"), limit=220)
        event_type = str(row.get("event_type") or "")
        if not note and not event_type:
            continue
        result.append({
            "event_type": event_type,
            "actor": str(row.get("actor") or ""),
            "note": note,
            "created_at": str(row.get("created_at") or ""),
        })
    return result


def _resolution_state(status: str, history: list[dict], *text_fields: object) -> str:
    clean_status = str(status or "").strip().upper()
    if clean_status.startswith("COMPLETED") or clean_status in {"DELETED", "ARCHIVED", "BLOCKED", "WAITING"}:
        return "closed_or_non_operational"

    signal_text = " ".join(
        [
            str(status or ""),
            *[str(value or "") for value in text_fields],
            *[
                f"{entry.get('event_type', '')} {entry.get('note', '')}"
                for entry in history
                if isinstance(entry, dict)
            ],
        ]
    ).lower()
    if any(word in signal_text for word in APPROVAL_SIGNAL_WORDS):
        return "awaiting_user_approval"
    if any(word in signal_text for word in RESOLUTION_SIGNAL_WORDS):
        return "resolved_or_decided_signal"
    return "active"


def _status_claim_guard(resolution_state: str) -> str:
    if resolution_state == "awaiting_user_approval":
        return "Do not describe as authorized/done; state that it is waiting for approval."
    if resolution_state == "resolved_or_decided_signal":
        return "Do not present resolved/decided subtopics as new decisions."
    if resolution_state == "closed_or_non_operational":
        return "Do not present this as operationally pending."
    return ""


def _topic_signature(item: dict) -> str:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("description", "verification", "reasoning")
    )
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]{4,}", normalized)
        if token not in TOPIC_STOPWORDS
    ]
    unique = list(dict.fromkeys(tokens))
    if len(unique) < 3:
        return f"id:{item.get('id', '')}"
    return "topic:" + " ".join(sorted(unique[:10]))


def _dedupe_context_groups(*groups: list[dict]) -> tuple[list[dict], ...]:
    seen: set[str] = set()
    output: list[list[dict]] = []
    for group in groups:
        kept: list[dict] = []
        for item in group:
            signature = _topic_signature(item)
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            kept.append(item)
        output.append(kept)
    return tuple(output)


def _serialize_reminders(filter_type: str, *, limit: int) -> list[dict]:
    rows = list(nexo_db.get_reminders(filter_type))
    result: list[dict] = []
    for row in rows[:limit]:
        item_id = str(row.get("id") or "")
        history = _compact_item_history("reminder", item_id)
        resolution_state = _resolution_state(
            str(row.get("status") or ""),
            history,
            row.get("description"),
        )
        result.append({
            "id": item_id,
            "description": _clean_text(row.get("description")),
            "date": str(row.get("date") or ""),
            "category": str(row.get("category") or ""),
            "status": str(row.get("status") or ""),
            "recent_history": history,
            "resolution_state": resolution_state,
            "has_resolution_signal": resolution_state in {"closed_or_non_operational", "resolved_or_decided_signal"},
            "status_claim_guard": _status_claim_guard(resolution_state),
        })
    return result


def _serialize_followups(filter_type: str, *, limit: int) -> list[dict]:
    rows = list(nexo_db.get_followups(filter_type))
    result: list[dict] = []
    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status.startswith("COMPLETED") or status in {"DELETED", "ARCHIVED"}:
            continue
        item_id = str(row.get("id") or "")
        history = _compact_item_history("followup", item_id)
        resolution_state = _resolution_state(
            row.get("status"),
            history,
            row.get("description"),
            row.get("verification"),
            row.get("reasoning"),
        )
        item = {
            "id": item_id,
            "description": _clean_text(row.get("description")),
            "date": str(row.get("date") or ""),
            "priority": _item_priority(row.get("priority")),
            "owner": str(row.get("owner") or ""),
            "status": str(row.get("status") or ""),
            "verification": _clean_text(row.get("verification"), limit=180),
            "reasoning": _clean_text(row.get("reasoning"), limit=180),
            "recent_history": history,
            "resolution_state": resolution_state,
            "has_resolution_signal": resolution_state in {"closed_or_non_operational", "resolved_or_decided_signal"},
            "status_claim_guard": _status_claim_guard(resolution_state),
        }
        item.update(_followup_recency_fields(row))
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _serialize_diaries(*, limit: int) -> list[dict]:
    rows = list(nexo_db.read_session_diary(last_day=True, include_automated=True))
    result: list[dict] = []
    for row in rows:
        summary = _clean_text(row.get("summary"), limit=280)
        pending = _clean_text(row.get("pending"), limit=220)
        if not summary and not pending:
            continue
        result.append({
            "created_at": str(row.get("created_at") or ""),
            "domain": str(row.get("domain") or ""),
            "source": str(row.get("source") or ""),
            "summary": summary,
            "pending": pending,
            "context_next": _clean_text(row.get("context_next"), limit=180),
        })
        if len(result) >= limit:
            break
    return result


def _serialize_recent_sent_emails(*, limit: int = 8) -> list[dict]:
    result: list[dict] = []
    try:
        rows = recent_sent_emails(hours=24, limit=limit)
    except Exception:
        return result
    for row in rows:
        result.append({
            "sent_at": str(row.get("sent_at") or ""),
            "to": _clean_text(row.get("to_addrs"), limit=180),
            "subject": _clean_text(row.get("subject"), limit=220),
            "source": str(row.get("source") or ""),
            "message_id": str(row.get("message_id") or ""),
        })
    return result


def _fetch_json_url(url: str, *, timeout: int = HTTP_TIMEOUT) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NEXO-Morning-Agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(512_000)
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def _fetch_text_url(url: str, *, timeout: int = HTTP_TIMEOUT) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NEXO-Morning-Agent/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(512_000).decode("utf-8", errors="replace")


def _desktop_settings_candidates() -> list[Path]:
    home = Path.home()
    candidates = [
        Path(os.environ.get("NEXO_DESKTOP_SETTINGS", "")),
        Path(os.environ.get("NEXO_DESKTOP_USER_DATA", "")) / "app-settings.json",
        home / "Library" / "Application Support" / "nexo-desktop-mvp" / "app-settings.json",
        home / "Library" / "Application Support" / "NEXO Desktop" / "app-settings.json",
        home / "Library" / "Application Support" / "nexo-desktop" / "app-settings.json",
    ]
    return [candidate for candidate in candidates if str(candidate)]


def _read_desktop_settings() -> dict:
    for candidate in _desktop_settings_candidates():
        try:
            if candidate.is_file():
                payload = json.loads(candidate.read_text())
                if isinstance(payload, dict):
                    return payload
        except Exception:
            continue
    return {}


def _normalize_location_candidate(value: object) -> dict:
    if not value:
        return {}
    candidate = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            candidate = json.loads(text)
        except Exception:
            return {"name": text}
    if not isinstance(candidate, dict):
        return {}
    lat = candidate.get("lat", candidate.get("latitude"))
    lon = candidate.get("lon", candidate.get("longitude"))
    name = str(candidate.get("name") or candidate.get("display") or candidate.get("city") or "").strip()
    try:
        lat_value = float(lat)
        lon_value = float(lon)
    except Exception:
        lat_value = None
        lon_value = None
    if lat_value is not None and lon_value is not None:
        return {"lat": lat_value, "lon": lon_value, "name": name}
    return {"name": name} if name else {}


def _geocode_location_name(name: str, *, language: str = "es") -> dict:
    clean = str(name or "").strip()
    if not clean:
        return {}
    params = urllib.parse.urlencode({
        "name": clean,
        "count": "1",
        "language": "en" if str(language).lower().startswith("en") else "es",
        "format": "json",
    })
    payload = _fetch_json_url(f"https://geocoding-api.open-meteo.com/v1/search?{params}")
    hit = (payload.get("results") or [None])[0]
    if not isinstance(hit, dict):
        return {}
    try:
        lat = float(hit.get("latitude"))
        lon = float(hit.get("longitude"))
    except Exception:
        return {}
    label_parts = [
        str(hit.get("name") or clean).strip(),
        str(hit.get("admin1") or "").strip(),
        str(hit.get("country") or "").strip(),
    ]
    return {
        "lat": lat,
        "lon": lon,
        "name": ", ".join(part for part in label_parts if part),
    }


def _resolve_weather_location(profile: dict) -> dict:
    settings = _read_desktop_settings()
    app = settings.get("app") if isinstance(settings.get("app"), dict) else {}
    app_loc = app.get("location") if isinstance(app.get("location"), dict) else {}
    language = str(app.get("ui_language") or profile.get("language") or "es")

    explicit = _normalize_location_candidate(app_loc)
    if explicit.get("lat") is not None and explicit.get("lon") is not None:
        return explicit
    if explicit.get("name"):
        try:
            geocoded = _geocode_location_name(str(explicit.get("name")), language=language)
            if geocoded:
                return geocoded
        except Exception:
            pass

    desktop_profile = settings.get("profile") if isinstance(settings.get("profile"), dict) else {}
    for source in [
        desktop_profile.get("current_residence"),
        profile.get("current_residence"),
        profile.get("location"),
        profile.get("coordinates"),
    ]:
        candidate = _normalize_location_candidate(source)
        if candidate.get("lat") is not None and candidate.get("lon") is not None:
            return candidate
        if candidate.get("name"):
            try:
                geocoded = _geocode_location_name(str(candidate.get("name")), language=language)
                if geocoded:
                    return geocoded
            except Exception:
                continue

    direct = _normalize_location_candidate({
        "latitude": profile.get("latitude"),
        "longitude": profile.get("longitude"),
        "name": profile.get("current_residence") or "",
    })
    return direct


def _weather_code_label(code: object) -> str:
    try:
        value = int(code)
    except Exception:
        return ""
    if value == 0:
        return "clear"
    if value in {1, 2, 3}:
        return "partly cloudy"
    if value in {45, 48}:
        return "fog"
    if value in {51, 53, 55, 56, 57}:
        return "drizzle"
    if value in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if value in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if value in {95, 96, 99}:
        return "storm"
    return "unknown"


def _collect_weather(profile: dict) -> dict:
    try:
        loc = _resolve_weather_location(profile)
        if not loc or loc.get("lat") is None or loc.get("lon") is None:
            return {"available": False, "error": "no_location"}
        params = urllib.parse.urlencode({
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "current_weather": "true",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
        })
        payload = _fetch_json_url(f"https://api.open-meteo.com/v1/forecast?{params}")
        current = payload.get("current_weather") if isinstance(payload.get("current_weather"), dict) else {}
        daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
        if not current:
            return {"available": False, "error": "weather_unavailable", "location": loc.get("name") or ""}
        code = current.get("weathercode")
        return {
            "available": True,
            "source": "open-meteo",
            "location": loc.get("name") or "",
            "temperature_c": current.get("temperature"),
            "weather_code": code,
            "weather": _weather_code_label(code),
            "high_c": (daily.get("temperature_2m_max") or [None])[0],
            "low_c": (daily.get("temperature_2m_min") or [None])[0],
            "precipitation_probability_max": (daily.get("precipitation_probability_max") or [None])[0],
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:240]}


def _fold_match(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return asciiish.casefold()


def _preference_list(preferences: dict | None, key: str, default: list[str] | None = None) -> list[str]:
    values = preferences if isinstance(preferences, dict) else {}
    raw = values.get(key)
    if isinstance(raw, (list, tuple, set)):
        items = [str(item or "").strip() for item in raw]
    elif raw:
        items = [part.strip() for part in str(raw).replace(";", ",").split(",")]
    else:
        items = list(default or [])
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _news_locale(profile: dict) -> tuple[str, str]:
    language = str(profile.get("language") or "").strip().lower()
    residence = _fold_match(profile.get("current_residence") or profile.get("location") or "")
    if language.startswith("en"):
        return "en", "US"
    if any(token in residence for token in ["united states", "usa", "eeuu", "estados unidos"]):
        return "en", "US"
    return "es", "ES"


def _news_feed_url(query: str, *, language: str, country: str) -> str:
    lang = "en" if language.startswith("en") else "es"
    params = {
        "hl": lang,
        "gl": country,
        "ceid": f"{country}:{lang}",
    }
    clean_query = str(query or "").strip()
    if clean_query:
        params["q"] = clean_query
        return f"https://news.google.com/rss/search?{urllib.parse.urlencode(params)}"
    return f"https://news.google.com/rss?{urllib.parse.urlencode(params)}"


def _profile_news_terms(profile: dict) -> list[str]:
    role = _fold_match(profile.get("role") or profile.get("profession") or profile.get("job_title") or "")
    residence = str(profile.get("current_residence") or profile.get("location") or "").strip()
    terms: list[str] = []
    role_queries = [
        (["fundador", "founder", "ceo", "directivo", "manager", "business"], "empresa economia tecnologia"),
        (["developer", "programador", "software", "technical", "tecnico", "engineer"], "tecnologia software inteligencia artificial"),
        (["medic", "doctor", "clinica", "health", "sanidad"], "salud sanidad medicina"),
        (["arquitect", "architecture", "inmobili", "real estate"], "vivienda urbanismo arquitectura"),
        (["abog", "law", "legal"], "legal normativa justicia"),
        (["educ", "profesor", "student", "estudiante"], "educacion formacion universidad"),
        (["ventas", "sales", "commercial", "comercial"], "ventas clientes empresa"),
        (["administr", "admin", "office"], "empresa laboral administracion"),
    ]
    for needles, query in role_queries:
        if any(needle in role for needle in needles):
            terms.append(query)
            break
    if residence:
        terms.append(f"{residence} actualidad")
    return terms


def _news_queries(profile: dict, preferences: dict | None) -> tuple[list[dict[str, str]], list[str], list[str]]:
    language, country = _news_locale(profile)
    interests = _preference_list(preferences, "news_interests", ["automatic"])
    excluded = _preference_list(preferences, "excluded_topics", [])
    automatic = not interests or "automatic" in interests
    query_map = NEWS_INTEREST_QUERY_EN if language.startswith("en") else NEWS_INTEREST_QUERY_ES
    queries: list[dict[str, str]] = []

    if automatic:
        for term in _profile_news_terms(profile):
            queries.append({"interest": "automatic", "query": term})
        fallback = "business technology economy" if language.startswith("en") else "empresa tecnologia economia"
        queries.append({"interest": "automatic", "query": fallback})

    for interest in interests:
        if interest == "automatic":
            continue
        query = query_map.get(interest)
        if not query:
            continue
        if interest == "local":
            residence = str(profile.get("current_residence") or profile.get("location") or "").strip()
            if residence:
                query = f"{residence} actualidad"
        queries.append({"interest": interest, "query": query})

    if not queries:
        queries.append({"interest": "automatic", "query": ""})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in queries:
        key = _fold_match(query.get("query") or "") or "top"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
        if len(deduped) >= NEWS_MAX_FEEDS:
            break
    return deduped, interests or ["automatic"], excluded


def _headline_is_excluded(title: str, excluded_topics: list[str]) -> bool:
    folded = _fold_match(title)
    for topic in excluded_topics:
        for keyword in NEWS_EXCLUDED_KEYWORDS.get(topic, []):
            if _fold_match(keyword) in folded:
                return True
    return False


def _parse_news_feed(xml_text: str, *, interest: str, excluded_topics: list[str]) -> list[dict]:
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title"), limit=220)
        if not title or _headline_is_excluded(title, excluded_topics):
            continue
        link = str(item.findtext("link") or "").strip()
        source = _clean_text(item.findtext("source"), limit=80)
        published = _clean_text(item.findtext("pubDate"), limit=120)
        items.append({
            "title": title,
            "source": source,
            "published": published,
            "url": link[:500],
            "interest": interest,
        })
    return items


def _collect_news(profile: dict, preferences: dict | None = None) -> dict:
    try:
        language, country = _news_locale(profile)
        queries, interests, excluded = _news_queries(profile, preferences)
        items: list[dict] = []
        seen_titles: set[str] = set()
        used_queries: list[str] = []
        for query in queries:
            feed_url = _news_feed_url(query.get("query") or "", language=language, country=country)
            xml_text = _fetch_text_url(feed_url)
            used_queries.append(query.get("query") or "top headlines")
            for headline in _parse_news_feed(xml_text, interest=query.get("interest") or "automatic", excluded_topics=excluded):
                key = _fold_match(headline.get("title") or "")
                if not key or key in seen_titles:
                    continue
                seen_titles.add(key)
                items.append(headline)
                if len(items) >= NEWS_MAX_HEADLINES:
                    break
            if len(items) >= NEWS_MAX_HEADLINES:
                break
        return {
            "available": bool(items),
            "source": "google-news-rss",
            "mode": "relevant_public_context",
            "language": language,
            "country": country,
            "interests": interests,
            "excluded_topics": excluded,
            "queries": used_queries,
            "selection_rule": "Headlines are only useful if they relate to the operator's work, location, explicit interests or broad high-impact context.",
            "headlines": items,
            "error": "" if items else "empty_feed",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:240], "headlines": []}


def _collect_external_context(profile: dict, preferences: dict | None) -> dict:
    values = preferences if isinstance(preferences, dict) else {}
    external: dict[str, Any] = {}
    if values.get("weather"):
        external["weather"] = _collect_weather(profile)
    if values.get("news"):
        external["news"] = _collect_news(profile, values)
    return external


def collect_context(profile: dict, preferences: dict | None = None) -> dict:
    nexo_db.init_db()
    due_followups = _serialize_followups("due", limit=MAX_DUE_ITEMS)
    due_followup_ids = {row["id"] for row in due_followups}
    active_followups = [
        row
        for row in _serialize_followups("active", limit=MAX_ACTIVE_ITEMS + MAX_DUE_ITEMS)
        if row["id"] not in due_followup_ids
    ][:MAX_ACTIVE_ITEMS]
    due_reminders = _serialize_reminders("due", limit=MAX_DUE_ITEMS)
    due_reminder_ids = {row["id"] for row in due_reminders}
    active_reminders = [
        row
        for row in _serialize_reminders("active", limit=MAX_ACTIVE_ITEMS + MAX_DUE_ITEMS)
        if row["id"] not in due_reminder_ids
    ][:MAX_ACTIVE_ITEMS]
    due_followups, active_followups, due_reminders, active_reminders = _dedupe_context_groups(
        due_followups,
        active_followups,
        due_reminders,
        active_reminders,
    )
    recent_sent = _serialize_recent_sent_emails()
    external = _collect_external_context(profile, preferences)
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "today": date.today().isoformat(),
        "operator": {
            "name": str(profile.get("operator_name") or "the operator"),
            "language": str(profile.get("language") or "en"),
            "email": str(profile.get("operator_email") or ""),
            "role": str(profile.get("role") or ""),
            "technical_level": str(profile.get("technical_level") or ""),
            "residence": str(profile.get("current_residence") or ""),
        },
        "assistant": {
            "name": str(profile.get("assistant_name") or "Nova"),
        },
        "due_reminders": due_reminders,
        "active_reminders": active_reminders,
        "due_followups": due_followups,
        "active_followups": active_followups,
        "recent_diaries": _serialize_diaries(limit=MAX_DIARY_ITEMS),
        "recent_sent_emails_24h": recent_sent,
        "external": external,
        "counts": {
            "due_reminders": len(due_reminders),
            "active_reminders": len(active_reminders),
            "due_followups": len(due_followups),
            "active_followups": len(active_followups),
            "recent_sent_emails_24h": len(recent_sent),
        },
    }


def append_recent_sent_email_block(body: str) -> str:
    try:
        block = format_recent_sent_email_block(hours=24, limit=8)
    except Exception:
        block = ""
    if not block or "EMAILS ENVIADOS ULTIMAS 24H" in body:
        return body
    return body.rstrip() + "\n\n" + block + "\n"


def build_prompt(context: dict, *, extra_instructions_block: str = "") -> str:
    operator = context.get("operator") if isinstance(context.get("operator"), dict) else {}
    assistant = context.get("assistant") if isinstance(context.get("assistant"), dict) else {}
    operator_name = str(operator.get("name") or "the operator")
    operator_language = str(operator.get("language") or "en").strip() or "en"
    assistant_name = str(assistant.get("name") or "Nova")
    extra_block = extra_instructions_block.strip()
    extra_section = f"\n{extra_block}\n" if extra_block else ""
    context_json = json.dumps(context, indent=2, ensure_ascii=False)
    return render_core_prompt(
        "morning-agent",
        assistant_name=assistant_name,
        operator_name=operator_name,
        operator_language=operator_language,
        extra_section=extra_section,
        context_json=context_json,
    )


def _extract_json_payload(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    candidates = [text]
    if text.startswith("```"):
        stripped = text
        if stripped.startswith("```json"):
            stripped = stripped[len("```json"):].strip()
        elif stripped.startswith("```"):
            stripped = stripped[3:].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        candidates.append(stripped)
    left = text.find("{")
    right = text.rfind("}")
    if left != -1 and right > left:
        candidates.append(text[left:right + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Morning agent returned invalid JSON output.")


def generate_briefing(prompt: str):
    backend = resolve_automation_backend()
    profile = resolve_client_runtime_profile(backend) if backend != "none" else {"model": "", "reasoning_effort": ""}
    profile_label = profile.get("model") or "default"
    if profile.get("reasoning_effort"):
        profile_label = f"{profile_label}/{profile['reasoning_effort']}"
    log(f"Launching {backend} ({profile_label}) for morning briefing...")

    env = os.environ.copy()
    env["NEXO_HEADLESS"] = "1"
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    result = run_automation_prompt(
        prompt,
        caller=CALLER,
        env=env,
        timeout=CLI_TIMEOUT,
        output_format="json",
        append_system_prompt=render_core_prompt("morning-agent-json-output"),
        allowed_tools="Read,Glob,Grep",
        bare_mode=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"automation backend exited {result.returncode}")

    payload = _extract_json_payload(result.stdout or "")
    try:
        return normalize_agent_email_payload(payload)
    except RuntimeError as exc:
        raise RuntimeError("Morning agent output is missing subject/body.") from exc


def write_latest_briefing(
    *,
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str,
    local_date: str = "",
    run_id: int | None = None,
) -> dict:
    return write_latest_briefing_artifacts(
        recipient=recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        local_date=local_date,
        run_id=run_id,
    )


def send_briefing(*, recipient: str, subject: str, body_text: str, body_html: str) -> str:
    sender = get_send_reply_script_path(local_script_dir=_script_dir)
    if not sender.exists():
        raise RuntimeError(f"nexo-send-reply.py not found at {sender}")

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="morning-briefing-", suffix=".txt")
    os.close(tmp_fd)
    html_fd, html_path = tempfile.mkstemp(prefix="morning-briefing-", suffix=".html")
    os.close(html_fd)
    Path(tmp_path).write_text(body_text, encoding="utf-8")
    Path(html_path).write_text(body_html, encoding="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(sender),
                "--to",
                recipient,
                "--subject",
                subject,
                "--body-file",
                tmp_path,
                "--html-file",
                html_path,
                "--audience",
                "operator",
                "--message-kind",
                "morning_briefing",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        Path(html_path).unlink(missing_ok=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"nexo-send-reply exited {result.returncode}")
    return (result.stdout or "").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and send the daily operator morning briefing.")
    parser.add_argument("--to", default="", help="Override recipient email.")
    parser.add_argument("--force", action="store_true", help="Send even if today's briefing was already delivered.")
    parser.add_argument("--dry-run", action="store_true", help="Generate the briefing but do not send it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _install_shutdown_signal_handlers()
    contract = get_script_runtime_contract("morning-agent")
    if not args.dry_run and not contract.get("available", True):
        log(f"Runtime blocked: {contract.get('blocked_reason') or 'missing prerequisite'}")
        return 0

    profile = get_operator_profile()
    recipient = resolve_recipient(profile, explicit_to=args.to)
    if not recipient and not args.dry_run:
        log("Runtime blocked: no operator recipient configured for morning-agent.")
        return 0

    state = load_state()
    today = date.today().isoformat()
    if not args.force and not args.dry_run:
        if state.get("last_sent_date") == today and state.get("last_recipient") == recipient:
            _record_existing_morning_briefing_sent(today, recipient, state)
            log(f"Morning briefing already sent today to {recipient}; use --force to resend.")
            return 0
        claim = _claim_morning_briefing_send(today, recipient)
        if not claim.get("acquired"):
            log(f"Morning briefing already handled today for {recipient}.")
            return 0
        _set_active_claim(today, recipient)
    elif args.force and not args.dry_run:
        _claim_morning_briefing_send(today, recipient, force=True)
        _set_active_claim(today, recipient)

    try:
        preference_contract = get_automation_preferences("morning-agent")
        preference_values = (
            (preference_contract.get("preferences") or {}).get("values")
            if isinstance(preference_contract, dict)
            else {}
        )
        context = collect_context(profile, preference_values if isinstance(preference_values, dict) else {})
        extra_blocks = "\n".join(
            block
            for block in [
                format_automation_preferences_prompt_block("morning-agent"),
                format_operator_extra_instructions_block("morning-agent"),
            ]
            if block.strip()
        )
        prompt = build_prompt(
            context,
            extra_instructions_block=extra_blocks,
        )
        presentation = generate_briefing(prompt)
        body_text = append_recent_sent_email_block(presentation.body_text)
        if body_text != presentation.body_text:
            presentation = build_email_presentation(subject=presentation.subject, body_text=body_text)
        artifact_payload = write_latest_briefing(
            recipient=recipient or "[dry-run]",
            subject=presentation.subject,
            body_text=presentation.body_text,
            body_html=presentation.body_html,
            local_date=today,
        )

        if args.dry_run:
            print(json.dumps({
                "subject": presentation.subject,
                "body": presentation.body_text,
                "body_text": presentation.body_text,
                "body_html": presentation.body_html,
            }, indent=2, ensure_ascii=False))
            return 0

        log(f"Sending morning briefing to {recipient}...")
        send_output = send_briefing(
            recipient=recipient,
            subject=presentation.subject,
            body_text=presentation.body_text,
            body_html=presentation.body_html,
        )
        _mark_morning_briefing_sent(
            today,
            recipient,
            subject=presentation.subject,
            body_text=presentation.body_text,
            body_html=presentation.body_html,
            send_output=send_output,
            artifact_payload=artifact_payload,
        )
        _clear_active_claim()
        save_state({
            "last_sent_date": today,
            "last_sent_at": datetime.now().astimezone().isoformat(),
            "last_recipient": recipient,
            "last_subject": presentation.subject,
            "last_send_output": send_output,
        })
        log("Morning briefing sent.")
        return 0
    except AutomationBackendUnavailableError as exc:
        if not args.dry_run and recipient:
            _mark_morning_briefing_failed(today, recipient, error=str(exc))
            _clear_active_claim()
        log(f"Automation backend unavailable: {exc}")
        return 1
    except Exception as exc:
        if not args.dry_run and recipient:
            _mark_morning_briefing_failed(today, recipient, error=str(exc))
            _clear_active_claim()
        log(f"Morning agent failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
