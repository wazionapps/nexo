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
import signal
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from automation_controls import (
    format_operator_extra_instructions_block,
    get_operator_briefing_recipient_status,
    get_operator_profile,
    get_script_runtime_contract,
    get_send_reply_script_path,
)
from client_preferences import resolve_automation_backend, resolve_client_runtime_profile
from core_prompts import render_core_prompt
from email_sent_events import format_recent_sent_email_block, recent_sent_emails
import db as nexo_db
from paths import data_dir, logs_dir, operations_dir
from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
LOG_DIR = logs_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "morning-agent.log"
STATE_FILE = data_dir() / "morning-agent-state.json"
LATEST_BRIEFING_FILE = operations_dir() / "morning-briefing-latest.md"
CALLER = "morning_agent"
CLI_TIMEOUT = 1500
MAX_DUE_ITEMS = 8
MAX_ACTIVE_ITEMS = 8
MAX_DIARY_ITEMS = 6
MORNING_BRIEFING_STALE_HOURS = 12
_ACTIVE_CLAIM: dict[str, str] = {}


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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS morning_briefing_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_date TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            subject TEXT DEFAULT '',
            send_output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT DEFAULT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(local_date, recipient)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_date "
        "ON morning_briefing_runs(local_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_status "
        "ON morning_briefing_runs(status)"
    )


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
                send_output = '',
                error = '',
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
                send_output = '',
                error = '',
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


def _mark_morning_briefing_sent(local_date: str, recipient: str, *, subject: str, send_output: str) -> None:
    now = datetime.now().astimezone().isoformat()
    conn = _morning_db_connection()
    _ensure_morning_briefing_runs_table(conn)
    conn.execute(
        """
        UPDATE morning_briefing_runs
        SET status = 'sent',
            subject = ?,
            send_output = ?,
            error = '',
            finished_at = ?,
            updated_at = ?
        WHERE local_date = ? AND recipient = ?
        """,
        (str(subject or ""), str(send_output or ""), now, now, str(local_date or ""), str(recipient or "")),
    )
    conn.commit()


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


def _serialize_reminders(filter_type: str, *, limit: int) -> list[dict]:
    rows = list(nexo_db.get_reminders(filter_type))
    result: list[dict] = []
    for row in rows[:limit]:
        result.append({
            "id": str(row.get("id") or ""),
            "description": _clean_text(row.get("description")),
            "date": str(row.get("date") or ""),
            "category": str(row.get("category") or ""),
            "status": str(row.get("status") or ""),
        })
    return result


def _serialize_followups(filter_type: str, *, limit: int) -> list[dict]:
    rows = list(nexo_db.get_followups(filter_type))
    result: list[dict] = []
    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status.startswith("COMPLETED") or status in {"DELETED", "ARCHIVED"}:
            continue
        result.append({
            "id": str(row.get("id") or ""),
            "description": _clean_text(row.get("description")),
            "date": str(row.get("date") or ""),
            "priority": _item_priority(row.get("priority")),
            "owner": str(row.get("owner") or ""),
            "status": str(row.get("status") or ""),
            "verification": _clean_text(row.get("verification"), limit=180),
            "reasoning": _clean_text(row.get("reasoning"), limit=180),
        })
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


def collect_context(profile: dict) -> dict:
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
    recent_sent = _serialize_recent_sent_emails()
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "today": date.today().isoformat(),
        "operator": {
            "name": str(profile.get("operator_name") or "the operator"),
            "language": str(profile.get("language") or "en"),
            "email": str(profile.get("operator_email") or ""),
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


def generate_briefing(prompt: str) -> tuple[str, str]:
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
        bare_mode=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"automation backend exited {result.returncode}")

    payload = _extract_json_payload(result.stdout or "")
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not subject or not body:
        raise RuntimeError("Morning agent output is missing subject/body.")
    return subject, body


def write_latest_briefing(*, recipient: str, subject: str, body: str) -> None:
    LATEST_BRIEFING_FILE.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        f"# Morning briefing\n\n"
        f"- Generated at: {datetime.now().astimezone().isoformat()}\n"
        f"- To: {recipient}\n"
        f"- Subject: {subject}\n\n"
        f"{body}\n"
    )
    LATEST_BRIEFING_FILE.write_text(rendered, encoding="utf-8")


def send_briefing(*, recipient: str, subject: str, body: str) -> str:
    sender = get_send_reply_script_path(local_script_dir=_script_dir)
    if not sender.exists():
        raise RuntimeError(f"nexo-send-reply.py not found at {sender}")

    tmp_fd, tmp_path = tempfile.mkstemp(prefix="morning-briefing-", suffix=".txt")
    os.close(tmp_fd)
    Path(tmp_path).write_text(body, encoding="utf-8")
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
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

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
        context = collect_context(profile)
        prompt = build_prompt(
            context,
            extra_instructions_block=format_operator_extra_instructions_block("morning-agent"),
        )
        subject, body = generate_briefing(prompt)
        body = append_recent_sent_email_block(body)
        write_latest_briefing(recipient=recipient or "[dry-run]", subject=subject, body=body)

        if args.dry_run:
            print(json.dumps({"subject": subject, "body": body}, indent=2, ensure_ascii=False))
            return 0

        log(f"Sending morning briefing to {recipient}...")
        send_output = send_briefing(recipient=recipient, subject=subject, body=body)
        _mark_morning_briefing_sent(today, recipient, subject=subject, send_output=send_output)
        _clear_active_claim()
        save_state({
            "last_sent_date": today,
            "last_sent_at": datetime.now().astimezone().isoformat(),
            "last_recipient": recipient,
            "last_subject": subject,
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
