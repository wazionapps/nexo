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
CLI_TIMEOUT = 1800
MAX_DUE_ITEMS = 8
MAX_ACTIVE_ITEMS = 8
MAX_DIARY_ITEMS = 6


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
        "counts": {
            "due_reminders": len(due_reminders),
            "active_reminders": len(active_reminders),
            "due_followups": len(due_followups),
            "active_followups": len(active_followups),
        },
    }


def build_prompt(context: dict, *, extra_instructions_block: str = "") -> str:
    operator = context.get("operator") if isinstance(context.get("operator"), dict) else {}
    assistant = context.get("assistant") if isinstance(context.get("assistant"), dict) else {}
    operator_name = str(operator.get("name") or "the operator")
    operator_language = str(operator.get("language") or "en").strip() or "en"
    assistant_name = str(assistant.get("name") or "Nova")
    extra_block = extra_instructions_block.strip()
    extra_section = f"\n{extra_block}\n" if extra_block else ""
    context_json = json.dumps(context, indent=2, ensure_ascii=False)
    return (
        f"You are {assistant_name}, preparing the daily morning briefing email for {operator_name}.\n\n"
        "Write the email using ONLY the facts present in the structured context below.\n"
        f"Use the operator's preferred language: {operator_language}.\n"
        "If the language value is invalid or unclear, use English.\n\n"
        "Hard rules:\n"
        "- Do not invent achievements, blockers, meetings, messages, or external events.\n"
        "- Do not mention source files, JSON, MCP, prompts, or internal implementation.\n"
        "- Keep the tone calm, competent, and operator-facing.\n"
        "- Prioritise what changed recently, what is due now, what is blocked, and what deserves focus today.\n"
        "- If activity was quiet, say so plainly instead of padding.\n"
        "- Mention operator decisions only when the context actually supports them.\n"
        "- Keep the email concise: roughly 180-350 words.\n"
        "- Use short sections and bullets when useful.\n"
        f"{extra_section}"
        "Return ONLY a valid JSON object with this exact shape:\n"
        '{\n  "subject": "string",\n  "body": "string"\n}\n\n'
        "Structured context:\n"
        f"{context_json}\n"
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
        append_system_prompt=(
            "Return raw JSON only. No markdown fences. No commentary. "
            "No tool calls unless absolutely unavoidable."
        ),
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
            log(f"Morning briefing already sent today to {recipient}; use --force to resend.")
            return 0

    try:
        context = collect_context(profile)
        prompt = build_prompt(
            context,
            extra_instructions_block=format_operator_extra_instructions_block("morning-agent"),
        )
        subject, body = generate_briefing(prompt)
        write_latest_briefing(recipient=recipient or "[dry-run]", subject=subject, body=body)

        if args.dry_run:
            print(json.dumps({"subject": subject, "body": body}, indent=2, ensure_ascii=False))
            return 0

        log(f"Sending morning briefing to {recipient}...")
        send_output = send_briefing(recipient=recipient, subject=subject, body=body)
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
        log(f"Automation backend unavailable: {exc}")
        return 1
    except Exception as exc:
        log(f"Morning agent failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
