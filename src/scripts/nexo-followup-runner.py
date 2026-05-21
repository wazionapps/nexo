#!/usr/bin/env python3
# nexo: name=followup-runner
# nexo: description=Continuous NEXO pending-work runner. Executes due followups, avoids overlap, and escalates operator attention through reminders/orchestrator.
# nexo: category=automation
# nexo: runtime=python
# nexo: timeout=10800
# nexo: cron_id=followup-runner
# nexo: interval_seconds=3600
# nexo: schedule_required=true
# nexo: recovery_policy=run_once_on_wake
# nexo: run_on_boot=false
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=7200
# nexo: doctor_allow_db=true

"""
NEXO Followup Runner v8 — continuous pending-work runner.

Role:
1. Pick up due or recurring followups that should already be running.
2. Process them through the real NEXO runtime and its MCP surface.
3. Avoid overlap via lock + bounded timeout.
4. Escalate operator attention through standard NEXO reminders when needed.

From the operator's point of view, these are all "pending items". Internally,
followups and reminders remain distinct, but the runner focuses on executable work.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from paths import data_dir, db_path, logs_dir
from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from automation_controls import (
    format_operator_extra_instructions_block,
    get_operator_profile,
    get_script_runtime_contract,
    get_send_reply_script_path,
)
from client_preferences import resolve_automation_backend, resolve_client_runtime_profile
from constants import AUTOMATION_SUBPROCESS_TIMEOUT
from core_prompts import render_core_prompt
from operator_language import build_operator_language_contract, normalize_operator_language
import db as nexo_db

NEXO_DB = db_path()
LOG_DIR = logs_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "followup-runner.log"
STATE_FILE = data_dir() / "followup-state.json"
RESULTS_FILE = data_dir() / "followup-runner-results.json"

CLI_TIMEOUT = AUTOMATION_SUBPROCESS_TIMEOUT
LOCK_FILE = LOG_DIR / "followup-runner.lock"
MAX_FOLLOWUPS_PER_RUN = 5  # Focus: Opus can actually execute 5, not 30
COOLDOWN_DAYS = 3  # Don't retry waiting_user/stale_review/blocked for 3 days
STALE_FOLLOWUP_TRIAGE_DAYS = 14
MAX_STALE_TRIAGE_PER_RUN = 8
MAX_NEEDS_OPERATOR_BRIEFING = 12
DEFAULT_ASSISTANT_NAME = "Nova"
DEFAULT_OPERATOR_LANGUAGE = "en"

# ── Logging ─────────────────────────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── State tracking ──────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── DB access ───────────────────────────────────────────────────────────
def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _followup_days_overdue(date_value: str, *, today_value: date | None = None) -> int:
    due = _parse_date(date_value)
    if not due:
        return 0
    today_obj = today_value or date.today()
    return max(0, (today_obj - due).days)


def _history_has_recent_movement(history, *, days: int = STALE_FOLLOWUP_TRIAGE_DAYS) -> bool:
    if not history:
        return False
    cutoff = date.today() - timedelta(days=days)
    for event in history:
        if not isinstance(event, dict):
            continue
        created = _parse_date(str(event.get("created_at") or event.get("date") or ""))
        if created and created >= cutoff:
            return True
    return False


def _is_stale_followup_for_triage(followup: dict) -> bool:
    status = str(followup.get("status") or "").strip().lower()
    if status in {"needs_decision", "waiting_user", "blocked", "waiting", "stale_review"}:
        return False
    if _followup_days_overdue(str(followup.get("date") or "")) < STALE_FOLLOWUP_TRIAGE_DAYS:
        return False
    return not _history_has_recent_movement(followup.get("history") or [])


def _is_in_cooldown(fu_id: str, state: dict) -> bool:
    """Check if a followup was recently attempted and should be skipped."""
    attempts = state.get("attempts", {})
    last = attempts.get(fu_id)
    if not last:
        return False
    last_status = last.get("status", "")
    if last_status not in ("needs_decision", "waiting_user", "stale_review", "blocked"):
        return False
    last_date_str = last.get("date", "")
    if not last_date_str:
        return False
    try:
        last_date = date.fromisoformat(last_date_str)
        return (date.today() - last_date).days < COOLDOWN_DAYS
    except ValueError:
        return False


def record_attempt(state: dict, fu_id: str, status: str):
    """Record an attempt for cooldown tracking."""
    if "attempts" not in state:
        state["attempts"] = {}
    state["attempts"][fu_id] = {
        "status": status,
        "date": date.today().isoformat(),
    }


def _operator_attention_label_set(operator_name: str = "") -> tuple[str, str, str]:
    clean_operator = " ".join(str(operator_name or "").split()).strip()
    subject = clean_operator if clean_operator else "the operator"
    return (
        f"this pending item needs {subject} to decide, approve, reply, or provide missing input before the automation can continue",
        "this pending item can continue without operator input and the automation should keep working on its own",
        "this pending item is only waiting on a customer, vendor, colleague, or external system rather than on the operator",
    )


def _operator_language(operator: dict | None = None) -> str:
    payload = operator if isinstance(operator, dict) else get_operator_profile()
    return normalize_operator_language(
        str(payload.get("language") or DEFAULT_OPERATOR_LANGUAGE).strip() or DEFAULT_OPERATOR_LANGUAGE
    )


def _uses_spanish(language: str) -> bool:
    return _operator_language({"language": language}).startswith("es")


def _fallback_operator_attention_hint(followup: dict) -> bool:
    """Last-resort structural fallback.

    Keep this intentionally narrow: product direction is semantic
    classification via the local classifier / LLM gate, not bilingual
    keyword lists wired to specific phrasings.
    """
    status = str(followup.get("status") or "").strip().lower()
    owner = str(followup.get("owner") or "").strip().lower()
    if status in {"needs_decision", "waiting_user"}:
        return True
    if owner == "user":
        return True
    if owner == "waiting":
        return False
    return False


def _classifier_requires_operator_attention(text: str, operator_name: str = "") -> bool | None:
    clean_text = " ".join(str(text or "").split())
    if len(clean_text) < 12:
        return None

    needs_attention, can_continue, waiting_external = _operator_attention_label_set(operator_name)
    clean_operator = " ".join(str(operator_name or "").split()).strip()
    subject = clean_operator if clean_operator else "the operator"
    question = render_core_prompt(
        "followup-runner-operator-attention-question",
        subject=subject,
    )
    context = render_core_prompt(
        "followup-runner-operator-attention-context",
        pending_item=clean_text,
    )
    try:
        from semantic_router import route as semantic_route
    except Exception:
        return None
    try:
        result = semantic_route(
            decision_kind="followup_operator_attention",
            question=question,
            context=context,
            labels=(needs_attention, can_continue, waiting_external),
        )
    except Exception:
        return None
    if not result.ok:
        return None
    label = result.label or result.verdict
    if label == needs_attention:
        return True
    if label in {can_continue, waiting_external}:
        return False
    return None


def _llm_requires_operator_attention(text: str, operator_name: str = "") -> bool | None:
    return _classifier_requires_operator_attention(text, operator_name=operator_name)


def _followup_needs_operator_attention(followup: dict, operator_name: str = "") -> bool:
    status = str(followup.get("status") or "").strip().lower()
    owner = str(followup.get("owner") or "").strip().lower()
    if status in {"needs_decision", "waiting_user"}:
        return True
    if owner == "user":
        return True
    if owner == "waiting":
        return False

    semantic_text = "\n".join(
        part
        for part in (
            str(followup.get("description") or "").strip(),
            str(followup.get("reasoning") or "").strip(),
            str(followup.get("verification") or "").strip(),
        )
        if part
    )
    classifier_verdict = _classifier_requires_operator_attention(
        semantic_text,
        operator_name=operator_name,
    )
    if classifier_verdict is not None:
        return classifier_verdict
    llm_verdict = _llm_requires_operator_attention(
        semantic_text,
        operator_name=operator_name,
    )
    if llm_verdict is not None:
        return llm_verdict
    return _fallback_operator_attention_hint(followup)


def get_all_active_followups(state: dict) -> dict:
    """Returns followups grouped by category for the briefing."""
    operator = get_operator_profile()
    operator_name = str(operator.get("operator_name") or "the operator")
    if not NEXO_DB.exists():
        log(f"DB not found: {NEXO_DB}")
        return {"actionable": [], "needs_operator": [], "future": [], "backlog": [], "cooled_down": [], "stale_triage": []}

    today = date.today().isoformat()
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    try:
        snapshot = nexo_db.followup_lifecycle_snapshot(limit=5000)
        rows = [
            item for item in (snapshot.get("lanes") or {}).get("active", [])
            if not str(item.get("description") or "").startswith("[Abandoned]")
        ]
        rows.sort(
            key=lambda item: (
                {"critical": 1, "high": 2, "medium": 3, "low": 4}.get(str(item.get("priority") or "medium"), 5),
                str(item.get("date") or "9999-12-31"),
            )
        )

        result = {"actionable": [], "needs_operator": [], "future": [], "backlog": [], "cooled_down": [], "stale_triage": []}
        undated_triage_budget = 2

        for row in rows:
            fu = dict(row)
            try:
                detail = nexo_db.get_followup(fu["id"], include_history=True)
            except Exception:
                detail = None
            if detail:
                fu["history"] = detail.get("history") or []
                fu["history_rules"] = detail.get("history_rules") or []
            fu_date = fu.get("date") or ""
            needs_operator = _followup_needs_operator_attention(
                fu,
                operator_name=operator_name,
            )

            if not fu_date:
                result["backlog"].append(fu)
                if undated_triage_budget > 0:
                    triage_fu = dict(fu)
                    triage_fu["triage_only"] = True
                    result["actionable"].append(triage_fu)
                    undated_triage_budget -= 1
            elif fu_date <= today:
                if _is_stale_followup_for_triage(fu):
                    stale_fu = dict(fu)
                    stale_fu["stale_triage"] = True
                    stale_fu["days_overdue"] = _followup_days_overdue(fu_date)
                    result["stale_triage"].append(stale_fu)
                elif needs_operator:
                    result["needs_operator"].append(fu)
                elif _is_in_cooldown(fu["id"], state):
                    result["cooled_down"].append(fu)
                else:
                    result["actionable"].append(fu)
            else:
                result["future"].append(fu)

        # Cap actionable to MAX_FOLLOWUPS_PER_RUN — focus over breadth
        if len(result["actionable"]) > MAX_FOLLOWUPS_PER_RUN:
            overflow = result["actionable"][MAX_FOLLOWUPS_PER_RUN:]
            result["actionable"] = result["actionable"][:MAX_FOLLOWUPS_PER_RUN]
            log(f"Capped actionable to {MAX_FOLLOWUPS_PER_RUN}, deferred {len(overflow)} to next run")
        if len(result["needs_operator"]) > MAX_NEEDS_OPERATOR_BRIEFING:
            overflow = result["needs_operator"][MAX_NEEDS_OPERATOR_BRIEFING:]
            result["needs_operator"] = result["needs_operator"][:MAX_NEEDS_OPERATOR_BRIEFING]
            log(f"Capped needs_operator to {MAX_NEEDS_OPERATOR_BRIEFING}, deferred {len(overflow)} noisy items")

        return result

    except Exception as e:
        log(f"DB error: {e}")
        return {"actionable": [], "needs_operator": [], "future": [], "backlog": [], "cooled_down": [], "stale_triage": []}
    finally:
        conn.close()


def advance_recurrent(fu_id: str, recurrence: str, result_summary: str = ""):
    """Advance a recurrent followup to next occurrence instead of completing."""
    if not recurrence:
        return

    today = date.today()
    next_date = None

    if recurrence == "daily":
        next_date = today + timedelta(days=1)
    elif recurrence.startswith("weekly:"):
        day_name = recurrence.split(":")[1].lower()
        days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6}
        target = days_map.get(day_name, 0)
        days_ahead = (target - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_date = today + timedelta(days=days_ahead)
    elif recurrence == "monthly":
        if today.month == 12:
            next_date = today.replace(year=today.year + 1, month=1)
        else:
            next_date = today.replace(month=today.month + 1)

    if next_date:
        try:
            if result_summary:
                nexo_db.add_followup_note(
                    fu_id,
                    f"Recurrent run executed: {result_summary}",
                    actor="followup-runner",
                )
            nexo_db.update_followup(
                fu_id,
                date=next_date.isoformat(),
                history_actor="followup-runner",
                history_event="rescheduled",
                history_note=f"Recurrent followup advanced to {next_date.isoformat()} after execution.",
            )
            log(f"  {fu_id}: recurrent → next date {next_date.isoformat()}")
        except Exception as exc:
            log(f"  {fu_id}: failed to advance recurrence ({exc})")


def followup_status(fu_id: str) -> str:
    conn = sqlite3.connect(str(NEXO_DB))
    try:
        row = conn.execute("SELECT status FROM followups WHERE id = ?", (fu_id,)).fetchone()
        return str(row[0]) if row and row[0] is not None else ""
    finally:
        conn.close()


def complete_followup_if_needed(fu_id: str, result_summary: str = ""):
    status = followup_status(fu_id).lower()
    if status == "completed":
        return
    try:
        nexo_db.complete_followup(fu_id, result_summary)
        log(f"  {fu_id}: marked completed por el runner")
    except Exception as exc:
        log(f"  {fu_id}: failed to mark followup as completed ({exc})")


def update_followup_fields(
    fu_id: str,
    *,
    date_value: str = "",
    verification: str = "",
    status: str = "",
    priority: str = "",
    history_event: str = "updated",
    history_note: str = "",
):
    try:
        fields = {}
        if date_value:
            fields["date"] = date_value
        if verification:
            fields["verification"] = verification
        if status:
            fields["status"] = status
        if priority:
            fields["priority"] = priority
        if not fields:
            return True
        result = nexo_db.update_followup(
            fu_id,
            history_actor="followup-runner",
            history_event=history_event,
            history_note=history_note,
            **fields,
        )
        if result.get("error"):
            raise RuntimeError(result["error"])
        return True
    except Exception as exc:
        log(f"  {fu_id}: failed to update followup ({exc})")
        return False


def render_options(options) -> str:
    if not options:
        return ""
    if isinstance(options, dict):
        chunks = []
        for key, value in options.items():
            chunks.append(f"{key}) {value}")
        return " | ".join(chunks)
    return str(options)


def attention_reminder_id(fu_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9-]+", "-", fu_id).strip("-") or "item"
    return f"R-FU-{safe}"[:96]


def attention_reminder_category(status: str) -> str:
    return "decisions" if status in {"needs_decision", "waiting_user", "stale_review"} else "waiting"


def attention_reminder_description(
    fu_id: str,
    *,
    summary: str,
    options,
    status: str,
    operator_language: str,
) -> str:
    detail = " ".join((summary or "").split())
    if not detail:
        detail = (
            "The runner cannot close this item without operator input."
            if _uses_spanish(operator_language)
            else "The runner cannot close this item without operator input."
        )
    description = f"{fu_id}: {detail}"
    opts_text = render_options(options)
    if opts_text:
        description += f" {'Options' if _uses_spanish(operator_language) else 'Options'}: {opts_text}"
    return description[:480]


def upsert_attention_reminder(
    fu_id: str,
    *,
    summary: str,
    options,
    status: str,
    operator_language: str,
):
    reminder_id = attention_reminder_id(fu_id)
    description = attention_reminder_description(
        fu_id,
        summary=summary,
        options=options,
        status=status,
        operator_language=operator_language,
    )
    category = attention_reminder_category(status)
    today = date.today().isoformat()
    existing = nexo_db.get_reminder(reminder_id)

    if existing:
        result = nexo_db.update_reminder(
            reminder_id,
            description=description,
            date=today,
            status="PENDING",
            category=category,
            history_actor="followup-runner",
            history_event="updated",
            history_note=f"{fu_id}: status={status}",
        )
        if result.get("error"):
            log(f"  {fu_id}: failed to update reminder {reminder_id} ({result['error']})")
            return
        nexo_db.add_reminder_note(reminder_id, description, actor="followup-runner")
        log(f"  {fu_id}: reminder {reminder_id} updated for orchestrator")
        return

    result = nexo_db.create_reminder(
        reminder_id,
        description,
        date=today,
        category=category,
    )
    if result.get("error"):
        log(f"  {fu_id}: failed to create reminder {reminder_id} ({result['error']})")
        return
    nexo_db.add_reminder_note(
        reminder_id,
        f"source_followup={fu_id} status={status}",
        actor="followup-runner",
    )
    log(f"  {fu_id}: reminder {reminder_id} created for orchestrator")


def resolve_attention_reminder(fu_id: str, *, resolution: str = ""):
    reminder_id = attention_reminder_id(fu_id)
    existing = nexo_db.get_reminder(reminder_id)
    if not existing:
        return
    current_status = str(existing.get("status") or "").upper()
    if current_status.startswith("COMPLETED") or current_status == "DELETED":
        return
    if resolution:
        nexo_db.add_reminder_note(
            reminder_id,
            f"Resolved from {fu_id}: {resolution[:300]}",
            actor="followup-runner",
        )
    result = nexo_db.complete_reminder(reminder_id)
    if result.get("error"):
        log(f"  {fu_id}: failed to complete reminder {reminder_id} ({result['error']})")
        return
    log(f"  {fu_id}: reminder {reminder_id} marked completed")


def defer_followup_after_attention(
    fu_id: str,
    *,
    summary: str,
    options,
    status: str,
    priority: str = "",
    operator_language: str,
):
    next_review = (date.today() + timedelta(days=1)).isoformat()
    details = summary.strip()
    opts_text = render_options(options)
    if opts_text:
        details = f"{details}\nOptions: {opts_text}"
    if details:
        note_result = nexo_db.add_followup_note(
            fu_id,
            f"{status}: {details}",
            actor="followup-runner",
        )
        if note_result.get("error"):
            log(f"  {fu_id}: failed to append history note ({note_result['error']})")
    ok = update_followup_fields(
        fu_id,
        date_value=next_review,
        status="PENDING",
        priority=priority,
        history_event="rescheduled",
        history_note=f"status={status}; next_review={next_review}",
    )
    if ok:
        log(f"  {fu_id}: {status} → reprogramado para {next_review}")
    upsert_attention_reminder(
        fu_id,
        summary=summary,
        options=options,
        status=status,
        operator_language=operator_language,
    )


def render_history_preview(events) -> list[str]:
    if not events:
        return []
    lines = []
    for event in list(events)[:3]:
        stamp = str(event.get("created_at") or "?")
        event_type = str(event.get("event_type") or "event")
        actor = str(event.get("actor") or "system")
        note = str(event.get("note") or "").strip()
        suffix = f" — {note}" if note else ""
        lines.append(f"      - {stamp} [{event_type}] ({actor}){suffix}")
    return lines


# ── Lock ────────────────────────────────────────────────────────────────
def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            return False
        except (ProcessLookupError, ValueError):
            pass
        except PermissionError:
            return False
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Recent activity context ────────────────────────────────────────────
def get_recent_activity(hours: int = 24) -> str:
    """Build a summary of what the runner did in the last N hours."""
    lines = []
    try:
        conn = sqlite3.connect(str(NEXO_DB))
        conn.row_factory = sqlite3.Row

        # Recent followup-runner diary entries
        diaries = conn.execute(
            "SELECT summary, created_at FROM session_diary "
            "WHERE domain='followup-runner' AND created_at >= datetime('now', ?)"
            "ORDER BY created_at DESC LIMIT 5",
            (f"-{hours} hours",)
        ).fetchall()
        if diaries:
            lines.append("EXECUTED IN THE LAST 24H:")
            for d in diaries:
                summary = str(d["summary"] or "")[:200]
                ts = str(d["created_at"] or "")[:16]
                lines.append(f"  [{ts}] {summary}")

        # Recent followup notes from the runner
        notes = conn.execute(
            "SELECT item_id AS followup_id, note, created_at FROM item_history "
            "WHERE item_type='followup' AND actor='followup-runner' AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT 10",
            ((datetime.now() - timedelta(hours=hours)).timestamp(),),
        ).fetchall()
        if notes:
            lines.append("\nFOLLOWUP NOTES WRITTEN (last 24h):")
            seen = set()
            for n in notes:
                fid = str(n["followup_id"] or "")
                if fid in seen:
                    continue
                seen.add(fid)
                note_text = str(n["note"] or "")[:150]
                lines.append(f"  {fid}: {note_text}")

        conn.close()
    except Exception as e:
        lines.append(f"(failed to read recent activity: {e})")

    return "\n".join(lines) if lines else ""


# ── Build prompt for Opus ───────────────────────────────────────────────
def build_prompt(actionable: list[dict]) -> str:
    operator = get_operator_profile()
    operator_name = str(operator.get("operator_name") or "the operator")
    assistant_name = str(operator.get("assistant_name") or DEFAULT_ASSISTANT_NAME)
    operator_language = _operator_language(operator)
    operator_email = str(operator.get("operator_email") or "").strip()
    send_reply_script = get_send_reply_script_path(local_script_dir=_script_dir)
    send_target = operator_email or "OPERATOR_EMAIL_NOT_CONFIGURED"
    extra_instructions_block = format_operator_extra_instructions_block("followup-runner")

    sections = []
    for i, fu in enumerate(actionable, 1):
        sections.append(f"\n[{i}] {fu['id']} (prioridad: {fu.get('priority', 'medium')})")
        sections.append(f"    Description: {fu['description']}")
        if fu.get("verification"):
            sections.append(f"    Verification: {fu['verification']}")
        if fu.get("reasoning"):
            sections.append(f"    Context: {fu['reasoning']}")
        if fu.get("history_rules"):
            sections.append("    Rules:")
            sections.extend(f"      - {rule}" for rule in fu["history_rules"][:3])
        history_preview = render_history_preview(fu.get("history") or [])
        if history_preview:
            sections.append("    Recent history:")
            sections.extend(history_preview)
        if fu.get("recurrence"):
            sections.append(
                f"    Recurrence: {fu['recurrence']} — do NOT mark completed; only report the result"
            )
        if fu.get("triage_only"):
            sections.append("    Mode: TRIAGE — this item has no due date. Set one if the timing is clear, or explain why it remains in backlog.")

    followup_text = "\n".join(sections)
    results_path = str(RESULTS_FILE)

    # Recent activity context
    recent = get_recent_activity(24)
    recent_block = ""
    if recent:
        recent_block = f"""
== CONTEXT: WHAT YOU DID IN THE LAST 24H ==
{recent}

Use this to avoid repeating work that was already done and to preserve continuity.
Do not repeat queries, verifications, or operator emails that already happened today.
"""

    proactive_block = ""
    return render_core_prompt(
        "followup-runner",
        assistant_name=assistant_name,
        work_intro=(
            "You have " + str(len(actionable)) + " followups to EXECUTE — not to classify."
            if actionable
            else "There are no pending followups. Enter PROACTIVE MODE."
        ),
        followup_block=(followup_text + "\n\n") if followup_text else "\n",
        recent_block=recent_block,
        proactive_block=proactive_block,
        extra_instructions_block=(extra_instructions_block + "\n\n") if extra_instructions_block else "",
        operator_language_contract_block=build_operator_language_contract(operator_language) + "\n\n",
        python_executable=sys.executable,
        send_reply_script=send_reply_script,
        send_target=send_target,
        operator_name=operator_name,
        results_path=results_path,
    )

# ── Main ────────────────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log("NEXO Followup Runner v8 — Pending Runner")

    contract = get_script_runtime_contract("followup-runner")
    if not contract.get("available", True):
        log(f"Runtime blocked: {contract.get('blocked_reason') or 'missing prerequisite'}")
        return

    # Morning agent is now briefing-only (no execution), so no skip needed at 7:00

    if not acquire_lock():
        log("Another instance running. Skipping.")
        return

    state = load_state()
    groups = get_all_active_followups(state)
    all_actionable = list(groups["actionable"])
    cooled = groups.get("cooled_down", [])
    stale_triage = groups.get("stale_triage", [])

    log(f"Actionable: {len(all_actionable)}, Cooled down: {len(cooled)}, "
        f"Needs operator: {len(groups['needs_operator'])}, "
        f"Future: {len(groups['future'])}, Backlog: {len(groups['backlog'])}, "
        f"Stale triage: {len(stale_triage)}")

    for fu in stale_triage[:MAX_STALE_TRIAGE_PER_RUN]:
        fid = str(fu.get("id") or "")
        if not fid:
            continue
        days_overdue = int(fu.get("days_overdue") or 0)
        summary = (
            f"Followup overdue for {days_overdue} days without recent movement. "
            "Operator decision required: close as obsolete, reschedule with reason, or convert into a concrete next action."
        )
        update_followup_fields(
            fid,
            date_value=date.today().isoformat(),
            status="stale_review",
            history_event="stale_triage",
            history_note=summary,
        )
        upsert_attention_reminder(
            fid,
            summary=summary,
            options={"a": "close obsolete", "b": "reschedule", "c": "convert to next action"},
            status="stale_review",
            operator_language=_operator_language(),
        )
        record_attempt(state, fid, "stale_review")

    results = []

    if all_actionable:
        # Clean previous results
        RESULTS_FILE.unlink(missing_ok=True)

        prompt = build_prompt(all_actionable)
        backend = resolve_automation_backend()
        try:
            from client_preferences import resolve_user_model
            _user_model = resolve_user_model()
        except Exception:
            _user_model = ""
        profile = resolve_client_runtime_profile(backend) if backend != "none" else {"model": "", "reasoning_effort": ""}
        profile_label = profile["model"] or _user_model or "default"
        if profile.get("reasoning_effort"):
            profile_label = f"{profile_label}/{profile['reasoning_effort']}"
        log(f"Launching {backend} ({profile_label}) with {len(all_actionable)} followups...")

        env = os.environ.copy()
        env["NEXO_HEADLESS"] = "1"
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE", None)

        try:
            result = run_automation_prompt(
                prompt,
                caller="followup_runner",
                env=env,
                timeout=CLI_TIMEOUT,
                output_format="text",
                allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__*",
            )

            if result.returncode != 0:
                log(f"Automation backend error (exit {result.returncode}): {result.stderr[:500]}")
            else:
                output = result.stdout.strip()
                log(f"Automation backend finished. Output: {len(output)} chars")

                if RESULTS_FILE.exists():
                    try:
                        data = json.loads(RESULTS_FILE.read_text())
                        automation_results = data.get("results", [])
                        results.extend(automation_results)
                        log(f"Parsed {len(automation_results)} automation results")
                    except Exception as e:
                        log(f"Could not parse results: {e}")
                else:
                    log("WARNING: Opus did not write results file")
                    # Save raw output for debugging
                    fallback = LOG_DIR / f"followup-output-{date.today().isoformat()}.txt"
                    fallback.write_text(output)

        except AutomationBackendUnavailableError as e:
            log(f"Automation backend unavailable: {e}")
        except Exception as e:
            log(f"Error: {e}")

    # Update state: complete non-recurrent, advance recurrent, or defer attention
    actionable_by_id = {fu["id"]: fu for fu in all_actionable}
    for r in results:
        fid = r["id"]
        followup_meta = actionable_by_id.get(fid, {})
        recurrence = followup_meta.get("recurrence")
        triage_only = bool(followup_meta.get("triage_only"))
        priority = str(followup_meta.get("priority") or "")
        summary = str(r.get("summary") or "").strip()
        options = r.get("options")

        if r["status"] == "completed" and not recurrence:
            if triage_only:
                log(f"  {fid}: triage resuelto")
            complete_followup_if_needed(fid, summary)
            resolve_attention_reminder(fid, resolution=summary)
            record_attempt(state, fid, "completed")
        elif r["status"] == "checked" and recurrence:
            advance_recurrent(fid, recurrence, summary)
            resolve_attention_reminder(fid, resolution=summary)
            record_attempt(state, fid, "checked")
        elif r["status"] in ("needs_decision", "waiting_user", "stale_review", "blocked"):
            defer_followup_after_attention(
                fid,
                summary=summary,
                options=options,
                status=r["status"],
                priority=priority,
                operator_language=_operator_language(),
            )
            # Cooldown: don't retry for COOLDOWN_DAYS
            record_attempt(state, fid, r["status"])
            log(f"  {fid}: {r['status']} -> cooldown {COOLDOWN_DAYS} days")

    total = len(all_actionable) + len(groups["needs_operator"]) + len(groups["future"]) + len(groups["backlog"]) + len(stale_triage)
    attention_handed_off = any(
        r.get("needs_attention") or r["status"] in ("needs_decision", "waiting_user", "stale_review", "blocked")
        for r in results
    )
    if total > 0 or results:
        if attention_handed_off:
            log("Attention handed off via reminders/orchestrator. Runner direct email path removed.")
        else:
            log("No urgent attention. Runner direct email path removed.")
    else:
        log("No followups at all. Runner direct email path removed.")

    # Save state with attempts + last run
    if "_meta" not in state:
        state["_meta"] = {}
    state["_meta"]["last_run"] = datetime.now().isoformat()
    save_state(state)

    log("Done.")
    log("=" * 60)
    release_lock()


if __name__ == "__main__":
    try:
        main()
    finally:
        release_lock()
