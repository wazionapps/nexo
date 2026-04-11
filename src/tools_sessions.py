from __future__ import annotations
"""Session management tools: startup, heartbeat, status."""

import json
import os
import time
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from db import (
    register_session, update_session, complete_session,
    get_active_sessions, clean_stale_sessions, search_sessions,
    get_inbox, get_pending_questions, now_epoch,
    SESSION_STALE_SECONDS, check_session_has_diary,
    save_checkpoint, read_checkpoint, increment_compaction_count,
    get_db, build_pre_action_context, format_pre_action_context_bundle,
    capture_context_event,
)

# ── Session Keepalive ────────────────────────────────────────────────
# Background thread per session that auto-pings last_update_epoch every
# KEEPALIVE_INTERVAL seconds.  This prevents clean_stale_sessions from
# killing sessions that are alive but quiet (e.g. waiting on long Tasks).
# Threads are daemon=True so they die when the MCP server process exits.

KEEPALIVE_INTERVAL = 600  # 10 min — well inside the 15-min TTL
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
SESSION_PORTABILITY_DIR = NEXO_HOME / "operations" / "session-portability"

_keepalive_threads: dict[str, threading.Event] = {}  # sid → stop_event


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag with sane falsey values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


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
    _keepalive_threads[sid] = stop_event
    t = threading.Thread(target=_keepalive_loop, args=(sid, stop_event), daemon=True)
    t.start()


def _stop_keepalive(sid: str) -> None:
    """Signal the keepalive thread for the given session to stop."""
    stop_event = _keepalive_threads.pop(sid, None)
    if stop_event is not None:
        stop_event.set()


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
               WHERE session_id = ? AND status IN ('open', 'running', 'blocked', 'needs_approval')
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
            "started_epoch": session_row["started_epoch"],
            "last_update_epoch": session_row["last_update_epoch"],
            "local_time": session_row["local_time"],
        },
        "checkpoint": dict(checkpoint) if checkpoint else {},
        "latest_diary": dict(diary) if diary else {},
        "diary_draft": dict(draft) if draft else {},
        "recent_context": recent_context,
        "open_protocol_tasks": protocol_tasks,
        "open_workflow_goals": workflow_goals,
        "open_workflow_runs": workflow_runs,
    }


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
    if checkpoint:
        lines.extend(
            [
                "",
                "Checkpoint:",
                f"- Goal: {checkpoint.get('current_goal') or checkpoint.get('task') or '(none)'}",
                f"- Next: {checkpoint.get('next_step') or '(none)'}",
                f"- Files: {checkpoint.get('active_files') or '[]'}",
            ]
        )
    if diary:
        lines.extend(
            [
                "",
                "Latest diary:",
                f"- Summary: {diary.get('summary') or '(none)'}",
                f"- Pending: {diary.get('pending') or '(none)'}",
                f"- Context next: {diary.get('context_next') or '(none)'}",
            ]
        )
    elif draft:
        lines.extend(
            [
                "",
                "Diary draft:",
                f"- Summary draft: {draft.get('summary_draft') or '(none)'}",
                f"- Context hint: {draft.get('last_context_hint') or '(none)'}",
            ]
        )
    recent_context = bundle.get("recent_context") or {}
    if recent_context.get("has_matches"):
        lines.extend(["", format_pre_action_context_bundle(recent_context, compact=True)])

    protocol_tasks = bundle.get("open_protocol_tasks") or []
    if protocol_tasks:
        lines.extend(["", "Open protocol tasks:"])
        for item in protocol_tasks[:5]:
            lines.append(f"- {item['task_id']}: {item['goal']} [{item['task_type']}/{item['status']}]")

    goals = bundle.get("open_workflow_goals") or []
    if goals:
        lines.extend(["", "Open goals:"])
        for item in goals[:5]:
            lines.append(f"- {item['goal_id']}: {item['title']} [{item['status']}] -> {item['next_action'] or '(no next action)'}")

    runs = bundle.get("open_workflow_runs") or []
    if runs:
        lines.extend(["", "Open workflows:"])
        for item in runs[:5]:
            lines.append(
                f"- {item['run_id']}: {item['goal']} [{item['status']}] "
                f"step={item['current_step_key'] or '?'} next={item['next_action'] or '(none)'}"
            )

    return "\n".join(lines)


def handle_session_export_bundle(sid: str = "", path: str = "") -> str:
    """Export a machine-readable session bundle for cross-client handoff."""
    bundle = _session_portability_bundle(sid)
    if not bundle.get("ok"):
        return json.dumps(bundle, ensure_ascii=False)

    session_id = bundle["session"]["sid"]
    export_path = Path(path).expanduser() if path else (SESSION_PORTABILITY_DIR / f"{session_id}.json")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    return json.dumps(
        {
            "ok": True,
            "sid": session_id,
            "path": str(export_path),
            "open_protocol_tasks": len(bundle.get("open_protocol_tasks") or []),
            "open_workflow_goals": len(bundle.get("open_workflow_goals") or []),
            "open_workflow_runs": len(bundle.get("open_workflow_runs") or []),
        },
        ensure_ascii=False,
    )


def handle_startup(
    task: str = "Startup",
    claude_session_id: str = "",
    session_token: str = "",
    session_client: str = "",
) -> str:
    """Full startup sequence: register, clean, report.

    Args:
        task: Initial task description
        claude_session_id: Legacy alias for the external client session token.
        session_token: External client session token. Claude Code passes its UUID via hooks;
                      other clients may pass a synthetic durable ID when useful.
                      Enables automatic inbox detection when hook-backed clients provide one.
        session_client: Optional client label such as `claude_code` or `codex`.
    """
    sid = _generate_sid()
    cleaned = clean_stale_sessions()
    linked_session_id = (session_token or claude_session_id or "").strip()
    inferred_client = (session_client or "").strip()
    if not inferred_client and claude_session_id and not session_token:
        inferred_client = "claude_code"
    register_session(
        sid,
        task,
        claude_session_id=linked_session_id,
        external_session_id=linked_session_id,
        session_client=inferred_client,
    )
    _start_keepalive(sid)
    active = get_active_sessions()
    other_sessions = [s for s in active if s["sid"] != sid]
    inbox = get_inbox(sid)

    lines = [f"SID: {sid}"]

    if cleaned > 0:
        lines.append(f"Cleaned {cleaned} stale sessions.")

    if other_sessions:
        lines.append("")
        lines.append("ACTIVE SESSIONS:")
        for s in other_sessions:
            age = _format_age(s["last_update_epoch"])
            lines.append(f"  {s['sid']} ({age}) — {s['task']}")
    else:
        lines.append("No other active sessions.")

    if inbox:
        lines.append("")
        lines.append("PENDING MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            lines.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    # Check LaunchAgent health (macOS only)
    la_warnings = _check_launchagents()
    if la_warnings:
        lines.append("")
        lines.append("⚠ LAUNCHAGENT MISMATCH (plist on disk ≠ loaded in memory):")
        for w in la_warnings:
            lines.append(f"  {w}")
        lines.append("  Fix: launchctl unload + load the affected plists, or restart.")

    return "\n".join(lines)


def _check_launchagents() -> list[str]:
    """Compare on-disk plists with what launchctl has loaded. macOS only."""
    import platform
    if platform.system() != "Darwin":
        return []

    import os, subprocess, plistlib, glob

    plist_dir = os.path.expanduser("~/Library/LaunchAgents")
    warnings = []

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
                warnings.append(f"{label}: not loaded (plist exists on disk)")
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
                    # Auto-repair: reload the plist
                    subprocess.run(["launchctl", "unload", plist_path], capture_output=True, timeout=5)
                    subprocess.run(["launchctl", "load", plist_path], capture_output=True, timeout=5)
                    warnings.append(f"{label}: AUTO-REPAIRED (was pointing to stale/tmp path, reloaded from disk)")
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
    """
    from db import get_db
    update_session(sid, task)
    parts = [f"OK: {sid} — {task}"]

    inbox = get_inbox(sid)
    if inbox:
        parts.append("")
        parts.append("MESSAGES:")
        for m in inbox:
            age = _format_age(m["created_epoch"])
            parts.append(f"  [{m['from_sid']}] ({age}): {m['text']}")

    questions = get_pending_questions(sid)
    if questions:
        parts.append("")
        parts.append("PENDING QUESTIONS (respond with nexo_answer):")
        for q in questions:
            age = _format_age(q["created_epoch"])
            parts.append(f"  {q['qid']} de {q['from_sid']} ({age}): {q['question']}")

    recent_query = (context_hint or task or "").strip()
    if recent_query:
        try:
            bundle = build_pre_action_context(
                query=recent_query,
                session_id=sid,
                hours=24,
                limit=4,
            )
            if bundle.get("has_matches"):
                parts.append("")
                parts.append(format_pre_action_context_bundle(bundle, compact=True))
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
        pass  # Draft accumulation is best-effort, never block heartbeat

    # Update session checkpoint with current goal (lightweight, every heartbeat)
    try:
        save_checkpoint(
            sid=sid,
            task=task,
            current_goal=context_hint[:300] if context_hint else task,
        )
    except Exception:
        pass  # Checkpoint update is best-effort

    try:
        capture_context_event(
            event_type="heartbeat",
            title=task[:160],
            summary=(context_hint or task)[:600],
            body=context_hint[:1600] if context_hint else "",
            context_key=f"session:{sid}",
            context_title=task[:160],
            context_summary=(context_hint or task)[:600],
            context_type="session_topic",
            state="active",
            owner="session",
            actor=sid,
            source_type="heartbeat",
            source_id=sid,
            session_id=sid,
            metadata={"task": task[:160]},
            ttl_hours=24,
        )
    except Exception:
        pass

    # ── Drive/Curiosity: detect signals from context_hint (best-effort) ──
    try:
        if context_hint and len(context_hint.strip()) >= 15:
            from tools_drive import detect_drive_signal as _detect_drive
            _drive_allow_llm = _env_flag("NEXO_DRIVE_LLM_IN_HEARTBEAT", default=False)
            _drive_result = _detect_drive(
                context_hint,
                source="heartbeat",
                source_id=sid,
                allow_llm=_drive_allow_llm,
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
    conn = get_db()
    row = conn.execute("SELECT started_epoch FROM sessions WHERE sid = ?", (sid,)).fetchone()
    if row:
        age_seconds = now_epoch() - row["started_epoch"]
        has_diary = check_session_has_diary(sid)

        # DIARY_OVERDUE: >10 heartbeats OR >30 minutes, without a diary
        if not has_diary and (_hb_count > 10 or age_seconds >= 1800):
            parts.append("")
            parts.append(f"⚠ DIARY_OVERDUE: {_hb_count} heartbeats, {int(age_seconds/60)}min active, no diary. Write nexo_session_diary_write NOW.")

    # Guard check reminder: if context_hint mentions code editing and no guard_check this session
    if context_hint and _hint_suggests_code_edit(context_hint):
        try:
            guard_used = conn.execute(
                "SELECT COUNT(*) FROM guard_log WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            if guard_used == 0:
                parts.append("")
                parts.append("⚠ GUARD REMINDER: You appear to be editing code but haven't called `nexo_guard_check` this session. Do it NOW before any edits.")
        except Exception:
            pass  # guard_log table may not exist in older installs

    if context_hint and _hint_suggests_correction(context_hint):
        try:
            if not _recent_learning_capture_exists(conn, sid, window_seconds=300):
                parts.append("")
                parts.append(
                    "⚠ LEARNING REMINDER: This looks like a user correction and no recent learning was captured. "
                    "If it revealed a reusable pattern, write `nexo_learning_add` NOW."
                )
        except Exception:
            pass  # Best-effort reminder only

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
            parts.append(f"  L#{l['id']}: {l['title']}")
            # First 200 chars of content
            parts.append(f"    {l['content'][:200]}")
        parts.append("")

    # 2. Last 5 changes in this area
    changes = conn.execute(
        "SELECT id, files, what_changed, why FROM change_log WHERE files LIKE ? OR what_changed LIKE ? ORDER BY id DESC LIMIT 5",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if changes:
        parts.append("## RECENT CHANGES")
        for c in changes:
            parts.append(f"  C#{c['id']}: {c['what_changed'][:150]}")
            if c['why']:
                parts.append(f"    Why: {c['why'][:100]}")
        parts.append("")

    # 3. Active followups for this area
    followups = conn.execute(
        "SELECT id, description, date, verification FROM followups WHERE status = 'PENDING' AND (description LIKE ? OR verification LIKE ?) ORDER BY date ASC LIMIT 10",
        (f"%{area}%", f"%{area}%")
    ).fetchall()
    if followups:
        parts.append("## ACTIVE FOLLOWUPS")
        for f in followups:
            parts.append(f"  {f['id']}: {f['description'][:150]} (date: {f['date']})")
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
                parts.append(f"  {p['key']}: {p['value'][:150]}")
            parts.append("")
    except Exception:
        pass

    # 5. Recent hot context in the last 24h
    try:
        hot_bundle = build_pre_action_context(query=area, hours=24, limit=4)
        if hot_bundle.get("has_matches"):
            parts.append("## RECENT HOT CONTEXT (24H)")
            parts.append(format_pre_action_context_bundle(hot_bundle, compact=True))
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
                parts.append(f"  [{r['source_type']}] {r['source_title'] or r['content'][:80]}")
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
    import os
    from pathlib import Path
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    tone_file = nexo_home / "operations" / "session-tone.json"

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

    # 1. Pending followups (what NEXO needs to do)
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
            return "Smart startup query: no relevant memories found."

        lines = [f"SMART STARTUP — {len(results)} memories pre-loaded from composite query:"]
        lines.append(f"Query: {composite_query[:200]}...")
        lines.append("")
        lines.append(cognitive.format_results(results))

        try:
            hot_bundle = build_pre_action_context(query=composite_query, hours=24, limit=4)
            if hot_bundle.get("has_matches"):
                lines.append("")
                lines.append(format_pre_action_context_bundle(hot_bundle, compact=True))
        except Exception:
            pass

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


def _hint_suggests_correction(hint: str) -> bool:
    """Detect explicit user correction signals in a heartbeat context hint."""
    hint_lower = hint.lower()
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
    return any(signal in hint_lower for signal in correction_signals)


def _recent_learning_capture_exists(conn, sid: str, window_seconds: int = 300) -> bool:
    """Check whether a recent learning was captured manually or via protocol task close."""
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


def handle_stop(sid: str) -> str:
    """Cleanly close a session, removing it from active sessions immediately."""
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
