from __future__ import annotations
"""Session management tools: startup, heartbeat, status."""

import json
import os
import paths
import queue
import sqlite3
import time
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from core_prompts import render_core_prompt
from client_preferences import client_to_provider, normalize_provider_key
from db import (
    register_session, update_session, complete_session,
    get_active_sessions, clean_stale_sessions, search_sessions,
    get_inbox, get_pending_questions, now_epoch,
    SESSION_STALE_SECONDS, check_session_has_diary,
    save_checkpoint, read_checkpoint, increment_compaction_count,
    get_db, build_pre_action_context, format_pre_action_context_bundle,
    capture_context_event, maintain_memory_observations,
)

try:
    from tools_hot_context import append_local_context_evidence
except Exception:  # pragma: no cover - local context is optional during bootstrap
    append_local_context_evidence = None

try:
    from r14_correction_learning import detect_correction as _detect_correction_semantic
except Exception:  # pragma: no cover - optional runtime dependency
    _detect_correction_semantic = None

# ── Session Keepalive ────────────────────────────────────────────────
# Background thread per session that auto-pings last_update_epoch every
# KEEPALIVE_INTERVAL seconds.  This prevents clean_stale_sessions from
# killing sessions that are alive but quiet (e.g. waiting on long Tasks).
# Threads are daemon=True so they die when the MCP server process exits.

KEEPALIVE_INTERVAL = 600  # 10 min — well inside the 15-min TTL


# Path resolution moved to lazy functions (AUDITOR-V700-PASS2 §11, B10 item
# 3). The prior module-level NEXO_HOME / SESSION_PORTABILITY_DIR constants
# were evaluated at import time, so tests that monkeypatched NEXO_HOME or
# paths.operations_dir() after import saw stale values. The ``__getattr__``
# hook below keeps ``tools_sessions.SESSION_PORTABILITY_DIR`` / ``.NEXO_HOME``
# working for attribute-style access (re-evaluated on every read). The
# existing ``monkeypatch.setattr(tools_sessions, "SESSION_PORTABILITY_DIR",
# ...)`` pattern in tests keeps working because setattr inserts into the
# module __dict__ and shadows __getattr__.


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _session_portability_dir() -> Path:
    return paths.operations_dir() / "session-portability"


_LAZY_PATHS = {
    "NEXO_HOME": _nexo_home,
    "SESSION_PORTABILITY_DIR": _session_portability_dir,
}


def __getattr__(name: str):
    resolver = _LAZY_PATHS.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return resolver()


try:
    from semantic_layers import redact_value as _redact_packet_value
except Exception:  # pragma: no cover - semantic layer may not be initialized at bootstrap
    def _redact_packet_value(value, *, max_chars=4000):
        return str(value or "")[:max_chars]


_SENSITIVE_PACKET_KEYS = {
    "body", "text_raw", "tool_input", "tool_output", "provider_payload",
    "raw_prompt", "raw_response", "transcript", "messages", "content_raw",
    "summary", "context_summary",
}


def _safe_packet_text(value, *, max_chars: int = 600) -> str:
    return _redact_packet_value(value, max_chars=max_chars)


def _safe_packet_payload(value, *, _depth: int = 0):
    if _depth > 5:
        return "[redacted_depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            raw_key = str(key or "").strip().lower()
            safe_key = _safe_packet_text(key, max_chars=120) or "field"
            if raw_key in _SENSITIVE_PACKET_KEYS:
                clean[safe_key] = "[redacted_payload]"
            else:
                clean[safe_key] = _safe_packet_payload(item, _depth=_depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_packet_payload(item, _depth=_depth + 1) for item in list(value)[:100]]
    return _safe_packet_text(value)

_keepalive_threads: dict[str, tuple[threading.Event, threading.Thread]] = {}  # sid -> (stop_event, thread)


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag with sane falsey values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _heartbeat_heavy_feature_enabled(name: str, default: bool = False) -> bool:
    """Gate expensive heartbeat add-ons out of the visible chat path.

    Heartbeat is the liveness/obligation primitive; it must not load local
    classifiers, Local Context, or analysis models on Desktop unless explicitly
    forced. Operators can still opt in with ``force`` / ``always`` for diagnosis.
    """
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if raw in {"force", "always", "required"}:
        return True
    client = str(os.environ.get("NEXO_MCP_CLIENT", "") or "").strip().lower()
    desktop_env = any(
        str(os.environ.get(key, "") or "").strip()
        for key in (
            "NEXO_DESKTOP_PRODUCT_SMOKE",
            "NEXO_DESKTOP_USER_DATA_DIR",
            "NEXO_DESKTOP_MANAGED",
            "NEXO_DESKTOP_MANAGED_SESSION",
        )
    )
    if desktop_env or client in {"desktop", "nexo_desktop", "claude_desktop", "claude_code"}:
        return False
    return _env_flag(name, default=default)


def _interactive_db_timeout_ms() -> int:
    """Short DB wait for interactive MCP tools.

    Long waits make Desktop look frozen when a background cron briefly owns
    the SQLite writer lock. Interactive tools should degrade and let the chat
    continue instead of waiting 30s per query.
    """
    try:
        return max(50, min(int(os.environ.get("NEXO_MCP_DB_BUSY_TIMEOUT_MS", "250")), 10000))
    except Exception:
        return 250


def _set_interactive_db_timeout() -> None:
    try:
        conn = get_db()
        conn.execute(f"PRAGMA busy_timeout={_interactive_db_timeout_ms()}")
    except Exception:
        pass


def _is_db_busy(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
        return True
    return "database is locked" in str(exc).lower()


def _safe_interactive(label: str, fn, default=None, warnings: list[str] | None = None):
    try:
        return fn()
    except Exception as exc:
        if warnings is not None:
            if _is_db_busy(exc):
                warnings.append(f"{label}: skipped because the local brain database is busy")
            else:
                warnings.append(f"{label}: skipped ({type(exc).__name__})")
        return default


def _interactive_timeout_seconds(name: str, default_ms: int) -> float:
    try:
        raw = os.environ.get(name, str(default_ms))
        value = int(raw)
    except Exception:
        value = default_ms
    return max(0.05, min(value, 10000) / 1000.0)


def _safe_interactive_timed(label: str, fn, default=None, warnings: list[str] | None = None, timeout_ms: int = 1200):
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put((True, fn()))
        except Exception as exc:
            result_queue.put((False, exc))

    worker = threading.Thread(target=runner, name=f"nexo-{label[:24]}", daemon=True)
    worker.start()
    try:
        ok, value = result_queue.get(timeout=_interactive_timeout_seconds("NEXO_MCP_INTERACTIVE_CONTEXT_TIMEOUT_MS", timeout_ms))
    except queue.Empty:
        if warnings is not None:
            warnings.append(f"{label}: skipped because it exceeded the interactive time budget")
        return default
    if ok:
        return value
    if warnings is not None:
        if _is_db_busy(value):
            warnings.append(f"{label}: skipped because the local brain database is busy")
        else:
            warnings.append(f"{label}: skipped ({type(value).__name__})")
    return default


def _keepalive_loop(sid: str, stop_event: threading.Event) -> None:
    """Periodically touch the session's last_update_epoch until stopped."""
    while not stop_event.wait(KEEPALIVE_INTERVAL):
        try:
            update_session(sid, None)  # None = keep current task, just touch timestamp
        except Exception:
            break  # DB gone or session deleted — exit silently


def _start_keepalive(sid: str) -> None:
    """Start a keepalive thread for the given session."""
    _stop_keepalive(sid)  # clean up any leftover
    stop_event = threading.Event()
    t = threading.Thread(target=_keepalive_loop, args=(sid, stop_event), daemon=True)
    _keepalive_threads[sid] = (stop_event, t)
    t.start()


def _stop_keepalive(sid: str, join_timeout: float = 1.0) -> None:
    """Signal the keepalive thread for the given session to stop."""
    entry = _keepalive_threads.pop(sid, None)
    if entry is None:
        return
    stop_event, thread = entry
    stop_event.set()
    if thread is not threading.current_thread():
        thread.join(timeout=max(0.0, join_timeout))


def _stop_all_keepalives(join_timeout: float = 1.0) -> None:
    """Signal and briefly join all keepalive threads before DB shutdown."""
    entries = list(_keepalive_threads.values())
    _keepalive_threads.clear()
    for stop_event, _thread in entries:
        stop_event.set()
    deadline = time.monotonic() + max(0.0, join_timeout)
    for _stop_event, thread in entries:
        if thread is threading.current_thread():
            continue
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)


def _generate_sid() -> str:
    """Generate unique session ID: nexo-{epoch}-{random}."""
    return f"nexo-{int(time.time())}-{secrets.randbelow(100000)}"


def _format_age(epoch: float) -> str:
    """Format seconds since epoch as human-readable age."""
    seconds = now_epoch() - epoch
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m"
    else:
        return f"{int(seconds / 3600)}h{int((seconds % 3600) / 60)}m"


def _resolve_session_row(conn, sid: str = ""):
    if sid.strip():
        return conn.execute("SELECT * FROM sessions WHERE sid = ?", (sid.strip(),)).fetchone()
    return conn.execute(
        "SELECT * FROM sessions ORDER BY last_update_epoch DESC LIMIT 1"
    ).fetchone()


def _session_portability_bundle(sid: str = "") -> dict:
    conn = get_db()
    session_row = _resolve_session_row(conn, sid)
    if not session_row:
        return {"ok": False, "error": "session not found"}

    session_id = str(session_row["sid"])
    checkpoint = read_checkpoint(session_id) or {}
    diary = conn.execute(
        """SELECT summary, decisions, pending, context_next, mental_state, domain, created_at
           FROM session_diary
           WHERE session_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (session_id,),
    ).fetchone()
    draft = conn.execute(
        """SELECT summary_draft, last_context_hint, updated_at
           FROM session_diary_draft
           WHERE sid = ?""",
        (session_id,),
    ).fetchone()
    protocol_tasks = [
        dict(row) for row in conn.execute(
            """SELECT task_id, goal, task_type, area, status, opened_at
               FROM protocol_tasks
               WHERE session_id = ? AND status = 'open'
               ORDER BY opened_at DESC
               LIMIT 10""",
            (session_id,),
        ).fetchall()
    ]
    workflow_goals = [
        dict(row) for row in conn.execute(
            """SELECT goal_id, title, status, priority, next_action, blocker_reason, updated_at
               FROM workflow_goals
               WHERE session_id = ? AND status IN ('active', 'blocked')
               ORDER BY updated_at DESC
               LIMIT 10""",
            (session_id,),
        ).fetchall()
    ]
    workflow_runs = [
        dict(row) for row in conn.execute(
            """SELECT run_id, goal_id, goal, workflow_kind, status, priority, next_action, current_step_key, updated_at
               FROM workflow_runs
               WHERE session_id = ? AND status IN ('open', 'running', 'blocked', 'waiting_approval')
               ORDER BY updated_at DESC
               LIMIT 10""",
            (session_id,),
        ).fetchall()
    ]
    recent_query = " | ".join(
        part for part in [
            str(session_row["task"] or "").strip(),
            str((checkpoint or {}).get("current_goal") or "").strip(),
            str((draft or {}).get("last_context_hint") or "").strip(),
        ] if part
    )
    recent_context = build_pre_action_context(
        query=recent_query,
        session_id=session_id,
        hours=24,
        limit=4,
    ) if recent_query else {"has_matches": False}
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "sid": session_id,
            "task": session_row["task"],
            "client": session_row["session_client"],
            "external_session_id": session_row["external_session_id"],
            "conversation_id": session_row["conversation_id"],
            "started_epoch": session_row["started_epoch"],
            "last_update_epoch": session_row["last_update_epoch"],
            "local_time": session_row["local_time"],
        },
        "checkpoint": dict(checkpoint) if checkpoint else {},
        "latest_diary": dict(diary) if diary else {},
        "diary_draft": dict(draft) if draft else {},
        "recent_context": _safe_packet_payload(recent_context),
        "open_protocol_tasks": protocol_tasks,
        "open_workflow_goals": workflow_goals,
        "open_workflow_runs": workflow_runs,
    }


def _timestamp_to_epoch(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return 0.0


def handle_session_compliance_state(sid: str = "", diary_window_minutes: int = 15) -> str:
    """Return Brain-verifiable session compliance state for Desktop gates."""
    from db import get_last_heartbeat_ts, list_session_correction_requirements

    conn = get_db()
    session_row = _resolve_session_row(conn, sid)
    if not session_row:
        return json.dumps({"ok": False, "error": "session not found"}, ensure_ascii=False, indent=2)

    session_id = str(session_row["sid"])
    now = time.time()
    try:
        clean_diary_window = int(diary_window_minutes or 15)
    except Exception:
        clean_diary_window = 15
    window_seconds = max(60, clean_diary_window * 60)
    last_heartbeat = get_last_heartbeat_ts(session_id) or 0.0
    latest_diary = conn.execute(
        """SELECT id, session_id, created_at, summary, source
           FROM session_diary
           WHERE session_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (session_id,),
    ).fetchone()
    diary_draft = conn.execute(
        """SELECT sid, summary_draft, last_context_hint, heartbeat_count, updated_at
           FROM session_diary_draft
           WHERE sid = ?
           ORDER BY updated_at DESC
           LIMIT 1""",
        (session_id,),
    ).fetchone()
    diary_epoch = _timestamp_to_epoch(latest_diary["created_at"] if latest_diary else 0)
    draft_epoch = _timestamp_to_epoch(diary_draft["updated_at"] if diary_draft else 0)
    last_diaryish = max(diary_epoch, draft_epoch)
    session_started = float(session_row["started_epoch"] or 0)
    session_last_update = float(session_row["last_update_epoch"] or session_started or 0)
    active_age_seconds = max(0.0, now - (session_started or now))
    open_corrections = list_session_correction_requirements(
        session_id=session_id,
        status="open",
        limit=20,
    )
    diary_recent_ok = bool(last_diaryish and (now - last_diaryish) <= window_seconds)
    diary_due = active_age_seconds >= window_seconds and not diary_recent_ok
    close_diary_ok = bool(latest_diary)

    result = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sid": session_id,
        "session": {
            "task": session_row["task"],
            "client": session_row["session_client"],
            "conversation_id": session_row["conversation_id"],
            "external_session_id": session_row["external_session_id"],
            "started_epoch": session_started,
            "last_update_epoch": session_last_update,
        },
        "heartbeat": {
            "last_heartbeat_ts": last_heartbeat,
            "age_seconds": (now - last_heartbeat) if last_heartbeat else None,
            "recorded": bool(last_heartbeat),
        },
        "diary": {
            "window_seconds": window_seconds,
            "latest": dict(latest_diary) if latest_diary else {},
            "draft": dict(diary_draft) if diary_draft else {},
            "last_diary_or_draft_epoch": last_diaryish,
            "recent_ok": diary_recent_ok,
            "due": diary_due,
            "close_ok": close_diary_ok,
        },
        "learning": {
            "open_correction_requirements": len(open_corrections),
            "pending": bool(open_corrections),
            "requirements": open_corrections,
        },
        "obligations": {
            "heartbeat_missing": not bool(last_heartbeat),
            "diary_required": diary_due,
            "learning_required": bool(open_corrections),
            "clean_close_blocked": (not close_diary_ok) or bool(open_corrections),
        },
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def handle_session_portable_context(sid: str = "") -> str:
    """Build a portable handoff packet for another client/runtime."""
    bundle = _session_portability_bundle(sid)
    if not bundle.get("ok"):
        return f"ERROR: {bundle.get('error', 'session not found')}"

    session = bundle["session"]
    checkpoint = bundle.get("checkpoint") or {}
    diary = bundle.get("latest_diary") or {}
    draft = bundle.get("diary_draft") or {}
    lines = [
        "SESSION PORTABILITY PACKET",
        f"SID: {session['sid']}",
        f"Task: {session['task'] or '(none)'}",
        f"Client: {session['client'] or '(unknown)'}",
    ]
    if session.get("external_session_id"):
        lines.append(f"External session: {session['external_session_id']}")

    try:
        from semantic_layers import select_semantic_layers

        semantic = select_semantic_layers(
            query=session.get("task") or "",
            intent_bundle={"intent_kind": "resume_workflow"},
            budget_policy={"budget_tier": "standard", "max_rendered_chars": 1800},
            surface="portable_context",
            scope_hint={"scope_type": "session", "scope_id": session["sid"]},
            requested_layers=["headline", "brief", "next_action", "risks", "source_map"],
        )
        if semantic.get("rendered"):
            lines.extend(["", "Semantic layers:", str(semantic["rendered"])])
    except Exception:
        pass

    if checkpoint:
        lines.extend(
            [
                "",
                "Checkpoint:",
                f"- Goal: {_safe_packet_text(checkpoint.get('current_goal') or checkpoint.get('task') or '(none)')}",
                f"- Next: {_safe_packet_text(checkpoint.get('next_step') or '(none)')}",
                f"- Files: {_safe_packet_text(checkpoint.get('active_files') or '[]')}",
            ]
        )
    if diary:
        lines.extend(
            [
                "",
                "Latest diary:",
                f"- Summary: {_safe_packet_text(diary.get('summary') or '(none)')}",
                f"- Pending: {_safe_packet_text(diary.get('pending') or '(none)')}",
                f"- Context next: {_safe_packet_text(diary.get('context_next') or '(none)')}",
            ]
        )
    elif draft:
        lines.extend(
            [
                "",
                "Diary draft:",
                f"- Summary draft: {_safe_packet_text(draft.get('summary_draft') or '(none)')}",
                f"- Context hint: {_safe_packet_text(draft.get('last_context_hint') or '(none)')}",
            ]
        )
    recent_context = bundle.get("recent_context") or {}
    if recent_context.get("has_matches"):
        lines.extend(["", format_pre_action_context_bundle(_safe_packet_payload(recent_context), compact=True)])

    protocol_tasks = bundle.get("open_protocol_tasks") or []
    if protocol_tasks:
        lines.extend(["", "Open protocol tasks:"])
        for item in protocol_tasks[:5]:
            lines.append(f"- {item['task_id']}: {_safe_packet_text(item['goal'])} [{item['task_type']}/{item['status']}]")

    goals = bundle.get("open_workflow_goals") or []
    if goals:
        lines.extend(["", "Open goals:"])
        for item in goals[:5]:
            lines.append(f"- {item['goal_id']}: {_safe_packet_text(item['title'])} [{item['status']}] -> {_safe_packet_text(item['next_action'] or '(no next action)')}")

    runs = bundle.get("open_workflow_runs") or []
    if runs:
        lines.extend(["", "Open workflows:"])
        for item in runs[:5]:
            lines.append(
                f"- {item['run_id']}: {_safe_packet_text(item['goal'])} [{item['status']}] "
                f"step={item['current_step_key'] or '?'} next={_safe_packet_text(item['next_action'] or '(none)')}"
            )

    return "\n".join(lines)


def handle_session_export_bundle(sid: str = "", path: str = "") -> str:
    """Export a machine-readable session bundle for cross-client handoff."""
    bundle = _session_portability_bundle(sid)
    if not bundle.get("ok"):
        return json.dumps(bundle, ensure_ascii=False)

    session_id = bundle["session"]["sid"]
    safe_bundle = _safe_packet_payload(bundle)
    export_path = Path(path).expanduser() if path else (_session_portability_dir() / f"{session_id}.json")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(safe_bundle, indent=2, ensure_ascii=False) + "\n")
    return json.dumps(
        {
            "ok": True,
            "sid": session_id,
            "path": _safe_packet_text(export_path),
            "open_protocol_tasks": len(bundle.get("open_protocol_tasks") or []),
            "open_workflow_goals": len(bundle.get("open_workflow_goals") or []),
            "open_workflow_runs": len(bundle.get("open_workflow_runs") or []),
        },
        ensure_ascii=False,
    )


def _autodetect_claude_session_id() -> str:
    """Read the Claude Code UUID from the SessionStart coordination file.

    SessionStart hook (see ~/.nexo/hooks/session-start.sh) writes the current
    Claude Code session UUID to ``<NEXO_HOME>/coordination/.claude-session-id``
    so the PreToolUse guardrail can correlate. Any nexo_startup call that
    forgets to pass session_token would otherwise create a session row
    without a UUID, and the strict hook would later block every edit with
    "unknown target" (learning #411 / #403 / #404).

    Mirrors the fallback logic in hook_guardrails._read_claude_session_id_from_coordination
    so both sides of the correlation agree on the same UUID.
    """
    import os as _os
    from pathlib import Path as _Path
    # NEXO_HOME is always set when the MCP server spawned this process; prefer it.
    # When absent (bare scripts), fall back to the default ~/.nexo path. No
    # "check both" path — callers that explicitly set NEXO_HOME to an isolated
    # directory want the isolation respected.
    env = _os.environ.get("NEXO_HOME", "").strip()
    base = _Path(env).expanduser() if env else (_Path.home() / ".nexo")
    path = base / "coordination" / ".claude-session-id"
    try:
        return path.read_text().strip()
    except (FileNotFoundError, OSError):
        return ""


def _read_session_briefing_excerpt(max_lines: int = 6) -> tuple[str, str]:
    """Return a short human-readable excerpt plus the briefing file path.

    SessionStart writes coordination/session-briefing.txt, but startup used to
    ignore it entirely. This helper keeps startup aware of the file without
    dumping the whole briefing into every session banner.
    """
    briefing_path = paths.coordination_dir() / "session-briefing.txt"
    try:
        raw = briefing_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return "", str(briefing_path)

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return "", str(briefing_path)
    excerpt = "\n".join(lines[:max_lines])
    return excerpt, str(briefing_path)


def _read_sleep_health_warning() -> tuple[list[str], str]:
    """Return a compact warning when nightly sleep failed or degraded."""
    health_path = paths.coordination_dir() / "sleep-health.json"
    try:
        payload = json.loads(health_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return [], str(health_path)

    if not isinstance(payload, dict):
        return [], str(health_path)
    status = str(payload.get("status") or "").strip().lower()
    if not status or status == "ok":
        return [], str(health_path)

    date_value = str(payload.get("date") or "").strip()
    error = _safe_packet_text(payload.get("error") or "unknown", max_chars=180)
    lines = [f"status={status} date={date_value or '?'} error={error}"]

    coverage = payload.get("coverage") or {}
    if isinstance(coverage, dict):
        visible = coverage.get("learnings_visible_count")
        total = coverage.get("learnings_total_declared")
        pct = coverage.get("coverage_pct")
        if visible is not None or total is not None or pct is not None:
            lines.append(f"coverage={visible or 0}/{total or 0} ({pct or 0}%)")

    return lines, str(health_path)


def _latest_deep_sleep_synthesis() -> Path | None:
    deep_sleep_dir = paths.operations_dir() / "deep-sleep"
    try:
        candidates = [
            path
            for path in deep_sleep_dir.glob("????-??-??-synthesis.json")
            if path.is_file()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.name[:10], path.stat().st_mtime))


def _latest_deep_sleep_start_packet() -> Path | None:
    deep_sleep_dir = paths.operations_dir() / "deep-sleep"
    try:
        candidates = [
            path
            for path in deep_sleep_dir.glob("????-??-??-agent-start-packet.json")
            if path.is_file()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.name[:10], path.stat().st_mtime))


def _read_agent_start_packet(payload: dict, packet_path: Path) -> tuple[list[str], str]:
    lines: list[str] = []
    date_value = str(payload.get("date") or packet_path.name[:10]).strip()
    summary = str(payload.get("summary") or "").strip()
    if date_value:
        lines.append(f"date={date_value}")
    if summary:
        lines.append(f"summary={_safe_packet_text(summary, max_chars=220)}")

    agenda = payload.get("agenda") or []
    if isinstance(agenda, list):
        for item in agenda[:3]:
            if not isinstance(item, dict):
                continue
            priority = str(item.get("priority") or "?").strip()
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip()
            if title or description:
                text = title if not description else f"{title} - {description}"
                lines.append(f"agenda[{priority}]={_safe_packet_text(text, max_chars=220)}")

    packets = payload.get("context_packets") or []
    if isinstance(packets, list):
        for packet in packets[:2]:
            if not isinstance(packet, dict):
                continue
            topic = str(packet.get("topic") or "context").strip()
            last_state = str(packet.get("last_state") or "").strip()
            if topic or last_state:
                text = topic if not last_state else f"{topic}: {last_state}"
                lines.append(f"context={_safe_packet_text(text, max_chars=240)}")
            files = packet.get("key_files") or []
            if isinstance(files, list) and files:
                rendered_files = ", ".join(str(file) for file in files[:4] if str(file).strip())
                if rendered_files:
                    lines.append(f"files={_safe_packet_text(rendered_files, max_chars=220)}")

    review_items = payload.get("review_items") or []
    if isinstance(review_items, list):
        for item in review_items[:2]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if title:
                lines.append(f"review={_safe_packet_text(title, max_chars=220)}")

    return lines[:10], str(packet_path)


def _read_deep_sleep_start_context() -> tuple[list[str], str]:
    """Summarize the latest Deep Sleep synthesis for new agent sessions."""
    packet_path = _latest_deep_sleep_start_packet()
    if packet_path is not None:
        try:
            packet_payload = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            packet_payload = None
        if isinstance(packet_payload, dict):
            return _read_agent_start_packet(packet_payload, packet_path)

    synthesis_path = _latest_deep_sleep_synthesis()
    if synthesis_path is None:
        return [], str(paths.operations_dir() / "deep-sleep")

    try:
        payload = json.loads(synthesis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], str(synthesis_path)
    if not isinstance(payload, dict):
        return [], str(synthesis_path)

    lines: list[str] = []
    date_value = str(payload.get("date") or synthesis_path.name[:10]).strip()
    summary = str(payload.get("summary") or "").strip()
    if date_value:
        lines.append(f"date={date_value}")
    if summary:
        lines.append(f"summary={_safe_packet_text(summary, max_chars=220)}")

    agenda = payload.get("morning_agenda") or []
    if isinstance(agenda, list):
        for item in agenda[:3]:
            if not isinstance(item, dict):
                continue
            priority = str(item.get("priority") or "?").strip()
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip()
            if title or description:
                text = title if not description else f"{title} - {description}"
                lines.append(f"agenda[{priority}]={_safe_packet_text(text, max_chars=220)}")

    packets = payload.get("context_packets") or []
    if isinstance(packets, list):
        for packet in packets[:2]:
            if not isinstance(packet, dict):
                continue
            topic = str(packet.get("topic") or "context").strip()
            last_state = str(packet.get("last_state") or "").strip()
            if topic or last_state:
                text = topic if not last_state else f"{topic}: {last_state}"
                lines.append(f"context={_safe_packet_text(text, max_chars=240)}")
            files = packet.get("key_files") or []
            if isinstance(files, list) and files:
                rendered_files = ", ".join(str(file) for file in files[:4] if str(file).strip())
                if rendered_files:
                    lines.append(f"files={_safe_packet_text(rendered_files, max_chars=220)}")

    actions = payload.get("actions") or []
    if isinstance(actions, list):
        review_count = 0
        for action in actions:
            if review_count >= 2 or not isinstance(action, dict):
                continue
            if action.get("action_class") != "draft_for_morning":
                continue
            content = action.get("content") or {}
            if isinstance(content, dict):
                title = str(content.get("title") or content.get("description") or "").strip()
            else:
                title = str(content or "").strip()
            if title:
                lines.append(f"review={_safe_packet_text(title, max_chars=220)}")
                review_count += 1

    return lines[:10], str(synthesis_path)


def handle_startup(
    task: str = "Startup",
    claude_session_id: str = "",
    session_token: str = "",
    session_client: str = "",
    session_provider: str = "",
    conversation_id: str = "",
) -> str:
    """Full startup sequence: register, clean, report.

    Args:
        task: Initial task description
        claude_session_id: Legacy alias for the external client session token.
        session_token: External client session token. Claude Code passes its UUID via hooks;
                      other clients may pass a synthetic durable ID when useful.
                      Enables automatic inbox detection when hook-backed clients provide one.
        session_client: Optional client label such as `claude_code` or `codex`.
        session_provider: Optional provider label such as `anthropic` or `openai`.
    """
    _set_interactive_db_timeout()
    sid = _generate_sid()
    startup_warnings: list[str] = []
    cleaned = _safe_interactive("stale-session cleanup", clean_stale_sessions, 0, startup_warnings)
    linked_session_id = (session_token or claude_session_id or "").strip()
    inferred_client = (session_client or "").strip()
    if not inferred_client and claude_session_id and not session_token:
        inferred_client = "claude_code"
    # v6.0.7 hotfix: when the caller did not pass an explicit UUID, fall back to
    # the Claude Code SessionStart UUID written by the SessionStart hook to
    # <NEXO_HOME>/coordination/.claude-session-id. This fixes the "unknown
    # target" strict-hook block observed for operators whose scripts call
    # nexo_startup() without propagating the hook payload (bug revisited
    # after PR #208 — PR #208 covered the hook side; this covers the
    # startup side so every session row is born correlated).
    if not linked_session_id and (not inferred_client or inferred_client == "claude_code"):
        linked_session_id = _autodetect_claude_session_id()
    if not inferred_client and linked_session_id:
        # If we recovered the UUID from the coordination file, the only
        # client that writes there is Claude Code.
        inferred_client = "claude_code"
    inferred_provider = normalize_provider_key(session_provider) or client_to_provider(inferred_client)
    conversation = str(conversation_id or "").strip()
    conflicts = []
    if conversation:
        def _load_conflicts():
            cutoff = now_epoch() - SESSION_STALE_SECONDS
            conn = get_db()
            rows = conn.execute(
                """
                SELECT sid, task, last_update_epoch, external_session_id, session_client, session_provider
                FROM sessions
                WHERE conversation_id = ? AND last_update_epoch > ?
                ORDER BY last_update_epoch DESC
                """,
                (conversation, cutoff),
            ).fetchall()
            return [dict(row) for row in rows if row["sid"] != sid]

        conflicts = _safe_interactive("conversation conflict lookup", _load_conflicts, [], startup_warnings)
    registered = _safe_interactive(
        "session registration",
        lambda: register_session(
            sid,
            task,
            claude_session_id=linked_session_id if inferred_client == "claude_code" else "",
            external_session_id=linked_session_id,
            session_client=inferred_client,
            session_provider=inferred_provider,
            conversation_id=conversation,
        ),
        None,
        startup_warnings,
    )
    memory_maintenance = None
    try:
        backfill_limit = int(os.environ.get("NEXO_MEMORY_STARTUP_BACKFILL_LIMIT", "0") or "0")
    except Exception:
        backfill_limit = 0
    if _env_flag("NEXO_MEMORY_MAINTENANCE_IN_STARTUP", default=False) or backfill_limit > 0:
        try:
            memory_maintenance = maintain_memory_observations(
                process_limit=int(os.environ.get("NEXO_MEMORY_STARTUP_PROCESS_LIMIT", "20") or "20"),
                retry_failed=True,
                backfill_limit=backfill_limit,
            )
        except Exception as exc:
            memory_maintenance = {"ok": False, "error": str(exc)}
    else:
        memory_maintenance = {"ok": True, "skipped": True}
    # v43 hotfix: also register in session_claude_aliases so multi-
    # conversation NEXO Desktop spawns (each with its own claude UUID)
    # resolve to the same NEXO sid on every PreToolUse hook lookup.
    # Backward-compatible: if the alias table does not yet exist (older
    # DB), register_claude_session_alias returns False silently and
    # the legacy sessions.claude_session_id column stays authoritative.
    if linked_session_id and inferred_client == "claude_code":
        try:
            from hook_guardrails import register_claude_session_alias
            from db import get_db as _get_db
            register_claude_session_alias(_get_db(), sid, linked_session_id)
        except Exception:
            # Never let alias registration failures block startup.
            pass
    if registered:
        _start_keepalive(sid)
    active = _safe_interactive("active-session lookup", get_active_sessions, [], startup_warnings)
    other_sessions = [s for s in active if s["sid"] != sid]
    inbox = _safe_interactive("inbox lookup", lambda: get_inbox(sid), [], startup_warnings)

    lines = [f"SID: {sid}"]
    if conversation:
        lines.append(f"CONVERSATION_ID: {conversation}")

    if cleaned > 0:
        lines.append(f"Cleaned {cleaned} stale sessions.")

    if startup_warnings:
        lines.append("")
        lines.append("STARTUP DEGRADED:")
        for warning in startup_warnings[:4]:
            lines.append(f"  {warning}")
        lines.append("  Continue responding; retry nexo_heartbeat shortly for full context.")

    if other_sessions:
        lines.append("")
        lines.append("ACTIVE SESSIONS:")
        for s in other_sessions:
            age = _format_age(s["last_update_epoch"])
            lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    else:
        lines.append("No other active sessions.")

    if conflicts:
        lines.append("")
        lines.append("CONVERSATION CONFLICT:")
        for row in conflicts[:3]:
            age = _format_age(row["last_update_epoch"])
            lines.append(
                f"  {row['sid']} ({age}) — {row['task']} "
                f"[provider={row.get('session_provider') or '?'} client={row.get('session_client') or '?'} external={row.get('external_session_id') or '?'}]"
            )

    if memory_maintenance and not memory_maintenance.get("ok"):
        lines.append("")
        lines.append("MEMORY OBSERVATIONS:")
        lines.append(f"  Maintenance warning: {memory_maintenance.get('error') or memory_maintenance}")

    if inbox:
        lines.append("")
        lines.append("PENDING MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            lines.append(f"  [{m['from_sid']}] ({age}): {_safe_packet_text(m['text'])}")

    briefing_excerpt, briefing_path = _read_session_briefing_excerpt()
    if briefing_excerpt:
        lines.append("")
        lines.append("SESSION BRIEFING:")
        for raw_line in briefing_excerpt.splitlines():
            lines.append(f"  {_safe_packet_text(raw_line)}")
        lines.append(f"  Full briefing: {_safe_packet_text(briefing_path)}")

    sleep_health, sleep_health_path = _read_sleep_health_warning()
    if sleep_health:
        lines.append("")
        lines.append("SLEEP HEALTH:")
        for raw_line in sleep_health:
            lines.append(f"  {_safe_packet_text(raw_line)}")
        lines.append(f"  Full health: {_safe_packet_text(sleep_health_path)}")

    start_context, start_context_path = _read_deep_sleep_start_context()
    if start_context:
        lines.append("")
        lines.append("DEEP SLEEP CONTEXT:")
        for raw_line in start_context:
            lines.append(f"  {_safe_packet_text(raw_line)}")
        lines.append(f"  Full synthesis: {_safe_packet_text(start_context_path)}")

    try:
        from memory_layer_audit import audit_memory_layers, format_memory_layer_warnings

        memory_warnings = format_memory_layer_warnings(audit_memory_layers(max_warnings=4))
        if memory_warnings:
            lines.append("")
            lines.append("MEMORY LAYER CHECK:")
            for raw_line in memory_warnings:
                lines.append(f"  {raw_line}")
    except Exception:
        pass

    # Check LaunchAgent health (macOS only)
    la_warnings = _check_launchagents()
    if la_warnings:
        lines.append("")
        lines.append("⚠ LAUNCHAGENT HEALTH:")
        for w in la_warnings:
            lines.append(f"  {w}")

    return "\n".join(lines)


def _check_launchagents() -> list[str]:
    """Compare on-disk plists with what launchctl has loaded. macOS only."""
    import platform
    if platform.system() != "Darwin":
        return []

    import os, subprocess, plistlib, glob

    plist_dir = os.path.expanduser("~/Library/LaunchAgents")
    warnings = []

    def _stderr_text(result, fallback: str) -> str:
        stderr = getattr(result, "stderr", "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return stderr.strip() or fallback

    for plist_path in glob.glob(os.path.join(plist_dir, "com.nexo.*.plist")):
        label = os.path.basename(plist_path).replace(".plist", "")
        try:
            with open(plist_path, "rb") as f:
                disk = plistlib.load(f)
            disk_args = disk.get("ProgramArguments", [])

            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                repair = subprocess.run(
                    ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist_path],
                    capture_output=True, text=True, timeout=5,
                )
                if repair.returncode == 0:
                    warnings.append(f"{label}: AUTO-REPAIRED (was not loaded, reloaded from disk)")
                else:
                    warnings.append(
                        f"{label}: REPAIR FAILED — "
                        f"{_stderr_text(repair, 'not loaded (plist exists on disk)')}"
                    )
                continue

            # Parse loaded ProgramArguments from launchctl output
            loaded_args = []
            in_args = False
            for line in result.stdout.splitlines():
                if '"ProgramArguments"' in line:
                    in_args = True
                    continue
                if in_args:
                    line = line.strip().rstrip(";")
                    if line == ");":
                        break
                    if line.startswith('"') and line.endswith('"'):
                        loaded_args.append(line.strip('"'))

            if loaded_args and disk_args and loaded_args != disk_args:
                # Check if loaded path points to /tmp or nonexistent path
                stale = any("/tmp/" in a or not os.path.exists(a) for a in loaded_args if "/" in a)
                if stale:
                    bootout = subprocess.run(
                        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if bootout.returncode != 0:
                        warnings.append(
                            f"{label}: REPAIR FAILED — "
                            f"{_stderr_text(bootout, 'could not unload stale launchd entry')}"
                        )
                        continue
                    repair = subprocess.run(
                        ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist_path],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if repair.returncode == 0:
                        warnings.append(f"{label}: AUTO-REPAIRED (was pointing to stale/tmp path, reloaded from disk)")
                    else:
                        warnings.append(
                            f"{label}: REPAIR FAILED — "
                            f"{_stderr_text(repair, 'could not reload stale plist from disk')}"
                        )
                else:
                    warnings.append(f"{label}: loaded args differ from disk plist")
        except Exception:
            continue

    return warnings


def handle_heartbeat(sid: str, task: str, context_hint: str = '') -> str:
    """Update session, check inbox + questions. Lightweight — no embeddings, no RAG.

    For cognitive features (sentiment, trust, RAG), use dedicated tools on-demand:
    - nexo_cognitive_sentiment (sentiment detection)
    - nexo_cognitive_trust (trust adjustment)
    - nexo_cognitive_retrieve / nexo_recall (memory retrieval)
    - nexo_context_packet (area-specific learnings)

    Args:
        sid: Session ID
        task: Current task description
        context_hint: Optional — stored for diary draft context and used for recent 24h continuity lookup.

    OpenTelemetry: emits an ai.tool.nexo_heartbeat span when OTEL is
    enabled (Fase 5 item 2). The span carries sid, task, and the
    context_hint length so dashboards can correlate heartbeat cadence
    with workload. No-op when telemetry is off.
    """
    from observability import tool_span
    with tool_span(
        "nexo_heartbeat",
        attributes={
            "nexo.session.id": sid,
            "nexo.heartbeat.task": (task or "")[:200],
            "nexo.heartbeat.context_hint_length": len(context_hint or ""),
        },
    ):
        return _handle_heartbeat_inner(sid, task, context_hint)


def _handle_heartbeat_inner(sid: str, task: str, context_hint: str = '') -> str:
    """Inner body of handle_heartbeat — wrapped by tool_span above."""
    from db import get_db, update_last_heartbeat_ts
    from mcp_write_queue import drain_write_queue, enqueue_write

    _set_interactive_db_timeout()
    heartbeat_warnings: list[str] = []
    _safe_interactive("mcp write queue drain", lambda: drain_write_queue(limit=10), None, None)
    mandate_state = None
    if context_hint:
        try:
            from autonomy_mandate import maybe_ingest_from_text

            mandate_state = maybe_ingest_from_text(
                context_hint,
                session_id=sid,
                source="heartbeat",
                classifier=(lambda **_: False),
            )
        except Exception:
            mandate_state = None
    if mandate_state is None:
        try:
            from autonomy_mandate import load_state

            mandate_state = load_state()
        except Exception:
            mandate_state = None

    def _commit_core_heartbeat() -> bool:
        update_session(sid, task)
        # v6.0.1 — stamp last_heartbeat_ts so downstream gates can verify
        # that this user turn had a real heartbeat.
        update_last_heartbeat_ts(sid)
        return True

    heartbeat_committed = bool(
        _safe_interactive("session heartbeat update", _commit_core_heartbeat, False, heartbeat_warnings)
    )
    if not heartbeat_committed:
        queued = enqueue_write(
            "heartbeat_update",
            {"sid": sid, "task": task, "heartbeat_ts": time.time()},
            priority="high",
        )
        if queued.get("accepted"):
            heartbeat_warnings.append(
                f"session heartbeat update: accepted in durable queue ({queued.get('writeId')})"
            )

    # Temporal anchor — surface authoritative UTC time so clients never drift
    # on date/day-of-week across long sessions. Neutral ISO-8601, no locale,
    # no timezone assumption: clients format per operator preferences.
    _now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    parts = [f"NOW_UTC: {_now_iso}", f"OK: {sid} — {task}"]
    if heartbeat_warnings:
        parts.append("")
        parts.append("HEARTBEAT DEGRADED:")
        for warning in heartbeat_warnings[:3]:
            parts.append(f"  {warning}")
        parts.append("  Continue with the user request; context will catch up on a later heartbeat.")
        return "\n".join(parts)

    inbox = _safe_interactive("inbox lookup", lambda: get_inbox(sid), [], heartbeat_warnings)
    if inbox:
        parts.append("")
        parts.append("MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            parts.append(f"  [{m['from_sid']}] ({age}): {_safe_packet_text(m['text'])}")

    questions = _safe_interactive("pending-question lookup", lambda: get_pending_questions(sid), [], heartbeat_warnings)
    if questions:
        parts.append("")
        parts.append("PENDING QUESTIONS (respond with nexo_answer):")
        for q in questions:
            age = _format_age(q["created_epoch"])
            parts.append(f"  {q['qid']} de {q['from_sid']} ({age}): {_safe_packet_text(q['question'])}")

    recent_query = (context_hint or task or "").strip()
    if recent_query:
        bundle = _safe_interactive_timed(
            "recent context lookup",
            lambda: build_pre_action_context(
                query=recent_query,
                session_id=sid,
                hours=24,
                limit=4,
            ),
            {},
            heartbeat_warnings,
            timeout_ms=900,
        )
        if bundle.get("has_matches"):
            parts.append("")
            parts.append(format_pre_action_context_bundle(_safe_packet_payload(bundle), compact=True))
        if _heartbeat_heavy_feature_enabled("NEXO_HEARTBEAT_LOCAL_CONTEXT", default=False) and append_local_context_evidence is not None:
            local_rendered = _safe_interactive_timed(
                "local context lookup",
                lambda: append_local_context_evidence("", recent_query, limit=4).strip(),
                "",
                heartbeat_warnings,
                timeout_ms=500,
            )
            if local_rendered:
                parts.append("")
                parts.append(local_rendered)

    try:
        from autonomy_mandate import format_execution_latch_notice

        latch_notice = format_execution_latch_notice(sid, state=mandate_state)
        if latch_notice:
            parts.append("")
            parts.append(latch_notice)
    except Exception:
        pass

    # Incremental diary draft — accumulate every heartbeat, full UPSERT every 5
    _hb_count = 0  # Hoisted for Layer 3 DIARY_OVERDUE signal
    try:
        import json as _json
        from db import get_diary_draft, upsert_diary_draft

        draft = get_diary_draft(sid)
        hb_count = (draft["heartbeat_count"] + 1) if draft else 1
        _hb_count = hb_count  # Copy to outer scope for Layer 3

        existing_tasks = _json.loads(draft["tasks_seen"]) if draft else []
        if task and task not in existing_tasks:
            existing_tasks.append(task)

        _conn = get_db()
        if hb_count % 5 == 0 or hb_count == 1:
            change_rows = _conn.execute(
                "SELECT id FROM change_log WHERE session_id = ? ORDER BY id", (sid,)
            ).fetchall()
            change_ids = [r["id"] for r in change_rows]

            decision_rows = _conn.execute(
                "SELECT id FROM decisions WHERE session_id = ? ORDER BY id", (sid,)
            ).fetchall()
            decision_ids = [r["id"] for r in decision_rows]

            summary = f"Session tasks: {', '.join(existing_tasks[-10:])}"
            upsert_diary_draft(
                sid=sid,
                tasks_seen=_json.dumps(existing_tasks),
                change_ids=_json.dumps(change_ids),
                decision_ids=_json.dumps(decision_ids),
                last_context_hint=context_hint[:300] if context_hint else '',
                heartbeat_count=hb_count,
                summary_draft=summary,
            )
        else:
            upsert_diary_draft(
                sid=sid,
                tasks_seen=_json.dumps(existing_tasks),
                change_ids=draft["change_ids"] if draft else '[]',
                decision_ids=draft["decision_ids"] if draft else '[]',
                last_context_hint=context_hint[:300] if context_hint else (draft["last_context_hint"] if draft else ''),
                heartbeat_count=hb_count,
                summary_draft=draft["summary_draft"] if draft else f"Session task: {task}",
            )
    except Exception:
        try:
            enqueue_write(
                "diary_draft_upsert",
                {
                    "sid": sid,
                    "tasks_seen": json.dumps([task] if task else []),
                    "change_ids": "[]",
                    "decision_ids": "[]",
                    "last_context_hint": context_hint[:300] if context_hint else "",
                    "heartbeat_count": max(1, int(_hb_count or 1)),
                    "summary_draft": f"Session task: {task}",
                },
                priority="normal",
            )
        except Exception:
            pass  # Draft accumulation is best-effort, never block heartbeat

    # Update session checkpoint with current goal (lightweight, every heartbeat)
    try:
        save_checkpoint(
            sid=sid,
            task=task,
            current_goal=context_hint[:300] if context_hint else task,
        )
    except Exception:
        try:
            enqueue_write(
                "session_checkpoint",
                {
                    "sid": sid,
                    "task": task,
                    "current_goal": context_hint[:300] if context_hint else task,
                },
                priority="normal",
            )
        except Exception:
            pass  # Checkpoint update is best-effort

    try:
        context_payload = {
            "event_type": "heartbeat",
            "title": task[:160],
            "summary": (context_hint or task)[:600],
            "body": context_hint[:1600] if context_hint else "",
            "context_key": f"session:{sid}",
            "context_title": task[:160],
            "context_summary": (context_hint or task)[:600],
            "context_type": "session_topic",
            "state": "active",
            "owner": "session",
            "actor": sid,
            "source_type": "heartbeat",
            "source_id": sid,
            "session_id": sid,
            "metadata": {"task": task[:160]},
            "ttl_hours": 24,
        }
        capture_context_event(**context_payload)
    except Exception:
        try:
            enqueue_write("context_event_capture", context_payload, priority="low")
        except Exception:
            pass

    # ── Drive/Curiosity: detect signals from context_hint (best-effort) ──
    try:
        if _heartbeat_heavy_feature_enabled("NEXO_DRIVE_IN_HEARTBEAT", default=False) and context_hint and len(context_hint.strip()) >= 15:
            from tools_drive import detect_drive_signal as _detect_drive
            _drive_allow_llm = _env_flag("NEXO_DRIVE_LLM_IN_HEARTBEAT", default=False)
            _drive_result = _safe_interactive_timed(
                "drive detection",
                lambda: _detect_drive(
                    context_hint,
                    source="heartbeat",
                    source_id=sid,
                    allow_llm=_drive_allow_llm,
                ),
                None,
                None,
                timeout_ms=350,
            )
            if _drive_result:
                # Check for READY signals relevant to current area
                from db import get_drive_signals as _get_drive
                _ready = _get_drive(status="ready", limit=3)
                if _ready:
                    parts.append("")
                    parts.append(f"DRIVE: {len(_ready)} mature signal(s) ready for investigation")
                    for _ds in _ready[:2]:
                        parts.append(f"  [{_ds['id']}] {_ds['signal_type']}: {_ds['summary'][:80]}")
    except Exception:
        pass  # Drive detection is best-effort, never block heartbeat

    # ── Layer 3: DIARY_OVERDUE signal based on heartbeat count + time ──
    conn = None
    row = _safe_interactive(
        "session age lookup",
        lambda: get_db().execute("SELECT started_epoch FROM sessions WHERE sid = ?", (sid,)).fetchone(),
        None,
        None,
    )
    if row:
        age_seconds = now_epoch() - row["started_epoch"]
        has_diary = _safe_interactive("diary lookup", lambda: check_session_has_diary(sid), True, None)

        # DIARY_OVERDUE: >10 heartbeats OR >30 minutes, without a diary
        if not has_diary and (_hb_count > 10 or age_seconds >= 1800):
            parts.append("")
            parts.append(
                render_core_prompt(
                    "heartbeat-diary-overdue",
                    heartbeat_count=_hb_count,
                    active_minutes=int(age_seconds / 60),
                )
            )

    # Guard check reminder: if context_hint mentions code editing and no guard_check this session
    if context_hint and _hint_suggests_code_edit(context_hint):
        try:
            conn = conn or get_db()
            guard_used = conn.execute(
                "SELECT COUNT(*) FROM guard_log WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            if guard_used == 0:
                parts.append("")
                parts.append(render_core_prompt("heartbeat-guard-reminder"))
        except Exception:
            pass  # guard_log table may not exist in older installs

    if context_hint and _hint_suggests_correction(context_hint):
        try:
            from db import (
                create_protocol_debt,
                list_protocol_debts,
                record_session_correction_requirement,
            )

            record_session_correction_requirement(
                sid,
                context_hint,
                source="heartbeat",
            )
            if not _recent_learning_capture_exists(conn, sid, window_seconds=300):
                existing_debt = list_protocol_debts(
                    status="open",
                    session_id=sid,
                    debt_type="missing_learning_after_correction",
                    limit=1,
                )
                if not existing_debt:
                    create_protocol_debt(
                        sid,
                        "missing_learning_after_correction",
                        severity="error",
                        evidence=(
                            "Detected user correction in heartbeat context. "
                            "A durable nexo_learning_add is required before "
                            "nexo_task_close or nexo_stop may close this session."
                        ),
                    )
                parts.append("")
                parts.append(render_core_prompt("heartbeat-learning-reminder"))
                parts.append(
                    "LEARNING REQUIRED: call nexo_learning_add for this correction before nexo_task_close or nexo_stop."
                )
        except Exception:
            pass  # Best-effort reminder only

    # Adaptive mode auto-fire from heartbeat. Closes the audit-followup
    # adaptive_log circuit: previously the table only filled when an agent
    # explicitly called nexo_adaptive_mode with signals, which almost never
    # happened in normal flow. Result: the learn_weights() pipeline (Fase 2
    # item 4) had zero training data and the shadow→active graduation
    # never fired. Now every heartbeat derives the 6 signals from the
    # context_hint and task fields and runs compute_mode, which writes one
    # adaptive_log row per heartbeat. Wrapped in best-effort try/except so
    # a failure here cannot block the heartbeat itself.
    try:
        if _heartbeat_heavy_feature_enabled("NEXO_HEARTBEAT_ADAPTIVE_MODE", default=True) and context_hint and len(context_hint.strip()) >= 5:
            def _compute_adaptive_mode():
                from plugins.adaptive_mode import compute_mode

                lowered = context_hint.lower()
                negative_markers = ("error", "fallo", "mal", "bloque", "lento", "problema", "broken", "failed")
                positive_markers = ("ok", "bien", "gracias", "resuelto", "success")
                if any(marker in lowered for marker in negative_markers):
                    vibe_label = "negative"
                    vibe_intensity = 0.7
                elif any(marker in lowered for marker in positive_markers):
                    vibe_label = "positive"
                    vibe_intensity = 0.6
                else:
                    vibe_label = "neutral"
                    vibe_intensity = 0.5
                # Heuristic signal derivation — same fields the manual tool
                # would feed compute_mode with, just synthesized from context.
                return compute_mode(
                    vibe=vibe_label,
                    vibe_intensity=vibe_intensity,
                    recent_corrections=0,  # heartbeat does not see explicit corrections
                    user_msg_length=len(context_hint),
                    context_hint=context_hint[:300],
                    tool_had_error=False,  # heartbeat is post-tool, not pre-tool
                )

            _safe_interactive(
                "adaptive mode",
                _compute_adaptive_mode,
                None,
                None,
            )
    except Exception:
        pass  # Best-effort, never block heartbeat

    # Protocol debt surfacing: if this session has open debts, warn so the
    # agent can resolve them with nexo_protocol_debt_resolve before claiming
    # any task complete. Mirrors task_open / task_close behavior so that
    # protocol debt is visible at every protocol touchpoint, not only at
    # task boundaries.
    try:
        from db import list_protocol_debts
        session_debts = list_protocol_debts(status="open", session_id=sid, limit=5)
        if session_debts:
            error_count = sum(1 for d in session_debts if d.get("severity") == "error")
            icon = "⛔" if error_count else "⚠"
            parts.append("")
            parts.append(
                f"{icon} PROTOCOL DEBT: {len(session_debts)} open debt(s) in this session"
                + (f" ({error_count} error)" if error_count else "")
                + "."
            )
            for debt in session_debts[:3]:
                evidence = (debt.get("evidence") or "").strip().replace("\n", " ")
                parts.append(
                    f"  [{debt.get('id')}] {debt.get('debt_type', '?')}"
                    f" ({debt.get('severity', '?')}): {evidence[:100]}"
                )
            parts.append(
                "  Resolve with nexo_protocol_debt_resolve before claiming task complete."
            )
    except Exception:
        pass  # Best-effort surfacing, never block heartbeat

    return "\n".join(parts)


def handle_context_packet(area: str, files: str = "") -> str:
    """Build a context packet for a specific area/project — designed for subagent injection.

    Returns: relevant learnings + last 5 changes + active followups + key preferences
    for the given area. Use this before delegating to a subagent.

    Args:
        area: Project/area name (e.g., 'ecommerce', 'shopify', 'backend', 'mobile-app', 'nexo')
        files: Optional comma-separated file paths for guard check
    """
    from db import get_db
    parts = []

    # 1. Learnings for this area (from nexo.db)
    conn = get_db()
    learnings = conn.execute(
        "SELECT id, title, content FROM learnings WHERE category LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT 15",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if learnings:
        parts.append("## KNOWN ERRORS — DO NOT REPEAT")
        for l in learnings:
            parts.append(f"  L#{l['id']}: {_safe_packet_text(l['title'])}")
            # First 200 chars of content
            parts.append(f"    {_safe_packet_text(l['content'], max_chars=200)}")
        parts.append("")

    # 2. Last 5 changes in this area
    changes = conn.execute(
        "SELECT id, files, what_changed, why FROM change_log WHERE files LIKE ? OR what_changed LIKE ? ORDER BY id DESC LIMIT 5",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if changes:
        parts.append("## RECENT CHANGES")
        for c in changes:
            parts.append(f"  C#{c['id']}: {_safe_packet_text(c['what_changed'], max_chars=150)}")
            if c['why']:
                parts.append(f"    Why: {_safe_packet_text(c['why'], max_chars=100)}")
        parts.append("")

    # 3. Active followups for this area
    from db import followup_lifecycle_lane, normalize_followup_status

    followup_rows = conn.execute(
        "SELECT id, description, date, verification, status, owner FROM followups "
        "WHERE (description LIKE ? OR verification LIKE ?) ORDER BY date ASC LIMIT 50",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    followups = []
    for row in followup_rows:
        item = dict(row)
        item["status"] = normalize_followup_status(item.get("status"))
        if followup_lifecycle_lane(item) == "active":
            followups.append(item)
        if len(followups) >= 10:
            break
    if followups:
        parts.append("## ACTIVE FOLLOWUPS")
        for f in followups:
            parts.append(f"  {f['id']}: {_safe_packet_text(f['description'], max_chars=150)} (date: {_safe_packet_text(f['date'], max_chars=80)})")
        parts.append("")

    # 4. Preferences related to this area
    try:
        prefs = conn.execute(
            "SELECT key, value FROM preferences WHERE key LIKE ? OR value LIKE ? LIMIT 10",
            (f"%{area}%", f"%{area}%")
        ).fetchall()
        if prefs:
            parts.append("## PREFERENCES")
            for p in prefs:
                parts.append(f"  {_safe_packet_text(p['key'], max_chars=120)}: {_safe_packet_text(p['value'], max_chars=150)}")
            parts.append("")
    except Exception:
        pass

    # 5. Recent hot context in the last 24h
    try:
        hot_bundle = build_pre_action_context(query=area, hours=24, limit=4)
        if hot_bundle.get("has_matches"):
            parts.append("## RECENT HOT CONTEXT (24H)")
            parts.append(format_pre_action_context_bundle(_safe_packet_payload(hot_bundle), compact=True))
            parts.append("")
    except Exception:
        pass

    # 6. Cognitive memories for this area
    try:
        import cognitive
        results = cognitive.search(
            query_text=area,
            top_k=5,
            min_score=0.55,
            stores="ltm",
            rehearse=False,
        )
        if results:
            parts.append("## RELEVANT COGNITIVE MEMORIES")
            for r in results:
                title = r.get("source_title") or str(r.get("content") or "")[:80]
                parts.append(f"  [{_safe_packet_text(r['source_type'], max_chars=80)}] {_safe_packet_text(title, max_chars=120)}")
            parts.append("")
    except Exception:
        pass

    # 7. Data flow tracing requirement (mandatory for all subagents)
    parts.append("## MANDATORY RULE: DATA FLOW TRACING")
    parts.append("BEFORE modifying any file or data, answer these 3 questions:")
    parts.append("  1. WHO PRODUCES this data? (which function/cron/endpoint generates it)")
    parts.append("  2. WHO CONSUMES this data? (what other files/functions read it)")
    parts.append("  3. WHAT BREAKS if I change it? (downstream effects)")
    parts.append("If you can't answer all 3 → READ the code that produces and consumes BEFORE touching.")
    parts.append("If you still can't → STOP and return the question. Do NOT guess.")
    parts.append("")

    if not parts:
        return f"No context found for area '{area}'. The subagent will start with no project-specific knowledge."

    header = f"CONTEXT PACKET — {area.upper()}\n{'='*40}\n\n"
    footer = f"\n{'='*40}\nINSTRUCTION: If you're not 100% sure about a fact, STOP and return the question. Do NOT invent."
    return header + "\n".join(parts) + footer


def _load_session_tone() -> str | None:
    """Load session-tone.json generated by Deep Sleep and format as startup guidance.

    Returns a human-readable instruction block that tells the agent HOW to behave
    emotionally in this session, based on yesterday's analysis.
    """
    tone_file = paths.operations_dir() / "session-tone.json"

    if not tone_file.exists():
        return None

    try:
        import json
        tone = json.loads(tone_file.read_text())
    except Exception:
        return None

    # Don't return stale tone (>48h old)
    from datetime import datetime, timedelta
    tone_date = tone.get("date", "")
    if tone_date:
        try:
            td = datetime.strptime(tone_date, "%Y-%m-%d")
            if datetime.now() - td > timedelta(hours=48):
                return None
        except ValueError:
            pass

    parts = ["SESSION TONE (from Deep Sleep analysis):"]

    mood = tone.get("mood_yesterday", 0.5)
    approach = tone.get("approach", "neutral")
    parts.append(f"  Yesterday mood: {mood:.0%} | Approach today: {approach}")

    if tone.get("acknowledge_mistakes"):
        mistakes = tone.get("mistakes_to_own", [])
        parts.append(f"  ⚠ OWN YOUR MISTAKES: You made errors yesterday. Acknowledge them specifically:")
        for m in mistakes[:3]:
            parts.append(f"    - {m[:100]}")
        parts.append("  Show what you learned. Don't just apologize — demonstrate improvement.")

    if tone.get("motivational"):
        if mood < 0.4:
            parts.append("  💪 USER HAD A TOUGH DAY: Be supportive. Lighter start. Acknowledge difficulty.")
        else:
            parts.append("  🚀 USER HAD A GREAT DAY: Reinforce momentum. Reference wins. Push ambitious goals.")

    if tone.get("reduce_load"):
        parts.append("  📉 REDUCE LOAD: Don't overwhelm with tasks. Propose 1-2 key things, not a full agenda.")

    ctx = tone.get("suggested_greeting_context", "")
    if ctx:
        parts.append(f"  Context: {ctx.strip()}")

    return "\n".join(parts) if len(parts) > 1 else None


def handle_smart_startup_query() -> str:
    """Generate and execute a composite cognitive query from pending followups + diary topics + reminders.

    Called during startup to pre-load the most relevant context for this session.
    Returns cognitive memories that match the current operational state.
    """
    from db import get_db
    conn = get_db()
    query_parts = []
    sent_email_block = ""
    try:
        from email_sent_events import format_recent_sent_email_block

        sent_email_block = format_recent_sent_email_block(hours=24, limit=8)
    except Exception:
        sent_email_block = ""

    # 1. Pending followups (what NEXO needs to do)
    try:
        from db import followup_lifecycle_snapshot

        active_followups = (followup_lifecycle_snapshot(limit=500).get("lanes") or {}).get("active", [])[:5]
        for f in active_followups:
            query_parts.append(str(f.get("description") or "")[:100])
    except Exception:
        followups = conn.execute(
            "SELECT description FROM followups WHERE status = 'PENDING' ORDER BY date ASC LIMIT 5"
        ).fetchall()
        for f in followups:
            query_parts.append(f['description'][:100])

    # 2. Due reminders (what the user needs to know)
    reminders = conn.execute(
        "SELECT description FROM reminders WHERE status = 'PENDING' AND date <= date('now', '+1 day') ORDER BY date ASC LIMIT 5"
    ).fetchall()
    for r in reminders:
        query_parts.append(r['description'][:100])

    # 3. Last session diary topics
    try:
        last_diary = conn.execute(
            "SELECT summary FROM session_diary ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_diary and last_diary['summary']:
            query_parts.append(last_diary['summary'][:200])
    except Exception:
        pass

    if not query_parts:
        if sent_email_block:
            return sent_email_block
        return "No pending context to pre-load."

    # Search per-part to avoid diffuse centroid that matches everything
    try:
        import cognitive
        all_results = []
        seen_ids = set()
        for part in query_parts[:6]:
            part_results = cognitive.search(
                query_text=part,
                top_k=3,
                min_score=0.6,
                stores="both",
                rehearse=False,  # Don't inflate strength on startup
            )
            for r in part_results:
                key = (r["store"], r["id"])
                if key not in seen_ids:
                    seen_ids.add(key)
                    all_results.append(r)
        # Sort by score descending, take top 10
        results = sorted(all_results, key=lambda x: x["score"], reverse=True)[:10]
        composite_query = " | ".join(query_parts[:6])
        if not results:
            if sent_email_block:
                return "Smart startup query: no relevant memories found.\n\n" + sent_email_block
            return "Smart startup query: no relevant memories found."

        lines = [f"SMART STARTUP — {len(results)} memories pre-loaded from composite query:"]
        lines.append(f"Query: {composite_query[:200]}...")
        lines.append("")
        lines.append(cognitive.format_results(results))

        try:
            hot_bundle = build_pre_action_context(query=composite_query, hours=24, limit=4)
            if hot_bundle.get("has_matches"):
                lines.append("")
                lines.append(format_pre_action_context_bundle(_safe_packet_payload(hot_bundle), compact=True))
        except Exception:
            pass

        if sent_email_block:
            lines.append("")
            lines.append(sent_email_block)

        # Session tone from Deep Sleep (emotional intelligence layer)
        tone = _load_session_tone()
        if tone:
            lines.append("")
            lines.append(tone)

        # Toolbox reminder: skills + behavioral learnings count
        toolbox = _toolbox_summary(conn)
        if toolbox:
            lines.append("")
            lines.append(toolbox)

        return "\n".join(lines)
    except Exception as e:
        return f"Smart startup query error: {e}"


def _hint_suggests_code_edit(hint: str) -> bool:
    """Check if a heartbeat context_hint suggests the agent is editing code."""
    hint_lower = hint.lower()
    edit_signals = ['edit', 'fix', 'patch', 'modify', 'implement', 'refactor', 'add function',
                    'change code', 'update script', 'write code', '.py', '.js', '.ts', '.php',
                    'commit', 'arregl', 'modific', 'implement', 'correg']
    return any(signal in hint_lower for signal in edit_signals)


def _hint_suggests_correction(hint: str, *, correction_detector=None) -> bool:
    """Detect user-correction intent in a heartbeat context hint.

    Heartbeat is on the visible-turn critical path. Use cheap textual signals
    first and only call the semantic detector when explicitly enabled; Desktop
    has its own enforcement loop for richer correction tracking.
    """
    text = (hint or "").strip()
    if not text:
        return False
    hint_lower = text.lower()
    correction_signals = [
        "that's wrong",
        "that is wrong",
        "wrong approach",
        "not like that",
        "fix this",
        "fix it",
        "está mal",
        "esta mal",
        "mal hecho",
        "incorrecto",
        "te equivocas",
        "te has equivocado",
        "lo hiciste mal",
        "no era eso",
        "corrige esto",
        "corrígelo",
        "corrigelo",
        "ya te dije",
        "otra vez el mismo",
        "de nuevo el mismo",
        "no deberías",
        "no deberias",
        "shouldn't have",
        "should not have",
    ]
    if any(signal in hint_lower for signal in correction_signals):
        return True

    detector = correction_detector if correction_detector is not None else _detect_correction_semantic
    semantic_allowed = correction_detector is not None or _heartbeat_heavy_feature_enabled(
        "NEXO_HEARTBEAT_SEMANTIC_CORRECTION",
        default=False,
    )
    if detector is not None and semantic_allowed:
        try:
            return bool(detector(text))
        except Exception:
            return False
    return False


def _recent_learning_capture_exists(conn, sid: str, window_seconds: int = 300) -> bool:
    """Check whether a recent learning was captured manually or via protocol task close."""
    if conn is None:
        conn = get_db()
    cutoff_epoch = time.time() - window_seconds

    row = conn.execute(
        "SELECT 1 FROM learnings WHERE created_at >= ? LIMIT 1",
        (cutoff_epoch,),
    ).fetchone()
    if row:
        return True

    row = conn.execute(
        """
        SELECT 1
        FROM protocol_tasks
        WHERE session_id = ?
          AND learning_id IS NOT NULL
          AND closed_at IS NOT NULL
          AND CAST(strftime('%s', closed_at) AS INTEGER) >= ?
        LIMIT 1
        """,
        (sid, int(cutoff_epoch)),
    ).fetchone()
    return bool(row)


def _toolbox_summary(conn) -> str:
    """Quick count of available skills and behavioral learnings for startup reminder."""
    try:
        skill_count = conn.execute(
            "SELECT COUNT(*) FROM skills"
        ).fetchone()[0]
        learning_count = conn.execute(
            "SELECT COUNT(*) FROM learnings WHERE status = 'active' AND priority IN ('critical', 'high')"
        ).fetchone()[0]
        parts = []
        if skill_count > 0:
            parts.append(f"{skill_count} skills available — use `nexo_skill_match(task)` before multi-step tasks")
            try:
                from skills_runtime import get_featured_skill_summaries

                featured = get_featured_skill_summaries(limit=3)
                if featured:
                    parts.append("Featured skills:")
                    for skill in featured:
                        triggers = ", ".join(skill.get("trigger_patterns", [])[:2]) or "no triggers"
                        parts.append(
                            f"- {skill['id']} — {skill['mode']}/{skill['execution_level']} — triggers: {triggers}"
                        )
            except Exception:
                pass
        if learning_count > 0:
            parts.append(f"{learning_count} high-priority learnings — use `nexo_guard_check` before editing code")
        if parts:
            return "TOOLBOX REMINDER:\n  " + "\n  ".join(parts)
    except Exception:
        pass
    return ""


def _log_session_learning_aggregation_shadow(sid: str, *, blocked: bool, pending_count: int) -> None:
    """Phase 1.5 (shadow) — session-level learning aggregation telemetry.

    The per-line gate above only sees corrections its detector flagged in the
    moment. The real close flow (here — NOT stop.py, which fires after every
    response with a 10s timeout) is where a session-WIDE aggregation belongs.
    Shadow first: record close-time compliance metrics to
    runtime/logs/learning-aggregation-shadow.ndjson so the active phase
    (full buffer analysis) can be sized with real data before it gates
    anything. Never raises, never blocks.
    """
    try:
        import json as _json
        import os as _os
        import time as _time
        from pathlib import Path as _Path

        base = _Path(_os.environ.get("NEXO_HOME") or (_Path.home() / ".nexo"))
        path = base / "runtime" / "logs" / "learning-aggregation-shadow.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_json.dumps({
                "ts": _time.time(),
                "sid": sid,
                "close_blocked_by_pending_correction": blocked,
                "pending_corrections_at_close": pending_count,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def handle_stop(sid: str) -> str:
    """Cleanly close a session, removing it from active sessions immediately."""
    pending_count = 0
    try:
        from db import list_session_correction_requirements

        pending = list_session_correction_requirements(session_id=sid, status="open", limit=3)
        pending_count = len(pending or [])
        if pending:
            _log_session_learning_aggregation_shadow(sid, blocked=True, pending_count=pending_count)
            return (
                "ERROR: session has user correction(s) without durable learning_add. "
                "Call nexo_learning_add for the correction before nexo_stop. "
                f"pending={len(pending)}"
            )
    except Exception:
        pass
    _log_session_learning_aggregation_shadow(sid, blocked=False, pending_count=pending_count)
    _stop_keepalive(sid)
    complete_session(sid)
    return f"Session {sid} closed."


def handle_status(keyword: str | None = None) -> str:
    """List active sessions, optionally filtered by keyword."""
    clean_stale_sessions()
    if keyword:
        sessions = search_sessions(keyword)
        if not sessions:
            return f"Nobody is working on '{keyword}'."
    else:
        sessions = get_active_sessions()

    if not sessions:
        return "No active sessions."

    lines = ["ACTIVE SESSIONS:"]
    for s in sessions:
        age = _format_age(s["last_update_epoch"])
        lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    return "\n".join(lines)
