#!/usr/bin/env python3
# nexo: name=email-monitor
# nexo: description=Monitor inbox every 1 minute and wake NEXO for email processing (1 email per session)
# nexo: runtime=python
# nexo: category=email
# nexo: cron_id=email-monitor
# nexo: interval_seconds=60
# nexo: schedule_required=true
# nexo: timeout=1800
# nexo: recovery_policy=run_once_on_wake
# nexo: run_on_boot=false
# nexo: run_on_wake=true
# nexo: idempotent=true
# nexo: max_catchup_age=1200
# nexo: doctor_allow_db=true
"""
NEXO Email Monitor — Thin launcher (CLI-migrated).
Every minute, wakes up NEXO to check and process his email.
NEXO handles EVERYTHING: read, process, reply, track.
"""

import subprocess
import os
import sys
import json
import hashlib
import logging
import re
import sqlite3
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from logging.handlers import RotatingFileHandler

_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
if str(_repo_src) not in sys.path:
    sys.path.insert(0, str(_repo_src))

from automation_controls import (
    format_operator_extra_instructions_block,
    get_operator_profile,
    get_script_runtime_contract,
    get_send_reply_script_path,
)
from paths import brain_dir, nexo_email_dir
from runtime_home import export_resolved_nexo_home

NEXO_HOME = export_resolved_nexo_home()
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
os.environ.setdefault("NEXO_HOME", str(NEXO_HOME))
os.environ["PATH"] = f"{NEXO_HOME / 'bin'}:{os.environ.get('PATH', '')}"
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from client_preferences import (
    resolve_automation_backend,
)
from core_prompts import render_core_prompt

try:
    from nexo_helper import call_tool_text
except ImportError:
    sys.path.insert(0, str(NEXO_HOME / "templates"))
    from nexo_helper import call_tool_text

BASE_DIR = nexo_email_dir()
CONFIG_PATH = BASE_DIR / "config.json"
EMAIL_DB_PATH = BASE_DIR / "nexo-email.db"
LOCK_FILE = BASE_DIR / ".lock"
SESSIONS_FILE = BASE_DIR / ".active-sessions.json"
WORKER_JOBS_DIR = BASE_DIR / "worker-jobs"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"
LOG_FILE = BASE_DIR / "monitor.log"
ALERT_FILE = BASE_DIR / ".consecutive-failures"
EMPTY_BACKOFF_STATE_FILE = BASE_DIR / ".empty-inbox-backoff.json"
ROUTING_RULES_FILE = brain_dir() / "operator-routing-rules.json"
DEBT_SLA_HOURS = 3
DEBT_WAKE_COOLDOWN_HOURS = 24
ZOMBIE_TIMEOUT_HOURS = 2
MAX_AUTOMATION_TIMEOUT_SECONDS = 1800
STALE_EMAIL_SESSION_MINUTES = 45
WORKER_STALE_MAX_MINUTES = 120
CONCURRENT_THRESHOLD_MINUTES = 15
MAX_CONCURRENT_SESSIONS = 2
MAX_EMAIL_ATTEMPTS = 3
DEFAULT_OPERATOR_LANGUAGE = "en"
EMPTY_INBOX_BACKOFF_STEPS = (
    (12, 60 * 60),
    (6, 30 * 60),
    (3, 15 * 60),
)
DEFAULT_ASSISTANT_NAME = "Nova"
EVENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id TEXT NOT NULL,
    event TEXT NOT NULL CHECK(event IN ('opened','processing','ack','replied','commitment','action_done','resolution','debt_flagged')),
    timestamp TEXT DEFAULT (datetime('now','localtime')),
    detail TEXT,
    meta TEXT,
    FOREIGN KEY (email_id) REFERENCES emails(message_id)
);
CREATE INDEX IF NOT EXISTS idx_ee_email ON email_events(email_id);
CREATE INDEX IF NOT EXISTS idx_ee_event ON email_events(event);
CREATE INDEX IF NOT EXISTS idx_ee_ts ON email_events(timestamp);
"""
ACTION_CLOSURE_EVENTS = ("action_done", "resolution")
SENT_REPLY_EVENTS = ("action_done", "resolution", "replied")
EMAIL_LOOP_GUARD_SQL = """
CREATE TABLE IF NOT EXISTS email_loop_guards (
    thread_key TEXT PRIMARY KEY,
    cooldown_until TEXT NOT NULL,
    last_message_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_email_loop_guards_until ON email_loop_guards(cooldown_until);
"""

BASE_DIR.mkdir(parents=True, exist_ok=True)
WORKER_JOBS_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)

# Rotating log: 5MB max, keep 3 backups
handler = RotatingFileHandler(str(LOG_FILE), maxBytes=5*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
log = logging.getLogger("nexo-monitor")
log.setLevel(logging.INFO)
log.addHandler(handler)


# ----------------------------------------------------------------------
# Email checkpoint system
# ----------------------------------------------------------------------
# Each email Nexo processes can take a non-trivial amount of work (drafting
# code, building a presentation, multi-step analysis). When a worker dies
# mid-flight (Brain release, OOM, timeout, manual reboot) the next retry
# previously started from scratch — it had no memory of the partial work the
# previous attempt had already produced. For long replies that meant tokens
# wasted on re-discovery and, occasionally, half-written files left behind in
# the working directory with no narrative context.
#
# The checkpoint helpers below persist a small JSON record per email-thread
# at ``~/.nexo/nexo-email/checkpoints/<sha1(message_id)[:16]>.json`` capturing
# what the previous attempt did so the retry's prompt can include it. The
# checkpoint is best-effort: if reading or writing fails the worker keeps
# running, just without the recovery context.

import hashlib as _hashlib  # alias to keep the public ``hashlib`` import explicit


def _email_checkpoint_path(message_id: str) -> Path:
    """Stable, filesystem-safe path for a given Message-ID.

    Message-IDs contain ``<``, ``>``, ``@`` and other characters that mix
    badly with filesystems, so we hash them. 16 hex chars (~64 bits) is well
    above the collision threshold for the few hundred emails Nexo handles
    per operator, while keeping filenames short enough to skim in a directory
    listing during a debug session.
    """
    # ``usedforsecurity=False`` declares the hash is purely a filename
    # disambiguator (Message-IDs contain ``<``, ``>``, ``@`` that the FS
    # rejects), not a cryptographic primitive. Bandit B324 flags weak
    # algorithms used for security; this annotation tells it this call
    # is safe by intent.
    digest = _hashlib.sha1(  # noqa: S324 - non-security: filename hashing only
        (message_id or "").encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return CHECKPOINTS_DIR / f"{digest}.json"


def _email_checkpoint_read(message_id: str) -> dict | None:
    """Return the checkpoint dict for ``message_id`` if one exists, else None.

    Returns ``None`` (not raise) on any IO/parse failure so the worker can
    treat "no recovery context" as a safe default.
    """
    if not message_id:
        return None
    path = _email_checkpoint_path(message_id)
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(f"Checkpoint read failed for {message_id}: {exc}")
        return None


def _email_checkpoint_write(
    *,
    message_id: str,
    subject: str,
    files_touched: list[str],
    last_assistant_text: str,
    last_error: str,
    attempts: int,
) -> None:
    """Persist a checkpoint atomically (tmp + rename).

    Best-effort: any failure is logged at warning level but never raised so
    the worker keeps progressing.
    """
    if not message_id:
        return
    path = _email_checkpoint_path(message_id)
    existing = _email_checkpoint_read(message_id) or {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    payload = {
        "message_id": message_id,
        "subject": str(subject or "")[:200],
        "first_attempt_at": existing.get("first_attempt_at") or now_iso,
        "last_attempt_at": now_iso,
        "attempts": int(attempts or existing.get("attempts", 0) + 1),
        "files_touched": sorted(set(
            list(existing.get("files_touched") or []) + list(files_touched or [])
        ))[:50],  # cap so a misbehaving run cannot blow up the checkpoint
        "last_assistant_text": str(last_assistant_text or "")[:4000],
        "last_error": str(last_error or "")[:500],
    }
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(path)
    except OSError as exc:
        log.warning(f"Checkpoint write failed for {message_id}: {exc}")


def _email_checkpoint_delete(message_id: str) -> None:
    """Remove the checkpoint when an email succeeds or is escalated."""
    if not message_id:
        return
    try:
        _email_checkpoint_path(message_id).unlink(missing_ok=True)
    except OSError as exc:
        log.warning(f"Checkpoint delete failed for {message_id}: {exc}")


def _email_checkpoint_cleanup(*, max_age_days: int = 7) -> int:
    """Drop checkpoint files older than ``max_age_days``. Idempotent.

    Returns the number of files removed. Called from ``main()`` once per
    monitor tick; on a healthy Mac this is sub-millisecond because the
    directory rarely holds more than a handful of entries.
    """
    if not CHECKPOINTS_DIR.is_dir():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for path in CHECKPOINTS_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log.info(f"Checkpoint cleanup: removed {removed} stale file(s) older than {max_age_days}d")
    return removed


def _scan_files_modified_since(working_dir: str | os.PathLike, since_epoch: float, *, max_files: int = 50) -> list[str]:
    """Return absolute paths in ``working_dir`` whose mtime is newer than
    ``since_epoch``. Used after a worker run to capture the files Nexo
    edited or created during the attempt, so a retry can decide whether to
    pick up where it left off.

    Skips hidden directories, NEXO runtime caches, and Git internals to
    avoid drowning the checkpoint in noise. Caps at ``max_files`` entries
    in case the caller passes a large repository as ``cwd``.
    """
    root = Path(working_dir or "").expanduser()
    if not root.is_dir():
        return []
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".nexo", "Library", "Documents"}
    out: list[str] = []
    try:
        for child in root.rglob("*"):
            try:
                if any(part in skip_dirs for part in child.parts):
                    continue
                if child.is_file() and child.stat().st_mtime > since_epoch:
                    out.append(str(child))
                    if len(out) >= max_files:
                        break
            except OSError:
                continue
    except OSError:
        return []
    return out


def _build_previous_progress_block(message_ids: list[str]) -> str:
    """Build a human-readable section describing progress from prior attempts
    on the given message_ids. Returns an empty string if no checkpoints
    exist, so the prompt builder can append it unconditionally."""
    blocks: list[str] = []
    for mid in message_ids or []:
        cp = _email_checkpoint_read(mid)
        if not cp:
            continue
        subject = cp.get("subject") or "(no subject)"
        attempts = cp.get("attempts") or 1
        files = cp.get("files_touched") or []
        last_text = (cp.get("last_assistant_text") or "").strip()
        last_error = (cp.get("last_error") or "").strip()
        section = [
            f"### Previous attempt on email \"{subject}\"",
            f"- Attempts so far: {attempts}",
        ]
        if files:
            section.append(f"- Files the previous attempt touched (may already contain partial work):")
            for f in files[:20]:
                section.append(f"    - {f}")
        if last_text:
            section.append("- Last narration captured before the previous attempt died:")
            section.append("    " + last_text.replace("\n", "\n    ")[:1500])
        if last_error:
            section.append(f"- Last error: {last_error}")
        section.append(
            "- Decide: continue from where the previous attempt left off (preferred when the partial files are coherent), or start fresh (only if the previous progress is clearly wrong). Either way, do not duplicate work."
        )
        blocks.append("\n".join(section))
    if not blocks:
        return ""
    return "\n\n## Previous attempt context (recovery checkpoint)\n\n" + "\n\n".join(blocks) + "\n"


def _extract_last_assistant_text_from_run(stdout: str) -> str:
    """Best-effort: pull the last assistant-visible text from Claude Code's
    JSON output. Used to give the next attempt's prompt a hint of what the
    dying attempt was thinking. Returns empty string if nothing parseable.
    """
    raw = (stdout or "").strip()
    if not raw or not raw.startswith("{"):
        return raw[:1000]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]
    # Claude Code 1.x: ``{"result": "...text..."}`` is the canonical exit shape.
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()[:4000]
    if isinstance(result, dict):
        # Some configs return structured result; collect strings.
        flat = " ".join(str(v) for v in result.values() if isinstance(v, str))
        if flat.strip():
            return flat.strip()[:4000]
    return raw[:1000]


def operator_routing_context() -> str:
    if not ROUTING_RULES_FILE.exists():
        return "No special routing rules."
    try:
        payload = json.loads(ROUTING_RULES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return "No special routing rules."

    lines = []
    for raw in payload.get("rules", []):
        patterns = [str(pattern).strip().lower() for pattern in raw.get("patterns", []) if str(pattern).strip()]
        if not patterns:
            continue
        owner = str(raw.get("owner") or "external").strip() or "external"
        reason = str(raw.get("reason") or "Do not escalate this to the operator by default.").strip()
        lines.append(f"- {', '.join(patterns)}: owner={owner} — {reason}")
    return "\n".join(lines) if lines else "No special routing rules."


def load_config():
    """Plan F1 — prefer email_accounts table over the legacy JSON.

    Any script updated to v6.4.0+ pulls IMAP/SMTP/password from the
    `email_accounts` table + `credentials` table via email_config.
    Fresh installs without `nexo email setup` fall back to the legacy
    ~/.nexo/nexo-email/config.json so crons keep working until the
    operator migrates.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _src = str(_Path(__file__).resolve().parents[1])
        if _src not in _sys.path:
            _sys.path.insert(0, _src)
        from email_config import load_email_config  # type: ignore
        cfg = load_email_config()
        if cfg:
            return cfg
    except Exception:
        pass
    # v0.32.5 — return None gracefully when no email setup exists yet. Before
    # this, FileNotFoundError bubbled up and the once-per-minute cron generated
    # 1,440 error rows per day per client without configured email. 50 paid
    # clients without email setup meant 72k error rows/day, false watchdog L2
    # alerts, log noise, and possible token burn. The cron now returns without
    # error when config is absent.
    try:
        with open(CONFIG_PATH) as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        payload.pop("automation_task_profile", None)
    return payload


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _format_seconds(seconds):
    seconds = max(0, int(seconds))
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _debt_fingerprint(debt_block):
    text = (debt_block or "").strip()
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def load_empty_inbox_backoff_state():
    default = {
        "empty_runs": 0,
        "next_allowed_wake_at": "",
        "last_interval_seconds": 0,
        "last_debt_fingerprint": "",
        "last_reason": "",
        "updated_at": "",
    }
    if not EMPTY_BACKOFF_STATE_FILE.exists():
        return default
    try:
        payload = json.loads(EMPTY_BACKOFF_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return default

    state = default.copy()
    state.update(payload if isinstance(payload, dict) else {})
    state["empty_runs"] = max(0, _safe_int(state.get("empty_runs"), 0))
    state["last_interval_seconds"] = max(0, _safe_int(state.get("last_interval_seconds"), 0))
    state["next_allowed_wake_at"] = str(state.get("next_allowed_wake_at") or "")
    state["last_debt_fingerprint"] = str(state.get("last_debt_fingerprint") or "")
    state["last_reason"] = str(state.get("last_reason") or "")
    state["updated_at"] = str(state.get("updated_at") or "")
    return state


def save_empty_inbox_backoff_state(state):
    payload = {
        "empty_runs": max(0, _safe_int(state.get("empty_runs"), 0)),
        "next_allowed_wake_at": str(state.get("next_allowed_wake_at") or ""),
        "last_interval_seconds": max(0, _safe_int(state.get("last_interval_seconds"), 0)),
        "last_debt_fingerprint": str(state.get("last_debt_fingerprint") or ""),
        "last_reason": str(state.get("last_reason") or ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    EMPTY_BACKOFF_STATE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))


def compute_empty_inbox_interval(base_interval_seconds, empty_runs):
    interval = max(60, _safe_int(base_interval_seconds, 300))
    for threshold, candidate in EMPTY_INBOX_BACKOFF_STEPS:
        if empty_runs >= threshold:
            return max(interval, candidate)
    return interval


def reset_empty_inbox_backoff_state(reason):
    state = {
        "empty_runs": 0,
        "next_allowed_wake_at": "",
        "last_interval_seconds": 0,
        "last_debt_fingerprint": "",
        "last_reason": reason,
    }
    save_empty_inbox_backoff_state(state)


def update_empty_inbox_backoff_state(*, state, base_interval_seconds, debt_block):
    now = datetime.now()
    debt_fingerprint = _debt_fingerprint(debt_block)
    empty_runs = max(0, _safe_int(state.get("empty_runs"), 0)) + 1
    interval_seconds = compute_empty_inbox_interval(base_interval_seconds, empty_runs)
    previous_due_at = _parse_iso_datetime(state.get("next_allowed_wake_at"))
    debt_changed = bool(debt_fingerprint) and debt_fingerprint != state.get("last_debt_fingerprint", "")
    should_suppress_debt_wake = bool(
        debt_fingerprint and previous_due_at and now < previous_due_at and not debt_changed
    )
    next_allowed_wake_at = (now + timedelta(seconds=interval_seconds)).isoformat(timespec="seconds")

    updated_state = {
        "empty_runs": empty_runs,
        "next_allowed_wake_at": next_allowed_wake_at,
        "last_interval_seconds": interval_seconds,
        "last_debt_fingerprint": debt_fingerprint,
        "last_reason": "debt_changed" if debt_changed else ("debt" if debt_fingerprint else "empty"),
    }
    save_empty_inbox_backoff_state(updated_state)
    return {
        "empty_runs": empty_runs,
        "interval_seconds": interval_seconds,
        "interval_label": _format_seconds(interval_seconds),
        "next_allowed_wake_at": next_allowed_wake_at,
        "debt_changed": debt_changed,
        "should_suppress_debt_wake": should_suppress_debt_wake,
    }


def ensure_email_events_table(conn):
    conn.executescript(EVENT_TABLE_SQL)


def ensure_email_loop_guard_table(conn):
    conn.executescript(EMAIL_LOOP_GUARD_SQL)


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _insert_event(conn, email_id, event, detail="", meta=None):
    conn.execute(
        "INSERT INTO email_events (email_id, event, detail, meta) VALUES (?, ?, ?, ?)",
        (email_id, event, detail, json.dumps(meta or {}, ensure_ascii=True, sort_keys=True)),
    )


def _row_value(row, key, default=""):
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _normalize_email_addr(value):
    parsed = parseaddr(str(value or ""))[1] or str(value or "")
    return parsed.strip().lower()


def _normalize_subject_for_thread(value):
    text = " ".join(str(value or "").lower().split())
    while True:
        new_text = re.sub(r"^(re|fw|fwd)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        if new_text == text:
            break
        text = new_text
    return text[:300]


def _email_thread_key(row):
    thread_id = str(_row_value(row, "thread_id", "") or "").strip()
    in_reply_to = str(_row_value(row, "in_reply_to", "") or "").strip()
    subject = _normalize_subject_for_thread(_row_value(row, "subject", ""))
    seed = thread_id or in_reply_to or subject or str(_row_value(row, "message_id", "") or "")
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest


def _is_agent_self_sender(row, config, priority_aliases=None):
    sender = _normalize_email_addr(_row_value(row, "from_addr", ""))
    if not sender:
        return False
    aliases = {_normalize_email_addr(item) for item in (priority_aliases or []) if _normalize_email_addr(item)}
    if sender in aliases:
        return False
    configured = _normalize_email_addr((config or {}).get("email", ""))
    return bool(configured and sender == configured)


def _loop_cooldown_active(conn, thread_key):
    row = conn.execute(
        """
        SELECT cooldown_until
        FROM email_loop_guards
        WHERE thread_key = ?
          AND datetime(replace(cooldown_until, 'T', ' ')) > datetime('now','localtime')
        LIMIT 1
        """,
        (thread_key,),
    ).fetchone()
    return bool(row)


def _recent_thread_rows(conn, thread_key, *, limit=12):
    rows = conn.execute(
        """
        SELECT message_id, subject, from_addr, status, thread_id, in_reply_to, received_at
        FROM emails
        ORDER BY datetime(COALESCE(NULLIF(received_at, ''), '1970-01-01 00:00:00')) DESC, rowid DESC
        LIMIT 80
        """
    ).fetchall()
    matched = []
    for row in rows:
        if _email_thread_key(row) == thread_key:
            matched.append(row)
        if len(matched) >= limit:
            break
    return matched


def _agent_thread_streak(conn, thread_key, config, priority_aliases=None):
    streak = 0
    for row in _recent_thread_rows(conn, thread_key):
        if _is_agent_self_sender(row, config, priority_aliases=priority_aliases):
            streak += 1
            continue
        break
    return streak


def _mark_loop_blocked(conn, row, *, thread_key, reason):
    mid = str(_row_value(row, "message_id", "") or "")
    cooldown_until = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
    plain_detail = "Paused for manual review: repeated internal email activity in this thread."
    conn.execute(
        """
        UPDATE emails
        SET status = 'needs_interactive', error = ?
        WHERE message_id = ?
        """,
        (plain_detail, mid),
    )
    _insert_event(
        conn,
        mid,
        "debt_flagged",
        plain_detail,
        {"guard": "self_thread_cooldown", "thread_key": thread_key, "reason": reason},
    )
    conn.execute(
        """
        INSERT INTO email_loop_guards (thread_key, cooldown_until, last_message_id, reason, updated_at)
        VALUES (?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(thread_key) DO UPDATE SET
            cooldown_until = excluded.cooldown_until,
            last_message_id = excluded.last_message_id,
            reason = excluded.reason,
            updated_at = excluded.updated_at
        """,
        (thread_key, cooldown_until, mid, reason),
    )
    conn.commit()
    log.warning("Paused email thread for manual review: %s (%s)", mid, reason)


def _email_loop_guard_blocks(conn, row, *, config=None, priority_aliases=None):
    if not config:
        return False
    thread_key = _email_thread_key(row)
    if not thread_key:
        return False
    if _loop_cooldown_active(conn, thread_key):
        _mark_loop_blocked(conn, row, thread_key=thread_key, reason="thread_cooldown_active")
        return True
    if _is_agent_self_sender(row, config, priority_aliases=priority_aliases):
        _mark_loop_blocked(conn, row, thread_key=thread_key, reason="agent_self_sender")
        return True
    if _agent_thread_streak(conn, thread_key, config, priority_aliases=priority_aliases) >= 3:
        _mark_loop_blocked(conn, row, thread_key=thread_key, reason="agent_thread_streak")
        return True
    return False


def _normalize_sqlite_ts(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ")
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _reconcile_processing_rows(conn):
    rows = conn.execute(
        """
        SELECT
            e.message_id,
            e.subject,
            e.started_at,
            e.received_at,
            (
                SELECT MAX(ev.timestamp)
                FROM email_events ev
                WHERE ev.email_id = e.message_id
                  AND ev.event = 'processing'
            ) AS processing_ts,
            (
                SELECT MAX(ev.timestamp)
                FROM email_events ev
                WHERE ev.email_id = e.message_id
                  AND ev.event = 'opened'
            ) AS opened_ts
        FROM emails e
        WHERE e.status = 'processing'
        """
    ).fetchall()

    reconciled = []
    for row in rows:
        started_at = _normalize_sqlite_ts(row["started_at"])
        processing_ts = _normalize_sqlite_ts(row["processing_ts"])
        opened_ts = _normalize_sqlite_ts(row["opened_ts"])
        received_at = _normalize_sqlite_ts(row["received_at"])
        anchor_ts = started_at or processing_ts or opened_ts or received_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not started_at:
            conn.execute(
                """
                UPDATE emails
                SET started_at = ?
                WHERE message_id = ?
                  AND status = 'processing'
                """,
                (anchor_ts, row["message_id"]),
            )

        if not processing_ts:
            _insert_event(
                conn,
                row["message_id"],
                "processing",
                "Reconciled processing marker from emails.status=processing",
                {
                    "reconciled": True,
                    "anchor_ts": anchor_ts,
                    "opened_ts": opened_ts,
                    "received_at": received_at,
                },
            )

        if not started_at or not processing_ts:
            reconciled.append(
                {
                    "email_id": row["message_id"],
                    "subject": row["subject"],
                    "anchor_ts": anchor_ts,
                    "filled_started_at": not started_at,
                    "inserted_processing_event": not processing_ts,
                }
            )

    return reconciled


def _reconcile_finished_rows(conn, *, hours=24):
    rows = conn.execute(
        """
        SELECT
            e.message_id,
            e.subject,
            e.started_at,
            e.received_at,
            e.completed_at,
            (
                SELECT MAX(ev.timestamp)
                FROM email_events ev
                WHERE ev.email_id = e.message_id
                  AND ev.event = 'processing'
            ) AS processing_ts,
            (
                SELECT MAX(ev.timestamp)
                FROM email_events ev
                WHERE ev.email_id = e.message_id
                  AND ev.event = 'opened'
            ) AS opened_ts
        FROM emails e
        WHERE e.status = 'processed'
          AND e.completed_at IS NOT NULL
          AND datetime(replace(e.completed_at, 'T', ' ')) >= datetime('now','localtime', ?)
          AND (
                e.started_at IS NULL
                OR trim(e.started_at) = ''
                OR NOT EXISTS (
                    SELECT 1
                    FROM email_events ev2
                    WHERE ev2.email_id = e.message_id
                      AND ev2.event = 'processing'
                )
          )
        """,
        (f"-{hours} hours",),
    ).fetchall()

    reconciled = []
    for row in rows:
        processing_ts = _normalize_sqlite_ts(row["processing_ts"])
        opened_ts = _normalize_sqlite_ts(row["opened_ts"])
        received_at = _normalize_sqlite_ts(row["received_at"])
        anchor_ts = processing_ts or opened_ts or received_at
        if not anchor_ts:
            continue

        conn.execute(
            """
            UPDATE emails
            SET started_at = ?
            WHERE message_id = ?
              AND status = 'processed'
              AND (started_at IS NULL OR trim(started_at) = '')
            """,
            (anchor_ts, row["message_id"]),
        )

        if not processing_ts:
            _insert_event(
                conn,
                row["message_id"],
                "processing",
                "Reconciled processing marker from processed email missing event",
                {
                    "reconciled": True,
                    "anchor_ts": anchor_ts,
                    "opened_ts": opened_ts,
                    "received_at": received_at,
                    "completed_at": _normalize_sqlite_ts(row["completed_at"]),
                },
            )

        reconciled.append(
            {
                "email_id": row["message_id"],
                "subject": row["subject"],
                "anchor_ts": anchor_ts,
                "inserted_processing_event": not processing_ts,
            }
        )

    return reconciled


def _recent_debt_flagged(conn, email_id, *, hours=6):
    row = conn.execute(
        """
        SELECT 1
        FROM email_events
        WHERE email_id = ?
          AND event = 'debt_flagged'
          AND timestamp >= datetime('now','localtime', ?)
        LIMIT 1
        """,
        (email_id, f"-{hours} hours"),
    ).fetchone()
    return bool(row)


def _debt_suppressed_recently(conn, email_id):
    return _recent_debt_flagged(conn, email_id, hours=DEBT_WAKE_COOLDOWN_HOURS)


def _has_active_processing_within(conn, email_id, *, hours=ZOMBIE_TIMEOUT_HOURS):
    row = conn.execute(
        """
        SELECT 1
        FROM emails
        WHERE message_id = ?
          AND status = 'processing'
          AND started_at IS NOT NULL
          AND datetime(replace(started_at, 'T', ' ')) >= datetime('now','localtime', ?)
        LIMIT 1
        """,
        (email_id, f"-{hours} hours"),
    ).fetchone()
    return bool(row)


def _has_action_done_after(conn, email_id, reference_ts):
    return _has_email_event_after(conn, email_id, ACTION_CLOSURE_EVENTS, reference_ts)


def _has_email_event_after(conn, email_id, events, reference_ts):
    if not email_id or not reference_ts:
        return False
    event_list = tuple(events or ())
    if not event_list:
        return False
    placeholders = ",".join("?" for _ in event_list)
    row = conn.execute(
        f"""
        SELECT 1
        FROM email_events
        WHERE email_id = ?
          AND event IN ({placeholders})
          AND datetime(replace(timestamp, 'T', ' ')) > datetime(replace(?, 'T', ' '))
        LIMIT 1
        """,
        (email_id, *event_list, reference_ts),
    ).fetchone()
    return bool(row)


def _has_sent_reply_event(conn, email_id):
    if not email_id:
        return False
    placeholders = ",".join("?" for _ in SENT_REPLY_EVENTS)
    row = conn.execute(
        f"""
        SELECT 1
        FROM email_events
        WHERE email_id = ?
          AND event IN ({placeholders})
        LIMIT 1
        """,
        (email_id, *SENT_REPLY_EVENTS),
    ).fetchone()
    return bool(row)


def _mark_processing_email_processed(conn, email_id, *, completed_at):
    cols = _email_table_columns(conn)
    updates = [
        "status = 'processed'",
        "started_at = NULL",
    ]
    params = []
    if "completed_at" in cols:
        updates.append(
            "completed_at = CASE WHEN completed_at IS NULL OR trim(completed_at) = '' THEN ? ELSE completed_at END"
        )
        params.append(completed_at)
    if "error" in cols:
        updates.append("error = NULL")
    params.append(email_id)
    cur = conn.execute(
        f"""
        UPDATE emails
        SET {', '.join(updates)}
        WHERE message_id = ?
          AND status = 'processing'
        """,
        tuple(params),
    )
    return cur.rowcount > 0


def scan_debt(db_path=EMAIL_DB_PATH, *, max_items=5):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_email_events_table(conn)
    if not _table_exists(conn, "emails"):
        log.warning("Debt scan skipped: email DB missing 'emails' table")
        conn.close()
        return ""
    live_reconciled = _reconcile_processing_rows(conn)
    finished_reconciled = _reconcile_finished_rows(conn)

    items = []
    now_label = datetime.now().isoformat(timespec="seconds")

    ack_rows = conn.execute(
        f"""
        SELECT e.email_id, m.subject, MAX(e.timestamp) AS last_ack_ts
        FROM email_events e
        LEFT JOIN emails m ON m.message_id = e.email_id
        WHERE e.event = 'ack'
        GROUP BY e.email_id
        HAVING last_ack_ts < datetime('now','localtime','-{DEBT_SLA_HOURS} hours')
        ORDER BY last_ack_ts ASC
        """
    ).fetchall()
    for row in ack_rows:
        if _has_active_processing_within(conn, row["email_id"]):
            continue
        if _has_action_done_after(conn, row["email_id"], row["last_ack_ts"]):
            continue
        if _debt_suppressed_recently(conn, row["email_id"]):
            continue
        items.append(
            {
                "email_id": row["email_id"],
                "kind": "ack",
                "label": f"ACK without closure >{DEBT_SLA_HOURS}h — {row['subject'] or row['email_id']} [{row['email_id']}]",
                "detail": f"ack since {row['last_ack_ts']}",
            }
        )

    commitment_rows = conn.execute(
        f"""
        SELECT e.email_id, m.subject, MAX(e.timestamp) AS last_commitment_ts
        FROM email_events e
        LEFT JOIN emails m ON m.message_id = e.email_id
        WHERE e.event = 'commitment'
        GROUP BY e.email_id
        HAVING last_commitment_ts < datetime('now','localtime','-{DEBT_SLA_HOURS} hours')
        ORDER BY last_commitment_ts ASC
        """
    ).fetchall()
    for row in commitment_rows:
        if _has_active_processing_within(conn, row["email_id"]):
            continue
        if _has_action_done_after(conn, row["email_id"], row["last_commitment_ts"]):
            continue
        if _debt_suppressed_recently(conn, row["email_id"]):
            continue
        items.append(
            {
                "email_id": row["email_id"],
                "kind": "commitment",
                "label": f"COMMITMENT without closure >{DEBT_SLA_HOURS}h — {row['subject'] or row['email_id']} [{row['email_id']}]",
                "detail": f"commitment since {row['last_commitment_ts']}",
            }
        )

    stuck_rows = conn.execute(
        f"""
        SELECT message_id, subject, started_at, received_at
        FROM emails
        WHERE status = 'processing'
          AND COALESCE(
                datetime(replace(started_at, 'T', ' ')),
                datetime(replace(received_at, 'T', ' ')),
                datetime('now','localtime')
              ) < datetime('now','localtime','-{ZOMBIE_TIMEOUT_HOURS} hours')
        ORDER BY COALESCE(started_at, received_at, '') ASC
        """
    ).fetchall()
    recovered = []
    sent_reconciled = []
    for row in stuck_rows:
        if _has_sent_reply_event(conn, row["message_id"]):
            if _mark_processing_email_processed(conn, row["message_id"], completed_at=now_label):
                sent_reconciled.append(row)
            continue
        conn.execute(
            """
            UPDATE emails
            SET status = 'pending',
                started_at = NULL
            WHERE message_id = ?
            """,
            (row["message_id"],),
        )
        _insert_event(
            conn,
            row["message_id"],
            "debt_flagged",
            f"Auto-recovered stuck processing email at {now_label}",
            {
                "reason": "processing_timeout",
                "previous_started_at": row["started_at"],
                "previous_received_at": row["received_at"],
            },
        )
        recovered.append(row)
        items.append(
            {
                "email_id": row["message_id"],
                "kind": "processing",
                "label": f"PROCESSING zombie >{ZOMBIE_TIMEOUT_HOURS}h -> reseteado a pending — {row['subject'] or row['message_id']} [{row['message_id']}]",
                "detail": f"started_at={row['started_at'] or 'NULL'}",
            }
        )

    for item in items:
        if item["kind"] in {"ack", "commitment"}:
            _insert_event(
                conn,
                item["email_id"],
                "debt_flagged",
                item["detail"],
                {"reason": item["kind"], "detected_at": now_label},
            )

    conn.commit()
    conn.close()

    if not items:
        return ""

    lines = ["== PENDING EMAIL DEBT DETECTED ==", "Prioritize closing or clarifying these threads before ignoring them:"]
    for item in items[:max_items]:
        lines.append(f"- {item['label']} ({item['detail']})")
    if len(items) > max_items:
        lines.append(f"- ... and {len(items) - max_items} more item(s)")
    if recovered:
        lines.append("")
        lines.append(f"Auto-recovery applied: {len(recovered)} processing-stuck email(s) were reset to pending.")
    if sent_reconciled:
        lines.append("")
        lines.append(
            f"Reconciled {len(sent_reconciled)} processing email(s) with already-sent reply events; no re-open applied."
        )
    total_reconciled = len(live_reconciled) + len(finished_reconciled)
    if total_reconciled:
        lines.append(f"Reconciled {total_reconciled} email(s) with inconsistent lifecycle state.")
    return "\n".join(lines)


def read_recent_hot_context(*, query: str = "", hours: int = 24, limit: int = 8) -> str:
    payload = {"hours": hours, "limit": limit}
    if query.strip():
        payload["query"] = query.strip()
    try:
        text = call_tool_text("nexo_pre_action_context", payload).strip()
    except Exception as exc:
        log.warning(f"nexo_pre_action_context failed: {exc}")
        return "Failed to preload hot context."
    return text or "No recent hot context found."


def acquire_lock():
    if LOCK_FILE.exists():
        try:
            content = LOCK_FILE.read_text().strip()
            lines = content.split('\n')
            pid = int(lines[0])
            os.kill(pid, 0)
            if len(lines) > 1:
                started_dt = datetime.fromisoformat(lines[1])
                if datetime.now() - started_dt > timedelta(minutes=STALE_EMAIL_SESSION_MINUTES):
                    log.warning(
                        f"Stale email lock from PID {pid} (>{STALE_EMAIL_SESSION_MINUTES}m). "
                        "Terminating stale email automation process group."
                    )
                    try:
                        pgid = os.getpgid(pid)
                        os.killpg(pgid, signal.SIGTERM)
                        time.sleep(2)
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            pass
                        else:
                            os.killpg(pgid, signal.SIGKILL)
                    except Exception as exc:
                        log.warning(f"Failed to terminate stale email process group cleanly: {exc}")
                    LOCK_FILE.unlink()
                else:
                    return False
            else:
                return False
        except (ProcessLookupError, ValueError, PermissionError):
            log.info("Removing stale lock (dead process)")
            LOCK_FILE.unlink()

    LOCK_FILE.write_text(f"{os.getpid()}\n{datetime.now().isoformat()}")
    return True


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def has_new_email(config):
    """Quick IMAP check — are there unseen emails?
    Returns count or -1 on error."""
    try:
        import imaplib
        mail = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
        mail.login(config["email"], config["password"])
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        mail.logout()
        if not data[0]:
            return 0
        return len(data[0].split())
    except Exception as e:
        log.error(f"IMAP check failed: {e}")
        return -1


def count_stuck_emails():
    """Check DB for emails that were seen by IMAP but never fully processed.
    These are emails stuck in 'new', 'pending', or 'processing' (< zombie threshold)
    that would otherwise be invisible because IMAP already marked them SEEN.
    Returns count of stuck emails, or 0 if DB is unavailable."""
    db_path = BASE_DIR / "nexo-email.db"
    if not db_path.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Get column names to handle both schema versions
        cols = [r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()]
        status_col = "status"
        if status_col not in cols:
            conn.close()
            return 0
        # Count emails not in terminal state
        stuck_statuses = ("new", "pending", "processing", "opened", "error")
        placeholders = ",".join("?" for _ in stuck_statuses)
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM emails WHERE {status_col} IN ({placeholders})",
            stuck_statuses,
        ).fetchone()
        count = row["cnt"] if row else 0
        conn.close()
        if count > 0:
            log.info(f"Found {count} stuck email(s) in DB (not yet processed/skipped)")
        return count
    except Exception as e:
        log.warning(f"Stuck email check failed: {e}")
        return 0


# === Email-loss prevention (2026-04-14): pre-register + orphan recovery ===
# Problem: if headless NEXO dies before marking email in BD, IMAP may have it
# marked SEEN (from an IMAP full-message read) but BD has no row. count_stuck_emails
# cannot see it, and has_new_email returns 0. The email is lost.
# Fix:
#   1. Pre-register: INSERT status='pending' rows BEFORE launching NEXO, using
#      BODY.PEEK so the monitor itself never marks SEEN.
#   2. Reconcile: scan IMAP SEEN last 24h, compare to BD. If a SEEN email has
#      no row or only status in {pending,processing,new,opened,error}, mark
#      it UNSEEN again so next pass picks it up.

TERMINAL_EMAIL_STATUSES = ("processed", "completed", "responded", "skipped", "sent")
SKIP_REPLY_SENDERS = (
    "dmarc", "noreply", "no-reply", "postmaster", "mailer-daemon",
    "notifications@github.com", "auto-confirm", "digest@",
)
SKIP_REPLY_SUBJECTS = (
    "report domain:", "[preview] report domain:", "dmarc report",
)


def _decode_header(raw):
    if raw is None:
        return ""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _parse_email_headers(raw_bytes):
    """Parse minimal headers from RFC822 header bytes. Returns dict with
    message_id, from_addr, from_name, subject, received_at, thread_id,
    in_reply_to. Empty strings on failure.

    Q-encoded headers (utf-8 / quoted-printable) come back as
    `email.header.Header` instances rather than plain strings. Plain
    `str(Header)` returns the still-encoded `=?utf-8?q?...?=` form, and
    `Header` itself does not support `.strip()` / `in` — both of which
    used to drop the email silently in production. Every `msg.get(...)`
    therefore goes through `_decode_header`, which decodes Q-encoding
    AND coerces to a real `str`."""
    try:
        import email as _email
        msg = _email.message_from_bytes(raw_bytes)
        from_raw = _decode_header(msg.get("From", ""))
        name = ""
        addr = from_raw
        if "<" in from_raw and ">" in from_raw:
            name = from_raw.split("<")[0].strip().strip('"')
            addr = from_raw.split("<")[1].split(">")[0].strip()
        references_raw = _decode_header(msg.get("References", ""))
        in_reply_to_raw = _decode_header(msg.get("In-Reply-To", ""))
        thread_seed = (references_raw or in_reply_to_raw).strip()
        thread_id = thread_seed.split()[-1] if thread_seed else ""
        return {
            "message_id": _decode_header(msg.get("Message-ID", "")).strip(),
            "from_addr": addr.strip().lower(),
            "from_name": name,
            "subject": _decode_header(msg.get("Subject", "")),
            "received_at": _decode_header(msg.get("Date", "")).strip(),
            "in_reply_to": in_reply_to_raw.strip(),
            "thread_id": thread_id,
        }
    except Exception as e:
        log.warning(f"Header parse failed: {e}")
        return {}


def _ensure_emails_table(conn):
    """Create emails table if missing — matches schema used by the headless."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emails (
            message_id TEXT PRIMARY KEY,
            from_addr TEXT,
            from_name TEXT,
            subject TEXT,
            body TEXT,
            received_at TEXT,
            status TEXT DEFAULT 'pending',
            started_at TEXT,
            completed_at TEXT,
            response TEXT,
            error TEXT,
            retries INTEGER DEFAULT 0,
            thread_id TEXT,
            in_reply_to TEXT,
            attempts INTEGER DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thread ON emails(thread_id)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()}
    if "attempts" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN attempts INTEGER DEFAULT 0")
    if "error" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN error TEXT")
    if "escalation_notified_at" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN escalation_notified_at TEXT")


def _email_table_columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()}


# --- Active sessions tracking (per-email concurrency) ---

def _load_active_sessions():
    if not SESSIONS_FILE.exists():
        return []
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save_active_sessions(sessions):
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))


def _session_emails_are_terminal(conn, email_ids):
    """True when every tracked email exists in DB and is already terminal."""
    tracked_ids = [mid for mid in (email_ids or []) if mid]
    if not tracked_ids:
        return False

    placeholders = ",".join("?" for _ in tracked_ids)
    release_statuses = set(TERMINAL_EMAIL_STATUSES) | {"needs_interactive"}
    rows = conn.execute(
        f"""
        SELECT message_id, status
        FROM emails
        WHERE message_id IN ({placeholders})
        """,
        tracked_ids,
    ).fetchall()
    status_by_id = {row[0]: str(row[1] or "").strip().lower() for row in rows}
    if len(status_by_id) != len(set(tracked_ids)):
        return False
    return all(status_by_id.get(mid) in release_statuses for mid in tracked_ids)


def _terminate_session_process(pid, email_ids):
    """Best-effort termination of a stale worker session and its children."""
    try:
        os.killpg(int(pid), signal.SIGTERM)
        log.info(f"Terminated stale session PID {pid} for emails: {email_ids}")
    except ProcessLookupError:
        log.info(f"Stale session PID {pid} already exited for emails: {email_ids}")
    except Exception as exc:
        log.warning(f"Failed to terminate stale session PID {pid} for emails {email_ids}: {exc}")


def _prune_dead_sessions():
    """Remove dead sessions and stale workers whose tracked emails already finished."""
    sessions = _load_active_sessions()
    alive = []
    conn = None
    try:
        if EMAIL_DB_PATH.exists():
            conn = sqlite3.connect(str(EMAIL_DB_PATH))
    except Exception as exc:
        log.warning(f"Failed to open email DB while pruning sessions: {exc}")
        conn = None
    for s in sessions:
        pid = int(s.get("pid", -1))
        email_ids = s.get("email_ids", [])
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            log.info(f"Pruned dead session PID {pid} for emails: {email_ids}")
            continue

        if conn is not None and _session_emails_are_terminal(conn, email_ids):
            log.info(f"Pruned terminal session PID {pid} for emails: {email_ids}")
            _terminate_session_process(pid, email_ids)
            continue

        started_raw = s.get("started_at", "")
        try:
            started_dt = datetime.fromisoformat(started_raw) if started_raw else None
        except ValueError:
            started_dt = None
        if started_dt is not None and datetime.now() - started_dt > timedelta(minutes=WORKER_STALE_MAX_MINUTES):
            age_min = int((datetime.now() - started_dt).total_seconds() // 60)
            log.warning(
                f"Pruned stale session PID {pid} (age {age_min}m > {WORKER_STALE_MAX_MINUTES}m) "
                f"for emails: {email_ids}"
            )
            _terminate_session_process(pid, email_ids)
            continue

        alive.append(s)

    if conn is not None:
        conn.close()
    if len(alive) != len(sessions):
        _save_active_sessions(alive)
    return alive


def _register_session(pid, email_ids):
    sessions = _prune_dead_sessions()
    sessions.append({
        "pid": pid,
        "email_ids": email_ids,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_active_sessions(sessions)


def _unregister_session(pid=None):
    target_pid = int(pid or os.getpid())
    sessions = _load_active_sessions()
    remaining = [s for s in sessions if int(s.get("pid", -1)) != target_pid]
    if len(remaining) != len(sessions):
        _save_active_sessions(remaining)


def _active_session_count():
    return len(_prune_dead_sessions())


def _emails_in_active_sessions():
    """Return set of email message_ids being processed by live sessions."""
    sessions = _prune_dead_sessions()
    ids = set()
    for s in sessions:
        ids.update(s.get("email_ids", []))
    return ids


def get_actionable_emails(conn, *, priority_aliases=None, config=None):
    """Get emails ready to process: pending/stuck, not in an active session,
    and under MAX_EMAIL_ATTEMPTS. Returns list of dicts."""
    ensure_email_events_table(conn)
    ensure_email_loop_guard_table(conn)
    active_ids = _emails_in_active_sessions()
    aliases = [str(item or "").strip().lower() for item in (priority_aliases or []) if str(item or "").strip()]
    params = [MAX_EMAIL_ATTEMPTS]
    order_by = "received_at ASC"
    if aliases:
        placeholders = ", ".join("?" for _ in aliases)
        order_by = (
            "CASE WHEN lower(COALESCE(from_addr, '')) IN (" + placeholders + ") "
            "THEN 0 ELSE 1 END, received_at ASC"
        )
        params.extend(aliases)
    rows = conn.execute(
        f"""
        SELECT message_id, subject, from_addr, status, attempts,
               started_at, received_at
        FROM emails
        WHERE status IN ('pending', 'stuck', 'new', 'error')
          AND COALESCE(attempts, 0) < ?
        ORDER BY {order_by}
        """,
        tuple(params),
    ).fetchall()
    actionable = []
    for row in rows:
        mid = row["message_id"]
        if mid in active_ids:
            continue
        if _email_loop_guard_blocks(conn, row, config=config, priority_aliases=priority_aliases):
            continue
        actionable.append(dict(row))
    return actionable


def has_long_running_session():
    """Check if any active session has been running > CONCURRENT_THRESHOLD_MINUTES."""
    sessions = _prune_dead_sessions()
    threshold = datetime.now() - timedelta(minutes=CONCURRENT_THRESHOLD_MINUTES)
    for s in sessions:
        started = _parse_iso_datetime(s.get("started_at"))
        if started and started < threshold:
            return True
    return False


def preregister_pending_emails(config):
    """Fetch UNSEEN headers with BODY.PEEK and INSERT OR IGNORE them as
    status='pending' in BD. Does NOT mark SEEN in IMAP.
    Returns count of rows inserted (new pending rows)."""
    try:
        import imaplib
        mail = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
        mail.login(config["email"], config["password"])
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        if not data or not data[0]:
            mail.logout()
            return 0
        uids = data[0].split()
        if not uids:
            mail.logout()
            return 0

        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        _ensure_emails_table(conn)
        inserted = 0
        for uid in uids:
            try:
                # BODY.PEEK keeps IMAP \Seen flag untouched
                _, fetched = mail.fetch(uid, "(BODY.PEEK[HEADER])")
                if not fetched or not fetched[0]:
                    continue
                raw = fetched[0][1] if isinstance(fetched[0], tuple) else b""
                headers = _parse_email_headers(raw)
                msg_id = headers.get("message_id", "")
                if not msg_id:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO emails
                      (message_id, from_addr, from_name, subject, received_at,
                       status, thread_id, in_reply_to)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        msg_id,
                        headers.get("from_addr", ""),
                        headers.get("from_name", ""),
                        headers.get("subject", ""),
                        headers.get("received_at", ""),
                        headers.get("thread_id", ""),
                        headers.get("in_reply_to", ""),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception as e:
                log.debug(f"Preregister UID {uid!r} failed: {e}")
        conn.commit()
        conn.close()
        mail.logout()
        if inserted:
            log.info(f"Pre-registered {inserted} UNSEEN email(s) as status='pending' in BD")
        return inserted
    except Exception as e:
        log.warning(f"preregister_pending_emails failed: {e}")
        return 0


def reconcile_orphaned_seen(config, hours=24):
    """Scan IMAP SEEN from last N hours; any whose Message-ID is absent from
    BD — or present but NOT in terminal state — gets un-flagged (\\Seen removed)
    so the next monitor pass picks it up. Uses BODY.PEEK, never marks SEEN.
    Returns count of emails recovered (marked UNSEEN)."""
    try:
        import imaplib
        mail = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
        mail.login(config["email"], config["password"])
        mail.select("INBOX")
        since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(SEEN SINCE {since})')
        if not data or not data[0]:
            mail.logout()
            return 0
        uids = data[0].split()
        if not uids:
            mail.logout()
            return 0

        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        conn.row_factory = sqlite3.Row
        _ensure_emails_table(conn)

        recovered = 0
        for uid in uids:
            try:
                _, fetched = mail.fetch(uid, "(BODY.PEEK[HEADER])")
                if not fetched or not fetched[0]:
                    continue
                raw = fetched[0][1] if isinstance(fetched[0], tuple) else b""
                headers = _parse_email_headers(raw)
                msg_id = headers.get("message_id", "")
                if not msg_id:
                    continue
                row = conn.execute(
                    "SELECT status FROM emails WHERE message_id = ?", (msg_id,)
                ).fetchone()
                # Ownership filter: the daemon only pre-registers emails IT
                # intends to handle. If message_id is ABSENT from BD, this
                # email was read by the operator in another mail client and must NOT be
                # touched. Only emails that the daemon claimed (row exists)
                # AND never reached a terminal state are true orphans.
                if not row:
                    continue
                status = row["status"]
                if status in TERMINAL_EMAIL_STATUSES:
                    continue
                # Orphan: claimed by daemon (has row) but stuck in
                # non-terminal state. Remove \Seen so next pass retries.
                mail.store(uid, "-FLAGS", "\\Seen")
                recovered += 1
                log.info(
                    f"Reconciled orphan SEEN email back to UNSEEN: UID={uid.decode() if isinstance(uid, bytes) else uid} "
                    f"msg_id={msg_id[:40]} db_status={status}"
                )
            except Exception as e:
                log.debug(f"Reconcile UID {uid!r} failed: {e}")
        conn.close()
        mail.logout()
        if recovered:
            log.info(f"Recovered {recovered} orphan email(s) (SEEN→UNSEEN, last {hours}h)")
        return recovered
    except Exception as e:
        log.warning(f"reconcile_orphaned_seen failed: {e}")
        return 0


def reconcile_terminal_unseen(config, hours=48):
    """Scan IMAP UNSEEN from last N hours; any whose Message-ID is in BD with
    a terminal status (processed/replied/skipped/resolved/needs_interactive)
    gets +\\Seen in IMAP. Keeps IMAP aligned with BD so the monitor does not
    emit noisy "N new IMAP but none actionable" warnings for emails already
    closed. Returns count of emails marked SEEN."""
    try:
        import imaplib
        mail = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
        mail.login(config["email"], config["password"])
        mail.select("INBOX")
        since = (datetime.now() - timedelta(hours=hours)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(UNSEEN SINCE {since})')
        if not data or not data[0]:
            mail.logout()
            return 0
        uids = data[0].split()
        if not uids:
            mail.logout()
            return 0

        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        conn.row_factory = sqlite3.Row
        _ensure_emails_table(conn)

        marked = 0
        for uid in uids:
            try:
                _, fetched = mail.fetch(uid, "(BODY.PEEK[HEADER])")
                if not fetched or not fetched[0]:
                    continue
                raw = fetched[0][1] if isinstance(fetched[0], tuple) else b""
                headers = _parse_email_headers(raw)
                msg_id = headers.get("message_id", "")
                if not msg_id:
                    continue
                row = conn.execute(
                    "SELECT status FROM emails WHERE message_id = ?", (msg_id,)
                ).fetchone()
                if not row:
                    continue
                status = (row["status"] or "").strip().lower()
                if status not in TERMINAL_EMAIL_STATUSES:
                    continue
                mail.store(uid, "+FLAGS", "\\Seen")
                marked += 1
            except Exception as e:
                log.debug(f"reconcile_terminal_unseen UID {uid!r} failed: {e}")
        conn.close()
        mail.logout()
        if marked:
            log.info(f"Marked {marked} terminal email(s) SEEN in IMAP (BD↔IMAP sync, last {hours}h)")
        return marked
    except Exception as e:
        log.warning(f"reconcile_terminal_unseen failed: {e}")
        return 0


def _recover_unreplied_processed(config, hours=24):
    """Recover emails marked 'processed' that never got an actual reply sent.
    This catches the case where NEXO dies (exit -9) after marking BD as processed
    but before sending the reply via nexo-send-reply.py."""
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        conn.row_factory = sqlite3.Row
        ensure_email_events_table(conn)

        max_rowid = conn.execute("SELECT MAX(rowid) FROM emails").fetchone()[0] or 0
        lookback_rows = max(50, int(hours * 5))
        min_rowid = max(0, max_rowid - lookback_rows)
        rows = conn.execute(
            """
            SELECT e.message_id, e.from_addr, e.subject, e.attempts
            FROM emails e
            WHERE e.status = 'processed'
              AND e.rowid > ?
              AND COALESCE(e.attempts, 0) < ?
              AND NOT EXISTS (
                  SELECT 1 FROM email_events ev
                  WHERE ev.email_id = e.message_id
                    AND ev.event IN ('replied', 'resolution', 'action_done')
              )
            ORDER BY e.rowid DESC
            LIMIT 20
            """,
            (min_rowid, MAX_EMAIL_ATTEMPTS),
        ).fetchall()

        recovered = 0
        for row in rows:
            from_addr = (row["from_addr"] or "").lower()
            subject = (row["subject"] or "").lower()
            if any(p in from_addr for p in SKIP_REPLY_SENDERS):
                continue
            if any(p in subject for p in SKIP_REPLY_SUBJECTS):
                continue

            conn.execute(
                "UPDATE emails SET status = 'pending' WHERE message_id = ?",
                (row["message_id"],),
            )
            recovered += 1
            log.warning(
                f"Recovered unreplied email: from={row['from_addr'][:30]} "
                f"subj={row['subject'][:40]} — reset to 'pending'"
            )

        conn.commit()
        conn.close()
        if recovered:
            log.info(f"Recovered {recovered} unreplied processed email(s) back to 'pending'")
        return recovered
    except Exception as e:
        log.warning(f"_recover_unreplied_processed failed: {e}")
        return 0


def _refresh_actionable_batch(original_batch):
    """Between retries, refresh which emails still need processing."""
    if not original_batch:
        return original_batch
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        conn.row_factory = sqlite3.Row
        _ensure_emails_table(conn)
        ids = [item["message_id"] for item in original_batch if item.get("message_id")]
        if not ids:
            conn.close()
            return original_batch
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT message_id, subject, from_addr, status, attempts,
                   started_at, received_at
            FROM emails
            WHERE message_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        conn.close()
        row_map = {row["message_id"]: dict(row) for row in rows}
        refreshed = []
        for item in original_batch:
            row = row_map.get(item["message_id"])
            if not row:
                continue
            if row.get("status") in TERMINAL_EMAIL_STATUSES:
                continue
            refreshed.append(row)
        return refreshed if refreshed else original_batch
    except Exception:
        return original_batch


def _claim_batch_for_launch(batch):
    """Mark claimed emails as processing before the automation session starts."""
    if not batch:
        return
    now_label = datetime.now().isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        ensure_email_events_table(conn)
        cols = _email_table_columns(conn)
        for email_row in batch:
            mid = email_row.get("message_id")
            if not mid:
                continue
            updates = [
                "status = 'processing'",
                "started_at = CASE WHEN started_at IS NULL OR trim(started_at) = '' THEN ? ELSE started_at END",
            ]
            params = [now_label]
            if "completed_at" in cols:
                updates.append("completed_at = NULL")
            if "error" in cols:
                updates.append("error = NULL")
            params.append(mid)
            conn.execute(
                f"UPDATE emails SET {', '.join(updates)} WHERE message_id = ?",
                params,
            )
            _insert_event(
                conn,
                mid,
                "processing",
                "Claimed by email monitor before launch",
                {
                    "claimed_at": now_label,
                    "source": "email-monitor",
                },
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Failed to mark batch as processing before launch: {e}")


def _reset_batch_to_pending(batch, reason):
    if not batch:
        return
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        _ensure_emails_table(conn)
        cols = _email_table_columns(conn)
        for email_row in batch:
            mid = email_row.get("message_id")
            if not mid:
                continue
            updates = ["status = 'pending'"]
            params = []
            if "error" in cols:
                updates.append("error = ?")
                params.append(reason[:500])
            params.append(mid)
            conn.execute(
                f"UPDATE emails SET {', '.join(updates)} WHERE message_id = ?",
                params,
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning(f"Failed to reset claimed batch to pending: {exc}")


def _write_worker_job(*, batch, debt_block, max_retries, retry_backoff):
    job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    job_path = WORKER_JOBS_DIR / f"job-{job_id}.json"
    payload = {
        "job_id": job_id,
        "batch": batch or [],
        "debt_block": debt_block or "",
        "max_retries": int(max_retries),
        "retry_backoff_seconds": int(retry_backoff),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    job_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True))
    return job_path


def _spawn_worker(job_path, config):
    worker_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-job",
        str(job_path),
    ]
    worker_env = os.environ.copy()
    worker_env.setdefault("NEXO_HOME", str(NEXO_HOME))
    worker_env["PATH"] = f"{NEXO_HOME / 'bin'}:{worker_env.get('PATH', '')}"
    cwd = config.get("working_dir", str(Path.home()))
    proc = subprocess.Popen(
        worker_cmd,
        cwd=cwd,
        env=worker_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def _run_worker_job(job_path):
    job_file = Path(job_path)
    if not job_file.exists():
        log.error(f"Worker job not found: {job_file}")
        return 1

    batch = []
    try:
        payload = json.loads(job_file.read_text())
        batch = payload.get("batch") or []
        debt_block = payload.get("debt_block") or ""
        max_retries = max(1, int(payload.get("max_retries") or 1))
        retry_backoff = max(1, int(payload.get("retry_backoff_seconds") or 60))
    except Exception as exc:
        log.error(f"Failed to load worker job {job_file}: {exc}")
        job_file.unlink(missing_ok=True)
        return 1

    config = load_config()
    if config is None:
        # v0.32.5 — worker invoked but email setup gone (config deleted
        # between scheduling and execution). Drop the job silently
        # rather than spamming exception logs.
        log.warning(f"Worker job {job_file.name}: no email config, dropping.")
        job_file.unlink(missing_ok=True)
        return 0

    log.info(
        f"Worker job started: {job_file.name} "
        f"(emails={len(batch)} debt={'yes' if debt_block else 'no'})"
    )

    try:
        for attempt in range(1, max_retries + 1):
            success = launch_nexo(config, debt_block=debt_block, target_emails=batch)
            if success:
                _recover_unreplied_processed(config, hours=4)
                track_failure(True)
                return 0

            if attempt < max_retries:
                wait = retry_backoff * attempt
                log.warning(f"NEXO failed (attempt {attempt}/{max_retries}). Retrying in {wait}s...")
                _recover_unreplied_processed(config, hours=4)
                batch = _refresh_actionable_batch(batch)
                if batch:
                    _increment_attempts([e["message_id"] for e in batch])
                    _claim_batch_for_launch(batch)
                time.sleep(wait)

        log.error(f"NEXO failed after {max_retries} attempts")
        _recover_unreplied_processed(config, hours=4)
        if batch:
            _escalate_exhausted_emails(config, batch)
        track_failure(False)
        return 1
    finally:
        _unregister_session()
        job_file.unlink(missing_ok=True)


def _get_operator_info():
    profile = get_operator_profile()
    return (
        str(profile.get("operator_name") or "the operator"),
        str(profile.get("assistant_name") or DEFAULT_ASSISTANT_NAME),
        str(profile.get("language") or DEFAULT_OPERATOR_LANGUAGE).strip() or DEFAULT_OPERATOR_LANGUAGE,
    )


def _uses_spanish(language: str) -> bool:
    normalized = str(language or "").strip().lower().replace("_", "-")
    return normalized == "es" or normalized.startswith("es-")


def _localized_operator_escalation_email(
    *,
    operator_name: str,
    assistant_name: str,
    operator_language: str,
    exhausted_count: int,
    details: str,
) -> tuple[str, str]:
    if _uses_spanish(operator_language):
        subject = f"[NEXO] Emails requiring manual attention ({exhausted_count})"
        body = (
            f"Hello {operator_name},\n\n"
            f"The following emails have already been attempted {MAX_EMAIL_ATTEMPTS} times "
            f"without succeeding (the session dies before completion):\n\n{details}\n\n"
            "I marked them as `needs_interactive`. "
            f"Open {assistant_name} Desktop and ask about the affected email so it can be resolved manually.\n\n"
            f"— {assistant_name}"
        )
        return subject, body

    subject = f"[NEXO] Emails requiring manual attention ({exhausted_count})"
    body = (
        f"Hello {operator_name},\n\n"
        f"The following emails have already been attempted {MAX_EMAIL_ATTEMPTS} times "
        f"without succeeding (the session dies before completion):\n\n{details}\n\n"
        f"I marked them as `needs_interactive`. "
        f"Open {assistant_name} Desktop and ask about the affected email so it can be resolved manually.\n\n"
        f"— {assistant_name}"
    )
    return subject, body


def _operator_aliases(config) -> list[str]:
    aliases: list[str] = []
    profile = get_operator_profile()
    for candidate in list(profile.get("operator_aliases") or []):
        value = str(candidate or "").strip().lower()
        if value and value not in aliases:
            aliases.append(value)
    # Legacy alias list kept only as compatibility input from the old
    # flat JSON config. New installs should populate operator_accounts.
    legacy_aliases = list(config.get("operator_aliases") or config.get("francisco_emails") or [])
    for candidate in legacy_aliases:
        value = str(candidate or "").strip().lower()
        if value and value not in aliases:
            aliases.append(value)
    operator_email = str(config.get("operator_email") or "").strip().lower()
    if operator_email and operator_email not in aliases:
        aliases.append(operator_email)
    return aliases


def _trusted_sender_domains(config, operator_aliases=None) -> list[str]:
    domains: list[str] = []

    def _add_domain(raw: str) -> None:
        value = str(raw or "").strip().lower()
        if not value:
            return
        if "@" in value:
            value = value.rsplit("@", 1)[-1].strip()
        value = value.strip("<>")
        if value and value not in domains:
            domains.append(value)

    for candidate in list(config.get("trusted_domains") or []):
        _add_domain(candidate)
    for candidate in list(operator_aliases or []):
        _add_domain(candidate)
    _add_domain(config.get("operator_email", ""))
    _add_domain(config.get("email", ""))
    return domains


def _runtime_path(existing_path: str = "") -> str:
    runtime_owner_home = NEXO_HOME.parent if NEXO_HOME.parent != NEXO_HOME else Path.home()
    parts = [
        str(NEXO_HOME / "runtime" / "bootstrap" / "npm-global" / "bin"),
        str(runtime_owner_home / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        existing_path,
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        for candidate in str(raw or "").split(":"):
            value = candidate.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return ":".join(ordered)


def build_processing_prompt(
    *,
    config,
    operator_name: str,
    assistant_name: str,
    operator_language: str,
    operator_email: str,
    operator_aliases_label: str,
    trusted_domains_label: str,
    send_reply_script,
    send_reply_target: str,
    agent_email_label: str,
    extra_instructions_block: str,
    project_atlas_path,
    target_emails=None,
    needs_interactive=None,
    normal_emails=None,
    debt_block: str = "",
    routing_rules: str = "",
    recent_hot_context: str = "",
    previous_progress_block: str = "",
) -> str:
    interactive_emails = list(needs_interactive or [])
    target_items = list(target_emails or [])
    normal_items = list(normal_emails or [])

    interactive_block = ""
    if interactive_emails:
        email_details = "\n".join(
            f"  - Subject: '{e.get('subject', '?')}' | From: {e.get('from_addr', '?')} | Attempts: {e.get('attempts', 0)}"
            for e in interactive_emails
        )
        interactive_block = (
            "\n== EMAILS REQUIRING MANUAL ATTENTION ==\n"
            f"The following emails have already been processed unsuccessfully {MAX_EMAIL_ATTEMPTS}+ times.\n"
            "Do NOT attempt the task again inside this daemon run. Instead:\n"
            f"1. Send an email TO {operator_name} ({operator_email}) explaining:\n"
            f"   - You received these emails and already attempted them {MAX_EMAIL_ATTEMPTS} times\n"
            "   - You could not complete the task automatically\n"
            f"   - {operator_name} should open {assistant_name} Desktop and ask about the affected email\n"
            "   - Include subject and sender so the operator knows which email this is about\n"
            "2. Mark these emails as status='needs_interactive' in the DB.\n"
            "3. Do NOT reply to the original sender — only to the operator.\n"
            f"Affected emails:\n{email_details}\n"
        )

    target_block = ""
    if target_items:
        ids = [e["message_id"] for e in (normal_items + interactive_emails)]
        target_block = (
            "\n== EMAILS ASSIGNED TO THIS SESSION ==\n"
            "Process ONLY these emails (by message_id). Do NOT process any others.\n"
            "Message-IDs:\n" + "\n".join(f"  - {mid}" for mid in ids) + "\n"
        )

    return render_core_prompt(
        "email-monitor",
        assistant_name=assistant_name,
        agent_mailbox=config.get("email", "agent@email"),
        recent_hot_context=recent_hot_context,
        project_atlas_path=project_atlas_path,
        operator_name=operator_name,
        operator_language=operator_language,
        email_db_path=EMAIL_DB_PATH,
        debt_sla_hours=DEBT_SLA_HOURS,
        zombie_timeout_hours=ZOMBIE_TIMEOUT_HOURS,
        config_path=CONFIG_PATH,
        agent_email_label=agent_email_label,
        send_reply_target=send_reply_target,
        operator_aliases_label=operator_aliases_label,
        python_executable=sys.executable,
        send_reply_script=send_reply_script,
        trusted_domains_label=trusted_domains_label,
        routing_rules=routing_rules or "No special routing rules.",
        extra_instructions_block=(
            (
                ("\n" + extra_instructions_block.strip() + "\n") if extra_instructions_block.strip() else ""
            )
            + (previous_progress_block or "")
        ),
        target_block=target_block,
        interactive_block=interactive_block,
        debt_block=(f"\n{debt_block.strip()}\n" if str(debt_block or "").strip() else ""),
    )


_EMAIL_MONITOR_COMPLEXITY_HIGH_TERMS = (
    "urgent", "urgente", "complaint", "queja", "reclamacion", "reclamación",
    "legal", "contract", "contrato", "invoice", "factura", "payment", "pago",
    "refund", "devolucion", "devolución", "booking", "reserva", "reservation",
    "cancel", "cancelacion", "cancelación", "cancellation", "deadline",
    "plazo", "claim", "dispute", "incidencia", "broken", "error",
)
_EMAIL_MONITOR_COMPLEXITY_SIMPLE_TERMS = (
    "thanks", "thank you", "gracias", "ok", "okay", "received", "recibido",
    "confirmado", "confirmation", "confirmacion", "confirmación", "perfecto",
    "noted", "anotado",
)


def _email_monitor_complexity_tier(target_emails=None, *, needs_interactive=None, debt_block: str = "") -> str:
    emails = list(target_emails or [])
    interactive = list(needs_interactive or [])
    if str(debt_block or "").strip() or interactive:
        return "alto"
    if len(emails) >= 3:
        return "alto"

    text_parts: list[str] = []
    attempts = 0
    for em in emails:
        getter = em.get if hasattr(em, "get") else lambda key, default=None: default
        attempts = max(attempts, _safe_int(getter("attempts", 0), 0))
        for key in ("subject", "from_addr", "snippet", "body", "body_text"):
            value = str(getter(key, "") or "").strip()
            if value:
                text_parts.append(value)
    if attempts > 0:
        return "alto"

    joined = " ".join(text_parts).lower()
    if any(term in joined for term in _EMAIL_MONITOR_COMPLEXITY_HIGH_TERMS):
        return "alto"
    if emails and len(emails) <= 1 and any(term in joined for term in _EMAIL_MONITOR_COMPLEXITY_SIMPLE_TERMS):
        return "bajo"
    return "medio"


def launch_nexo(config, debt_block="", target_emails=None):
    """Launch NEXO through the configured automation backend to process emails.
    target_emails: optional list of dicts with message_id, subject, attempts."""

    operator_name, assistant_name, operator_language = _get_operator_info()
    agent_email = str(config.get("email") or "").strip()
    operator_email = config.get("operator_email", "")
    operator_aliases = _operator_aliases(config)
    operator_aliases_label = ", ".join(operator_aliases) if operator_aliases else "no operator aliases configured"
    trusted_domains = _trusted_sender_domains(config, operator_aliases)
    trusted_domains_label = ", ".join(trusted_domains) if trusted_domains else "configured trusted domains"
    send_reply_script = get_send_reply_script_path(local_script_dir=_script_dir)
    send_reply_target = operator_email or "OPERATOR_EMAIL_NOT_CONFIGURED"
    agent_email_label = agent_email or "the agent mailbox"
    extra_instructions_block = format_operator_extra_instructions_block("email-monitor")
    project_atlas_path = brain_dir() / "project-atlas.json"

    needs_interactive = []
    normal_emails = []
    if target_emails:
        for em in target_emails:
            if (em.get("attempts") or 0) >= MAX_EMAIL_ATTEMPTS:
                needs_interactive.append(em)
            else:
                normal_emails.append(em)

    routing_rules = operator_routing_context()
    recent_hot_context = read_recent_hot_context(query="", hours=24, limit=10)
    target_message_ids = [str(e.get("message_id") or "") for e in (target_emails or []) if e.get("message_id")]
    previous_progress_block = _build_previous_progress_block(target_message_ids)
    if previous_progress_block:
        log.info(
            f"Resuming from checkpoint(s) for {len(target_message_ids)} email(s); "
            "previous attempt context attached to prompt."
        )
    email_tier = _email_monitor_complexity_tier(
        target_emails=target_emails,
        needs_interactive=needs_interactive,
        debt_block=debt_block,
    )
    prompt = build_processing_prompt(
        config=config,
        operator_name=operator_name,
        assistant_name=assistant_name,
        operator_language=operator_language,
        operator_email=operator_email,
        operator_aliases_label=operator_aliases_label,
        trusted_domains_label=trusted_domains_label,
        send_reply_script=send_reply_script,
        send_reply_target=send_reply_target,
        agent_email_label=agent_email_label,
        extra_instructions_block=extra_instructions_block,
        project_atlas_path=project_atlas_path,
        target_emails=target_emails,
        needs_interactive=needs_interactive,
        normal_emails=normal_emails,
        debt_block=debt_block,
        routing_rules=routing_rules,
        recent_hot_context=recent_hot_context,
        previous_progress_block=previous_progress_block,
    )
    working_dir = config.get("working_dir", str(Path.home()))
    run_started_at = time.time()

    env = os.environ.copy()
    env["NEXO_HEADLESS"] = "1"  # Skip stop hook post-mortem
    # Remove Claude Code env vars to avoid conflicts
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)
    env["TERM"] = "dumb"
    env["PATH"] = _runtime_path(env.get("PATH", ""))
    env["HOME"] = str(Path.home())

    backend = resolve_automation_backend()
    profile_label = "default"
    try:
        from resonance_map import resolve_model_and_effort

        mapped_model, mapped_effort = resolve_model_and_effort("email_monitor", backend, explicit_tier=email_tier)
        profile_label = mapped_model or "default"
        if mapped_effort:
            profile_label = f"{profile_label}/{mapped_effort}"
    except Exception:
        pass
    log.info(f"Launching NEXO via {backend} ({profile_label})...")
    requested_timeout = int(config.get("max_process_time", MAX_AUTOMATION_TIMEOUT_SECONDS) or MAX_AUTOMATION_TIMEOUT_SECONDS)
    effective_timeout = max(60, min(requested_timeout, MAX_AUTOMATION_TIMEOUT_SECONDS))

    def _persist_failure_checkpoints(*, error_msg: str, last_text: str) -> None:
        """Capture per-email checkpoint when the run did not complete OK so
        the next attempt's prompt carries the previous attempt's progress.
        Best-effort: never raises out of here."""
        if not target_message_ids:
            return
        try:
            files_touched = _scan_files_modified_since(working_dir, run_started_at)
        except Exception:
            files_touched = []
        for em in target_emails or []:
            mid = str(em.get("message_id") or "")
            if not mid:
                continue
            _email_checkpoint_write(
                message_id=mid,
                subject=str(em.get("subject") or ""),
                files_touched=files_touched,
                last_assistant_text=last_text,
                last_error=error_msg,
                attempts=int((em.get("attempts") or 0) + 1),
            )

    try:
        result = run_automation_prompt(
            prompt,
            caller="email_monitor",
            cwd=config.get("working_dir", str(Path.home())),
            env=env,
            timeout=effective_timeout,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
            tier=email_tier,
        )

        if result.stdout.strip():
            log.info(f"NEXO output: {result.stdout.strip()[:1000]}")
        if result.returncode != 0:
            log.error(f"NEXO exit code {result.returncode}")
            if result.stderr:
                log.error(f"stderr: {result.stderr[:500]}")
            _persist_failure_checkpoints(
                error_msg=f"exit {result.returncode}: {(result.stderr or '')[:200]}",
                last_text=_extract_last_assistant_text_from_run(result.stdout or ""),
            )
            return False
        # Success: drop checkpoints for the emails the worker just handled,
        # so the recovery context does not leak into a future, unrelated
        # attempt on the same Message-ID (rare, but possible after a
        # status reset by ``_recover_unreplied_processed``).
        for mid in target_message_ids:
            _email_checkpoint_delete(mid)
        return True

    except AutomationBackendUnavailableError as e:
        log.error(f"Automation backend unavailable: {e}")
        _persist_failure_checkpoints(error_msg=f"AutomationBackendUnavailable: {e}", last_text="")
        return False
    except subprocess.TimeoutExpired:
        log.error(f"Email automation exceeded {effective_timeout}s and was terminated")
        _persist_failure_checkpoints(
            error_msg=f"timeout after {effective_timeout}s",
            last_text="",
        )
        return False
    except Exception as e:
        log.error(f"Launch error: {e}")
        _persist_failure_checkpoints(error_msg=f"unexpected: {e}", last_text="")
        return False
def track_failure(success):
    """Track consecutive failures. Alert if 3+ in a row."""
    if success:
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
        return

    count = 1
    if ALERT_FILE.exists():
        try:
            count = int(ALERT_FILE.read_text().strip()) + 1
        except ValueError:
            count = 1
    ALERT_FILE.write_text(str(count))

    if count >= 3:
        log.error(f"ALERT: {count} consecutive failures! Email monitor may be broken.")
        alert_flag = BASE_DIR / ".alert-monitor-failing"
        alert_flag.write_text(f"{count} consecutive failures as of {datetime.now().isoformat()}")


def _increment_attempts(email_ids):
    """Bump attempts counter for a batch of emails before launching."""
    if not email_ids:
        return
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        for mid in email_ids:
            conn.execute(
                "UPDATE emails SET attempts = COALESCE(attempts, 0) + 1 WHERE message_id = ?",
                (mid,),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Failed to increment attempts: {e}")


def _mark_needs_interactive(email_ids):
    """Mark emails as needs_interactive after too many failed attempts."""
    if not email_ids:
        return
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        for mid in email_ids:
            conn.execute(
                "UPDATE emails SET status = 'needs_interactive' WHERE message_id = ?",
                (mid,),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Failed to mark needs_interactive: {e}")


def _filter_already_notified(message_ids):
    """Return the subset of message_ids that have NOT been escalated yet
    (i.e. emails.escalation_notified_at IS NULL). Idempotent: if the column
    is missing for any reason, no row is filtered out."""
    if not message_ids:
        return []
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        try:
            _ensure_emails_table(conn)
            placeholders = ",".join("?" for _ in message_ids)
            rows = conn.execute(
                f"""
                SELECT message_id FROM emails
                WHERE message_id IN ({placeholders})
                  AND escalation_notified_at IS NULL
                """,
                tuple(message_ids),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"escalation_notified_at filter failed, falling through: {e}")
        return list(message_ids)


def _mark_escalation_notified(message_ids):
    """Stamp emails.escalation_notified_at = now() so we never re-notify
    the operator about the same exhausted email."""
    if not message_ids:
        return
    try:
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        try:
            _ensure_emails_table(conn)
            now_iso = datetime.now(timezone.utc).isoformat()
            for mid in message_ids:
                conn.execute(
                    "UPDATE emails SET escalation_notified_at = ? WHERE message_id = ?",
                    (now_iso, mid),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"Failed to stamp escalation_notified_at: {e}")


def _escalate_exhausted_emails(config, batch):
    """After all retries exhausted, directly escalate emails with attempts >= MAX
    by marking them needs_interactive and sending email to operator via nexo-send-reply.py.

    Deduplicated by emails.escalation_notified_at: if an email is already in
    needs_interactive state from a previous run, we do not re-notify the operator."""
    exhausted = [e for e in batch if (e.get("attempts", 0) + 1) >= MAX_EMAIL_ATTEMPTS]
    if not exhausted:
        return
    ids = [e["message_id"] for e in exhausted]
    _mark_needs_interactive(ids)
    log.info(f"Marked {len(ids)} email(s) as needs_interactive after exhausting retries")

    pending_notify_ids = set(_filter_already_notified(ids))
    if not pending_notify_ids:
        log.info(
            f"All {len(ids)} exhausted email(s) already escalated to operator earlier — skipping duplicate notification."
        )
        return
    skipped = len(ids) - len(pending_notify_ids)
    if skipped:
        log.info(
            f"Escalation dedup: {skipped} email(s) already notified, will only escalate {len(pending_notify_ids)} new one(s)."
        )
    exhausted = [e for e in exhausted if e["message_id"] in pending_notify_ids]

    operator_name, assistant_name, operator_language = _get_operator_info()
    operator_email = config.get("operator_email", "")
    if not operator_email:
        log.warning("No operator_email configured — cannot send escalation email")
        return

    details = "\n".join(
        f"  - Subject: {e.get('subject', '?')} | From: {e.get('from_addr', '?')}"
        for e in exhausted
    )
    subject, body = _localized_operator_escalation_email(
        operator_name=operator_name,
        assistant_name=assistant_name,
        operator_language=operator_language,
        exhausted_count=len(exhausted),
        details=details,
    )
    body_file = BASE_DIR / ".escalation-body.txt"
    body_file.write_text(body, encoding="utf-8")

    send_script = get_send_reply_script_path(local_script_dir=_script_dir)
    send_ok = False
    try:
        result = subprocess.run(
            [
                sys.executable, str(send_script),
                "--to", f"{operator_name} <{operator_email}>",
                "--subject", subject,
                "--body-file", str(body_file),
            ],
            timeout=30,
            capture_output=True,
        )
        send_ok = (result.returncode == 0)
        if send_ok:
            log.info(f"Escalation email sent to {operator_email}")
        else:
            log.warning(
                f"Escalation send returned exit={result.returncode}; "
                f"stderr={(result.stderr or b'').decode('utf-8', 'replace')[:200]}"
            )
    except Exception as e:
        log.warning(f"Failed to send escalation email: {e}")
    finally:
        body_file.unlink(missing_ok=True)

    if send_ok:
        _mark_escalation_notified(list(pending_notify_ids))


def main():
    log.info("=== Monitor check ===")

    try:
        contract = get_script_runtime_contract("email-monitor")
        if not contract.get("available", True):
            log.warning(f"Runtime blocked: {contract.get('blocked_reason') or 'missing prerequisite'}")
            track_failure(True)
            return

        config = load_config()
        # v0.32.5 — exit cleanly when no email setup exists. Before this,
        # FileNotFoundError bubbled up to 1,440 errors/day per Windows client
        # without email. Now the monitor exits silently.
        if config is None:
            log.info("No email config — skipping monitor check.")
            return
        base_interval_seconds = max(60, _safe_int(config.get("check_interval_seconds"), 300))
        backoff_state = load_empty_inbox_backoff_state()
        debt_block = scan_debt()

        reconcile_orphaned_seen(config, hours=24)
        reconcile_terminal_unseen(config, hours=48)
        # Recovery window widened from 24h to 7 days (168h): a single email can
        # fall between several Brain releases in a short window (4 releases in
        # one day on 2026-04-26). The 24h sweep let those drop into a permanent
        # limbo because the next sweep happened after the email was already
        # outside the lookback. 7 days is large enough to absorb a normal
        # release cadence while still small enough that very old "stuck"
        # emails are not retried indefinitely. Companion checkpoint system in
        # ``_email_checkpoint_*`` lets a retried email continue from the
        # previous attempt's progress instead of restarting from scratch.
        _recover_unreplied_processed(config, hours=168)
        preregistered_count = preregister_pending_emails(config)
        _email_checkpoint_cleanup(max_age_days=7)

        # --- Concurrency check ---
        active_count = _active_session_count()
        if active_count >= MAX_CONCURRENT_SESSIONS:
            if not has_long_running_session():
                log.info(
                    f"{active_count} active session(s), none over {CONCURRENT_THRESHOLD_MINUTES}m. Skipping."
                )
                return
            log.info(
                f"{active_count} active session(s) but at least one >{CONCURRENT_THRESHOLD_MINUTES}m. "
                "Max concurrent reached — skipping."
            )
            return

        if active_count > 0 and not has_long_running_session():
            log.info(
                f"{active_count} active session(s), none over {CONCURRENT_THRESHOLD_MINUTES}m threshold. "
                "Waiting for current session to finish or exceed threshold."
            )
            return

        # --- Get actionable emails ---
        conn = sqlite3.connect(str(EMAIL_DB_PATH))
        conn.row_factory = sqlite3.Row
        _ensure_emails_table(conn)
        actionable = get_actionable_emails(conn, priority_aliases=_operator_aliases(config), config=config)
        conn.close()

        stuck_count = len(actionable)
        imap_count = has_new_email(config)

        if imap_count < 0:
            log.error("IMAP check returned error")
            if stuck_count == 0:
                track_failure(False)
                return

        total = max(stuck_count, imap_count if imap_count > 0 else 0)

        if total > 0:
            if backoff_state.get("empty_runs"):
                log.info(
                    "Resetting empty-inbox backoff after new email(s): "
                    f"empty_runs={backoff_state.get('empty_runs', 0)}"
                )
            reset_empty_inbox_backoff_state("new_email")
        else:
            backoff = update_empty_inbox_backoff_state(
                state=backoff_state,
                base_interval_seconds=base_interval_seconds,
                debt_block=debt_block,
            )

            if not debt_block:
                log.info(
                    "No new whitelisted emails. "
                    f"empty_runs={backoff['empty_runs']} "
                    f"next_interval={backoff['interval_label']} "
                    f"next_due={backoff['next_allowed_wake_at']}"
                )
                track_failure(True)
                return

            if backoff["should_suppress_debt_wake"]:
                log.info(
                    "Debt detected but suppressed by empty-inbox backoff. "
                    f"empty_runs={backoff['empty_runs']} "
                    f"next_interval={backoff['interval_label']} "
                    f"next_due={backoff['next_allowed_wake_at']}"
                )
                track_failure(True)
                return

            log.info(
                "Debt wake allowed under empty-inbox backoff. "
                f"empty_runs={backoff['empty_runs']} "
                f"next_interval={backoff['interval_label']} "
                f"next_due={backoff['next_allowed_wake_at']}"
                f"{' (debt changed)' if backoff['debt_changed'] else ''}"
            )

        if total == 0 and not debt_block:
            track_failure(True)
            return

        # --- Claim work under lock so later runs can safely proceed in parallel ---
        if not acquire_lock():
            log.info("Another monitor instance is claiming work. Skipping.")
            return

        try:
            conn = sqlite3.connect(str(EMAIL_DB_PATH))
            conn.row_factory = sqlite3.Row
            _ensure_emails_table(conn)
            actionable = get_actionable_emails(conn, priority_aliases=_operator_aliases(config), config=config)
            conn.close()

            if actionable:
                batch = [actionable[0]]
                email_ids = [batch[0]["message_id"]]
                remaining = len(actionable) - 1
                log.info(
                    f"1 actionable email (of {len(actionable)}) — waking NEXO "
                    f"(session {active_count + 1}/{MAX_CONCURRENT_SESSIONS})"
                    f"{f' ({remaining} more queued)' if remaining else ''}"
                )
                _increment_attempts(email_ids)
                batch = _refresh_actionable_batch(batch)
                _claim_batch_for_launch(batch)
            elif debt_block:
                batch = None
                log.info("Debt detected in email lifecycle -- waking NEXO")
            else:
                log.warning(
                    f"{imap_count} new IMAP email(s) but none actionable in DB after preregister "
                    f"(inserted={preregistered_count}, active_sessions={active_count}). "
                    "Skipping blind wake; waiting for a tracked actionable email or debt."
                )
                track_failure(True)
                return
        finally:
            release_lock()

        max_retries = config.get("max_retries", 3)
        retry_backoff = config.get("retry_backoff_seconds", 60)
        job_path = _write_worker_job(
            batch=batch,
            debt_block=debt_block,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
        )
        try:
            worker_pid = _spawn_worker(job_path, config)
        except Exception as exc:
            log.error(f"Failed to spawn email worker: {exc}")
            _reset_batch_to_pending(batch, f"spawn failed: {exc}")
            job_path.unlink(missing_ok=True)
            track_failure(False)
            return

        _register_session(worker_pid, [item["message_id"] for item in batch] if batch else [])
        log.info(
            f"Spawned detached email worker PID {worker_pid} "
            f"for {len(batch) if batch else 0} email(s)"
            f"{' + debt' if debt_block else ''}"
        )
        return

    except Exception as e:
        log.error(f"Error: {e}")
        track_failure(False)
    finally:
        log.info("=== Monitor done ===")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker-job":
        sys.exit(_run_worker_job(sys.argv[2]))
    main()
