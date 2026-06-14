from __future__ import annotations
"""NEXO MCP Server — Phase 4: Hot-Reload Plugin System."""

import os
import signal
import sys
import threading
import json
import time
from pathlib import Path

from fastmcp import FastMCP
from core_prompts import render_core_prompt
from db import init_db, rebuild_fts_index, get_db, close_db, fts_add_dir, fts_remove_dir, fts_list_dirs
from tools_sessions import (
    handle_startup,
    handle_heartbeat,
    handle_status,
    handle_context_packet,
    handle_smart_startup_query,
    handle_session_portable_context,
    handle_session_compliance_state,
    handle_session_export_bundle,
)
from continuity import (
    build_resume_bundle,
    continuity_audit,
    format_bundle_text,
    json_dumps as _continuity_json_dumps,
    read_snapshot,
    record_compaction_event,
    write_snapshot,
)
from tools_hot_context import (
    handle_recent_context_capture,
    handle_recent_context,
    handle_pre_action_context,
    handle_recent_context_resolve,
    handle_hot_context_list,
)
from tools_transcripts import (
    handle_transcript_recent,
    handle_transcript_search,
    handle_transcript_read,
)
from tools_memory_v2 import (
    handle_memory_answer,
    handle_memory_backfill,
    handle_memory_event_list,
    handle_memory_event_stats,
    handle_memory_health,
    handle_memory_maintenance,
    handle_memory_observation_list,
    handle_memory_observation_stats,
    handle_memory_search,
    handle_memory_timeline,
)
from tools_system_catalog import (
    handle_system_catalog,
    handle_tool_explain,
)
from tools_product_knowledge import (
    handle_capability_explain,
    handle_product_answer,
    handle_product_capabilities,
    handle_product_knowledge_validate,
    handle_product_surface_status,
)
from tools_drive import (
    handle_drive_signals,
    handle_drive_reinforce,
    handle_drive_act,
    handle_drive_dismiss,
)
from user_context import get_context as _get_ctx
from tools_coordination import (
    handle_track, handle_untrack, handle_files,
    handle_send, handle_ask, handle_answer, handle_check_answer,
)
from tools_reminders import handle_reminders
from tools_menu import handle_menu
from tools_reminders_crud import (
    handle_reminder_create, handle_reminder_get, handle_reminder_update,
    handle_reminder_complete, handle_reminder_note, handle_reminder_restore, handle_reminder_delete,
    handle_followup_create, handle_followup_get, handle_followup_update,
    handle_followup_complete, handle_followup_note, handle_followup_restore, handle_followup_delete,
)
from tools_learnings import (
    handle_learning_add, handle_learning_search,
    handle_learning_update, handle_learning_delete, handle_learning_list,
    handle_learning_quality, handle_learning_resolve_candidate,
)
from tools_credentials import (
    handle_credential_get, handle_credential_create,
    handle_credential_update, handle_credential_delete, handle_credential_list,
)
from tools_task_history import (
    handle_task_log, handle_task_list, handle_task_frequency,
)
from tools_automation_sessions import (
    handle_session_log_create,
    handle_session_log_close,
)
from plugins.cortex import handle_cortex_check, handle_cortex_decide
from plugins.guard import handle_guard_check
from plugins.protocol import (
    handle_confidence_check,
    handle_protocol_debt_resolve,
    handle_task_acknowledge_guard,
    handle_task_close,
    handle_task_open,
)
from plugins.episodic_memory import handle_session_diary_read, handle_session_diary_write
from plugins.cards import handle_card_match
from plugins.skills import handle_skill_match
from plugins.workflow import (
    handle_goal_get,
    handle_goal_list,
    handle_goal_open,
    handle_goal_update,
    handle_workflow_compensation,
    handle_workflow_get,
    handle_workflow_handoff,
    handle_workflow_list,
    handle_workflow_open,
    handle_workflow_replay,
    handle_workflow_resume,
    handle_workflow_update,
)
from plugin_loader import load_all_plugins, load_plugin, remove_plugin, list_plugins
from tools_guardian import handle_guardian_rule_override
from tools_api_call import (
    handle_api_call,
    handle_create_app_token,
    handle_support_ticket_create,
    handle_support_ticket_list,
    handle_support_ticket_read,
)
from runtime_versioning import (
    RestartRequiredMiddleware,
    build_mcp_status,
    prime_process_fingerprint,
    prime_process_version,
)
from runtime_service import (
    is_runtime_service_process,
    run_mcp_proxy_adapter,
    runtime_service_status,
    should_use_mcp_adapter,
    write_service_state,
)
from mcp_write_queue import queue_status as mcp_write_queue_status, start_write_queue_worker
from local_context import api as local_context_api
from local_context.db import close_local_context_db


# ── Graceful shutdown: close DB on any termination signal ──────────
def _shutdown_handler(signum, frame):
    # Guarantee the process actually dies on SIGTERM/SIGINT. A prior failure mode
    # left orphaned servers ignoring SIGTERM for *days*: cleanup (close_db) blocked
    # on a never-released lock and sys.exit() never ran while the stdio loop held
    # the main thread. Arm a hard-exit watchdog FIRST, then best-effort close, then
    # os._exit. WAL + autocommit means there is no open transaction to lose and the
    # durable write queue replays unprocessed items on next start, so a hard exit is
    # safe even if cleanup is skipped.
    try:
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
    except Exception:
        pass
    try:
        close_local_context_db()
        close_db()
    except Exception:
        pass
    os._exit(0)


def _resolved_nexo_home() -> str:
    return os.environ.get("NEXO_HOME", os.path.join(os.path.expanduser("~"), ".nexo"))


def _data_dir() -> str:
    return os.path.join(_resolved_nexo_home(), "data")


def _backup_dir() -> str:
    try:
        import paths

        return str(paths.backups_dir())
    except Exception:
        return os.path.join(_resolved_nexo_home(), "runtime", "backups")


def _allow_fresh_db_on_corruption() -> bool:
    value = str(os.environ.get("NEXO_ALLOW_FRESH_DB_ON_CORRUPTION", "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _quarantine_corrupt_db_file(db_path: str) -> None:
    if os.path.exists(db_path):
        corrupt_path = db_path + ".corrupt"
        os.rename(db_path, corrupt_path)
        print(f"[NEXO] Corrupt DB moved to {os.path.basename(corrupt_path)}", file=sys.stderr)
    for ext in (".db-wal", ".db-shm"):
        wal_path = db_path.replace(".db", ext)
        if os.path.exists(wal_path):
            os.remove(wal_path)


def _restore_valid_db_backup() -> bool:
    import glob
    import shutil
    import sqlite3

    from db._core import DB_PATH as db_path

    backups = sorted(glob.glob(os.path.join(_backup_dir(), "nexo-*.db")), reverse=True)
    for backup_path in backups:
        try:
            test_conn = sqlite3.connect(backup_path)
            integrity = test_conn.execute("PRAGMA integrity_check").fetchone()
            test_conn.close()
            if not integrity or integrity[0] != "ok":
                continue
            try:
                close_db()
            except Exception:
                pass
            shutil.copy2(backup_path, db_path)
            print(f"[NEXO] Restored DB from backup: {os.path.basename(backup_path)}", file=sys.stderr)
            init_db()
            return True
        except Exception:
            continue
    return False


def _init_db_or_exit() -> None:
    import sqlite3

    for attempt in range(3):
        try:
            init_db()
            return
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                print(f"[NEXO] DB init temporarily busy: {exc}", file=sys.stderr)
                raise SystemExit(75)
            print(f"[NEXO] DB init failed: {exc}", file=sys.stderr)
            break
        except sqlite3.DatabaseError as exc:
            print(f"[NEXO] DB init failed: {exc}", file=sys.stderr)
            break

    restored = False
    try:
        restored = _restore_valid_db_backup()
    except Exception as restore_exc:
        print(f"[NEXO] Backup restore failed: {restore_exc}", file=sys.stderr)

    if restored:
        return

    try:
        close_db()
    except Exception:
        pass

    try:
        from db._core import DB_PATH as db_path
        _quarantine_corrupt_db_file(db_path)
    except Exception:
        pass

    if not _allow_fresh_db_on_corruption():
        print(
            "[NEXO] Refusing to create a fresh empty database automatically. "
            "Restore a valid backup or set NEXO_ALLOW_FRESH_DB_ON_CORRUPTION=1 to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        init_db()
        print("[NEXO] Fresh database created because override is enabled.", file=sys.stderr)
    except Exception as fresh_exc:
        print(f"[NEXO] FATAL: Cannot initialize database: {fresh_exc}", file=sys.stderr)
        print("[NEXO] Check permissions on NEXO_HOME/data/ and disk space.", file=sys.stderr)
        sys.exit(1)


def _emit_startup_preflight_messages(result: dict) -> None:
    if result.get("updated"):
        print("[NEXO] Startup update applied.", file=sys.stderr)
    if result.get("deferred_reason"):
        print(f"[NEXO] Startup update deferred: {result['deferred_reason']}", file=sys.stderr)
    if result.get("git_update"):
        print(f"[NEXO] {result['git_update']}", file=sys.stderr)
    if result.get("npm_notice"):
        print(f"[NEXO] {result['npm_notice']}", file=sys.stderr)
    if result.get("claude_md_update"):
        print(f"[NEXO] {result['claude_md_update']}", file=sys.stderr)
    for message in result.get("client_bootstrap_updates", []):
        if message != result.get("claude_md_update"):
            print(f"[NEXO] {message}", file=sys.stderr)
    for migration in result.get("migrations", []):
        if migration.get("status") == "failed":
            print(
                f"[NEXO] Migration {migration.get('file', '?')} FAILED: {migration.get('message', '')}",
                file=sys.stderr,
            )


def _run_startup_preflight_sync() -> None:
    try:
        from auto_update import startup_preflight

        result = startup_preflight(entrypoint="server", interactive=False)
        _emit_startup_preflight_messages(result)
    except Exception as e:
        print(f"[NEXO auto-update] error: {e}", file=sys.stderr)


_ESSENTIAL_MCP_STARTUP_PLUGINS = (
    "cards.py",
    "doctor.py",
    "desktop_preferences.py",
    "episodic_memory.py",
    "evolution.py",
    "lifecycle_events.py",
    "outcomes.py",
    "preferences.py",
    "protocol.py",
    "recover.py",
    "skills.py",
    "user_state_tools.py",
    "workflow.py",
)


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "si"}


def _mcp_startup_plugin_mode() -> str:
    return str(os.environ.get("NEXO_MCP_PLUGIN_MODE", "none") or "none").strip().lower()


def _load_startup_plugins() -> None:
    mode = _mcp_startup_plugin_mode()
    if mode in {"none", "off", "0", "false"}:
        print("[NEXO] MCP dynamic plugin loading skipped.", file=sys.stderr)
        return
    if mode in {"full", "all", "legacy"}:
        load_all_plugins(mcp)
        return

    if mode not in {"essential", "fast", "default"}:
        print(f"[NEXO] Unknown NEXO_MCP_PLUGIN_MODE={mode!r}; using essential plugins.", file=sys.stderr)

    loaded = 0
    for filename in _ESSENTIAL_MCP_STARTUP_PLUGINS:
        try:
            loaded += int(load_plugin(mcp, filename) or 0)
        except Exception as exc:
            print(f"[PLUGIN ERROR] {filename}: {exc}", file=sys.stderr)
    print(f"[NEXO] MCP essential plugins ready: {loaded} tools.", file=sys.stderr)


def _select_reapable_servers(processes, *, self_pid, resident_pid):
    """Pure selection: given ps rows [{pid, ppid, cmdline}], return the PIDs of
    NEXO MCP servers that are safe to reap.

    A server is reapable iff it is ORPHANED (ppid == 1 — its launching client is
    gone, so it serves nobody) and runs a NEXO ``server.py``. We never touch a
    server that still has a live parent (ppid != 1 → a connected client), nor the
    warm runtime-service resident (``resident_pid``), nor ourselves (``self_pid``).
    """
    reapable: list[int] = []
    for proc in processes:
        try:
            pid = int(proc.get("pid"))
            ppid = int(proc.get("ppid"))
        except (TypeError, ValueError):
            continue
        if pid in (self_pid, resident_pid):
            continue
        if ppid != 1:
            continue  # has a live parent/client — leave it alone
        cmd = str(proc.get("cmdline") or "")
        if "server.py" not in cmd or "nexo" not in cmd.lower():
            continue
        reapable.append(pid)
    return reapable


def reap_orphaned_mcp_servers() -> int:
    """Best-effort cleanup of orphaned NEXO MCP servers left behind by dead
    clients. Runs once at stdio startup so the process count can't grow without
    bound (the historical cause of SQLite contention + wedged servers). Never
    raises; returns how many SIGTERMs were signalled."""
    try:
        import subprocess

        self_pid = os.getpid()
        resident_pid = -1
        try:
            from runtime_service import read_service_state

            resident_pid = int((read_service_state() or {}).get("pid") or -1)
        except Exception:
            resident_pid = -1
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        rows = []
        for line in proc.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            rows.append({"pid": parts[0], "ppid": parts[1], "cmdline": parts[2]})
        reaped = 0
        for pid in _select_reapable_servers(rows, self_pid=self_pid, resident_pid=resident_pid):
            try:
                os.kill(pid, signal.SIGTERM)
                reaped += 1
            except Exception:
                pass
        if reaped:
            print(f"[NEXO] Reaped {reaped} orphaned MCP server(s).", file=sys.stderr)
        return reaped
    except Exception:
        return 0


def _server_init():
    """Run side effects needed by the MCP server.

    Called only when the server is actually started (not on import).
    """
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # ── Write PID file for stale process detection ─────────────────
    if not _env_flag("NEXO_MCP_PROBE"):
        data_dir = _data_dir()
        os.makedirs(data_dir, exist_ok=True)
        _pid_file = os.path.join(data_dir, "nexo.pid")
        with open(_pid_file, "w") as f:
            f.write(str(os.getpid()))

    # ── Reap orphaned servers from dead clients (stdio only) ───────
    # Defense against unbounded process growth: a client that crashed/reconnected
    # leaves its old server orphaned (reparented to init, ppid==1). Those serve
    # nobody yet still open the shared SQLite. Clean them on each fresh start.
    if not _env_flag("NEXO_MCP_PROBE") and not is_runtime_service_process():
        reap_orphaned_mcp_servers()

    # ── Database initialization with recovery ─────────────────────
    _init_db_or_exit()

    # ── Durable MCP write queue ───────────────────────────────────
    start_write_queue_worker()

    # ── Auto-update / startup preflight ───────────────────────────
    # The MCP client waits for an immediate JSON-RPC handshake. Running update
    # checks here can block the transport and make clients start without NEXO.
    if _env_flag("NEXO_MCP_RUN_STARTUP_PREFLIGHT"):
        _run_startup_preflight_sync()
    else:
        print("[NEXO] MCP startup preflight deferred.", file=sys.stderr)

    # ── Load plugins ───────────────────────────────────────────────
    _load_startup_plugins()


mcp = FastMCP(
    name="nexo",
    instructions=render_core_prompt(
        "server-mcp-instructions",
        assistant_name=_get_ctx().assistant_name,
    ),
)
prime_process_version()
prime_process_fingerprint()
mcp.add_middleware(
    RestartRequiredMiddleware(client=str(os.environ.get("NEXO_MCP_CLIENT", "") or "").strip())
)

# FastMCP (both 2.14.7 and 3.2.4) auto-generates an outputSchema wrapper
# (`{required: [result], x-fastmcp-wrap-result: true}`) for every tool whose
# return annotation is a non-object type such as `str`. Claude Code validates
# MCP tool responses strictly against outputSchema and rejects our plain-text
# replies with `Output validation error: result is a required property`,
# which makes every `nexo_*` tool inexecutable from Claude Code. We opt out
# of output_schema globally by wrapping `mcp.tool` so every decorator here
# and in plugins defaults to `output_schema=None` unless the caller passes
# something explicit. See followup NF-FASTMCP-OUTPUT-SCHEMA-1776969764.
_mcp_tool_original = mcp.tool


def _mcp_tool_without_output_schema(name_or_fn=None, **kwargs):
    kwargs.setdefault("output_schema", None)
    return _mcp_tool_original(name_or_fn, **kwargs)


mcp.tool = _mcp_tool_without_output_schema  # type: ignore[method-assign]


def _run_kwargs_from_env() -> dict:
    transport = str(os.environ.get("NEXO_MCP_TRANSPORT", "stdio") or "stdio").strip().lower()
    if transport in {"http", "streamable_http"}:
        transport = "streamable-http"
    if transport == "stdio":
        return {"transport": "stdio"}

    host = str(os.environ.get("NEXO_MCP_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port_text = str(os.environ.get("NEXO_MCP_PORT", "8000") or "8000").strip()
    path = str(os.environ.get("NEXO_MCP_PATH", "/mcp") or "/mcp").strip() or "/mcp"
    try:
        port = int(port_text)
    except Exception:
        port = 8000

    kwargs = {
        "transport": transport,
        "host": host,
        "port": port,
    }
    if transport == "streamable-http":
        kwargs["path"] = path
    return kwargs


# ── Session management (3 tools) ──────────────────────────────────

@mcp.tool
def nexo_startup(task: str = "Startup", claude_session_id: str = "", session_token: str = "", session_client: str = "", session_provider: str = "", conversation_id: str = "") -> str:
    """Register new session, clean stale ones, return active sessions + alerts.

    Call this ONCE at the start of every conversation.
    Returns the session ID (SID) — store it for use in all other nexo_ tools.

    Args:
        task: Initial task description.
        claude_session_id: Legacy alias for the external client session token.
        session_token: External client session token. Claude Code passes its UUID via hooks;
                      other clients may pass a synthetic durable token when useful.
                      Pass this to enable automatic inter-terminal inbox detection when available.
        session_client: Optional client label such as `claude_code` or `codex`.
        session_provider: Optional provider label such as `anthropic` or `openai`.
        conversation_id: Stable client-side conversation identifier when available.
    """
    return handle_startup(
        task,
        claude_session_id=claude_session_id,
        session_token=session_token,
        session_client=session_client,
        session_provider=session_provider,
        conversation_id=conversation_id,
    )


@mcp.tool
def nexo_heartbeat(sid: str, task: str, context_hint: str = '') -> str:
    """Update session task, check inbox and pending questions. Auto-detects trust events.

    Call this at the START of every user interaction (before doing work).
    May surface silent runtime signals DIARY_OVERDUE, GUARD_REMINDER,
    LEARNING_REMINDER, and PROTOCOL_DEBT; clients must treat those as
    internal obligations, not user-visible content.
    Output always begins with a NOW_UTC line (ISO-8601, UTC) — use it as the
    authoritative wall-clock time for any artifact (emails, diaries, followups)
    to avoid date/day-of-week drift across long sessions.
    Args:
        sid: Your session ID from nexo_startup.
        task: Brief description of current work (5-10 words).
        context_hint: Last 2-3 sentences from the user or current topic. Used for sentiment detection, trust auto-scoring, and mid-session RAG. ALWAYS provide this for best results.
    """
    return handle_heartbeat(sid, task, context_hint)


@mcp.tool
def nexo_session_diary_read(
    session_id: str = "",
    last_n: int = 3,
    last_day: bool = False,
    domain: str = "",
    brief: bool = False,
) -> str:
    """Read recent session diaries for context continuity."""
    return handle_session_diary_read(session_id, last_n, last_day, domain, brief)


@mcp.tool
def nexo_session_diary_write(
    decisions: str = "",
    summary: str = "",
    discarded: str = "",
    pending: str = "",
    context_next: str = "",
    mental_state: str = "",
    user_signals: str = "",
    domain: str = "",
    session_id: str = "",
    self_critique: str = "",
    source: str = "claude",
    payload_json: str = "",
) -> str:
    """Write end-of-session diary with decisions, pending context, and self-critique.

    Exposed as an essential MCP tool because Desktop close/archive/app-exit
    enforcement depends on it even when dynamic plugin loading is disabled.
    """
    return handle_session_diary_write(
        decisions=decisions,
        summary=summary,
        discarded=discarded,
        pending=pending,
        context_next=context_next,
        mental_state=mental_state,
        user_signals=user_signals,
        domain=domain,
        session_id=session_id,
        self_critique=self_critique,
        source=source,
        payload_json=payload_json,
    )


@mcp.tool
def nexo_session_compliance_state(sid: str = "", diary_window_minutes: int = 15) -> str:
    """Return Brain-verifiable heartbeat, diary, learning, and close compliance state."""
    return handle_session_compliance_state(sid, diary_window_minutes)


@mcp.tool
def nexo_confidence_check(
    goal: str,
    task_type: str = "answer",
    area: str = "",
    context_hint: str = "",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    unknowns: str = "[]",
    verification_step: str = "",
    stakes: str = "",
) -> str:
    """Decide whether an answer should proceed directly or be verified first."""
    return handle_confidence_check(
        goal,
        task_type,
        area,
        context_hint,
        constraints,
        evidence_refs,
        unknowns,
        verification_step,
        stakes,
    )


@mcp.tool
def nexo_protocol_debt_resolve(
    debt_ids: str = "",
    task_id: str = "",
    session_id: str = "",
    debt_types: str = "",
    resolution: str = "",
    debt_id: str = "",
) -> str:
    """Resolve protocol debt records by id or filters."""
    return handle_protocol_debt_resolve(debt_ids, task_id, session_id, debt_types, resolution, debt_id)


@mcp.tool
def nexo_card_match(
    query: str,
    limit: int = 5,
    include_protocol: bool = True,
    locale: str = "es",
    category: str = "",
    business_type: str = "",
) -> str:
    """Find official NEXO protocol cards for a user request."""
    return handle_card_match(query, limit, include_protocol, locale, category, business_type)


@mcp.tool
def nexo_skill_match(task: str, level: str = "") -> str:
    """Find reusable NEXO skills for the current task."""
    return handle_skill_match(task, level)


@mcp.tool
def nexo_stop(sid: str) -> str:
    """Cleanly close a session. Removes it from active sessions immediately.

    Call this when ending a conversation to avoid ghost sessions.
    Args:
        sid: Session ID to close."""
    from tools_sessions import handle_stop
    return handle_stop(sid)


@mcp.tool
def nexo_cortex_check(
    goal: str,
    task_type: str = "answer",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
) -> str:
    """Cognitive pre-action check. Call before significant actions."""
    return handle_cortex_check(
        goal,
        task_type,
        plan,
        known_facts,
        unknowns,
        constraints,
        evidence_refs,
        verification_step,
    )


@mcp.tool
def nexo_cortex_decide(
    goal: str,
    alternatives: str,
    task_type: str = "execute",
    impact_level: str = "high",
    context_hint: str = "",
    area: str = "",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    session_id: str = "",
    task_id: str = "",
    linked_outcome_id: int = 0,
    goal_profile_id: str = "",
    goal_id: str = "",
    auto_create_outcome: bool = False,
) -> str:
    """Evaluate concrete alternatives for a high-impact task and persist the recommendation."""
    return handle_cortex_decide(
        goal,
        alternatives,
        task_type,
        impact_level,
        context_hint,
        area,
        constraints,
        evidence_refs,
        session_id,
        task_id,
        linked_outcome_id,
        goal_profile_id,
        goal_id,
        auto_create_outcome,
    )


@mcp.tool
def nexo_guard_check(files: str = "", area: str = "") -> str:
    """Check learnings relevant to files/area before reading or editing code."""
    return handle_guard_check(files, area)


@mcp.tool
def nexo_task_open(
    sid: str,
    goal: str = "",
    task_type: str = "answer",
    area: str = "",
    files: str = "",
    project_hint: str = "",
    plan: str = "[]",
    known_facts: str = "[]",
    unknowns: str = "[]",
    constraints: str = "[]",
    evidence_refs: str = "[]",
    verification_step: str = "",
    stakes: str = "",
    context_hint: str = "",
    description: str = "",
    ack_rules: str = "",
) -> str:
    """Open a protocol task for non-trivial work.

    ``ack_rules`` accepts "#95,#156" / "95,156" / "[95, 156]" and, when
    the guard surfaces blocking rules, acknowledges them inline instead
    of requiring a separate ``nexo_task_acknowledge_guard`` call.
    """
    return handle_task_open(
        sid,
        goal,
        task_type,
        area,
        files,
        project_hint,
        plan,
        known_facts,
        unknowns,
        constraints,
        evidence_refs,
        verification_step,
        stakes,
        context_hint,
        description,
        ack_rules,
    )


@mcp.tool
def nexo_task_acknowledge_guard(
    sid: str,
    task_id: str,
    learning_ids: str = "",
    note: str = "",
) -> str:
    """Acknowledge blocking guard rules on an open protocol task."""
    return handle_task_acknowledge_guard(sid, task_id, learning_ids, note)


@mcp.tool
def nexo_task_close(
    sid: str,
    task_id: str,
    outcome: str = "",
    evidence: str = "",
    files_changed: str = "",
    correction_happened: bool = False,
    change_summary: str = "",
    change_why: str = "",
    change_risks: str = "",
    change_verify: str = "",
    triggered_by: str = "",
    followup_needed: bool = False,
    followup_id: str = "",
    followup_description: str = "",
    followup_date: str = "",
    followup_verification: str = "",
    followup_reasoning: str = "",
    learning_category: str = "",
    learning_title: str = "",
    learning_content: str = "",
    learning_reasoning: str = "",
    outcome_notes: str = "",
    result: str = "",
    summary: str = "",
    verification: str = "",
    evidence_refs: str = "",
    work_type: str = "",
    stakes: str = "",
    artifact_hash: str = "",
    last_human_validation_of_artifact_hash: str = "",
) -> str:
    """Close a protocol task with evidence and optional artifacts.

    For high-stakes/irreversible closures (publish stable, broadcast, payment,
    force-push, revoke) pass ``work_type``/``stakes`` plus ``artifact_hash`` and
    ``last_human_validation_of_artifact_hash`` (both must match) so the close
    gates can be satisfied instead of dead-ending.
    """
    return handle_task_close(
        sid,
        task_id,
        outcome,
        evidence,
        files_changed,
        correction_happened,
        change_summary,
        change_why,
        change_risks,
        change_verify,
        triggered_by,
        followup_needed,
        followup_id,
        followup_description,
        followup_date,
        followup_verification,
        followup_reasoning,
        learning_category,
        learning_title,
        learning_content,
        learning_reasoning,
        outcome_notes,
        result,
        summary,
        verification,
        evidence_refs,
        work_type,
        stakes,
        artifact_hash,
        last_human_validation_of_artifact_hash,
    )


@mcp.tool
def nexo_workflow_open(
    sid: str,
    goal: str,
    goal_id: str = "",
    workflow_kind: str = "general",
    protocol_task_id: str = "",
    idempotency_key: str = "",
    priority: str = "normal",
    steps: str = "[]",
    shared_state: str = "{}",
    next_action: str = "",
    owner: str = "",
) -> str:
    """Open a durable workflow run for long multi-step work."""
    return handle_workflow_open(
        sid,
        goal,
        goal_id,
        workflow_kind,
        protocol_task_id,
        idempotency_key,
        priority,
        steps,
        shared_state,
        next_action,
        owner,
    )


@mcp.tool
def nexo_workflow_update(
    run_id: str,
    step_key: str = "",
    step_title: str = "",
    step_status: str = "",
    run_status: str = "",
    checkpoint_label: str = "",
    summary: str = "",
    shared_state: str = "",
    state_patch: str = "",
    evidence: str = "",
    next_action: str = "",
    retry_after: str = "",
    max_retries: int = 0,
    retry_policy: str = "",
    requires_approval: bool = False,
    compensation: str = "",
    actor: str = "",
    owner: str = "",
) -> str:
    """Update a workflow run with a replayable checkpoint."""
    return handle_workflow_update(
        run_id,
        step_key,
        step_title,
        step_status,
        run_status,
        checkpoint_label,
        summary,
        shared_state,
        state_patch,
        evidence,
        next_action,
        retry_after,
        max_retries,
        retry_policy,
        requires_approval,
        compensation,
        actor,
        owner,
    )


@mcp.tool
def nexo_goal_open(
    sid: str,
    title: str,
    objective: str = "",
    parent_goal_id: str = "",
    priority: str = "normal",
    next_action: str = "",
    success_signal: str = "",
    owner: str = "",
    shared_state: str = "{}",
) -> str:
    """Open a durable goal so objectives survive sessions."""
    return handle_goal_open(
        sid,
        title,
        objective,
        parent_goal_id,
        priority,
        next_action,
        success_signal,
        owner,
        shared_state,
    )


@mcp.tool
def nexo_goal_update(
    goal_id: str,
    status: str = "",
    title: str = "",
    objective: str = "",
    parent_goal_id: str = "",
    next_action: str = "",
    success_signal: str = "",
    blocker_reason: str = "",
    owner: str = "",
    shared_state: str = "",
) -> str:
    """Update a durable goal with active/blocked/completed state."""
    return handle_goal_update(
        goal_id,
        status,
        title,
        objective,
        parent_goal_id,
        next_action,
        success_signal,
        blocker_reason,
        owner,
        shared_state,
    )


@mcp.tool
def nexo_goal_get(goal_id: str, include_runs: bool = False) -> str:
    """Read one durable goal and optionally include linked workflow runs."""
    return handle_goal_get(goal_id, include_runs)


@mcp.tool
def nexo_goal_list(status: str = "", include_closed: bool = False, limit: int = 20) -> str:
    """List durable goals."""
    return handle_goal_list(status, include_closed, limit)


@mcp.tool
def nexo_workflow_get(run_id: str, include_steps: bool = True, checkpoint_limit: int = 8) -> str:
    """Read the full durable workflow state."""
    return handle_workflow_get(run_id, include_steps, checkpoint_limit)


@mcp.tool
def nexo_workflow_handoff(
    run_id: str,
    actor: str,
    next_action: str = "",
    handoff_note: str = "",
    shared_state: str = "",
    new_owner: str = "",
) -> str:
    """Record a durable workflow handoff."""
    return handle_workflow_handoff(run_id, actor, next_action, handoff_note, shared_state, new_owner)


@mcp.tool
def nexo_workflow_compensation(run_id: str, checkpoint_limit: int = 10) -> str:
    """Return the compensation plan for a partially completed workflow."""
    return handle_workflow_compensation(run_id, checkpoint_limit)


@mcp.tool
def nexo_workflow_resume(run_id: str) -> str:
    """Summarize the next actionable step for a workflow run."""
    return handle_workflow_resume(run_id)


@mcp.tool
def nexo_workflow_replay(run_id: str, limit: int = 20) -> str:
    """Replay recent checkpoints for a workflow run."""
    return handle_workflow_replay(run_id, limit)


@mcp.tool
def nexo_workflow_list(status: str = "", include_closed: bool = False, limit: int = 20) -> str:
    """List durable workflow runs."""
    return handle_workflow_list(status, include_closed, limit)


@mcp.tool
def nexo_status(keyword: str = "") -> str:
    """List active sessions. Filter by keyword if provided."""
    return handle_status(keyword if keyword else None)


@mcp.tool
def nexo_runtime_service_status() -> str:
    """Return the resident NEXO Runtime Service status for diagnostics."""
    return json.dumps(runtime_service_status(), indent=2, ensure_ascii=False)


@mcp.tool
def nexo_mcp_write_queue_status(limit: int = 20) -> str:
    """Return durable MCP write queue counts and recent records."""
    try:
        clean_limit = max(1, min(int(limit or 20), 100))
    except Exception:
        clean_limit = 20
    return json.dumps(mcp_write_queue_status(limit=clean_limit), indent=2, ensure_ascii=False)


@mcp.tool
def nexo_managed_mcp_status(apply: bool = False) -> str:
    """Read or apply the Brain-owned managed MCP catalog reconciliation plan."""
    from managed_mcp import managed_mcp_status, reconcile_managed_mcp
    import paths

    runtime_root = Path(__file__).resolve().parent
    if apply:
        result = reconcile_managed_mcp(
            nexo_home=paths.home(),
            runtime_root=runtime_root,
            apply=True,
        )
    else:
        result = managed_mcp_status(
            nexo_home=paths.home(),
            runtime_root=runtime_root,
        )
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool
def nexo_closure_status(refresh: bool = True, limit: int = 10) -> str:
    """Read the Operational Closure Plane status and ranked closure queue."""
    from closure_plane import handle_closure_status

    try:
        clean_limit = max(1, min(int(limit or 10), 100))
    except Exception:
        clean_limit = 10
    return handle_closure_status(refresh=bool(refresh), limit=clean_limit)


@mcp.tool
def nexo_closure_next(
    limit: int = 10,
    include_waiting: bool = False,
    source: str = "",
    kind: str = "",
    state: str = "",
    max_risk: float = 0.0,
    area: str = "",
) -> str:
    """Return the next ranked closure items without executing source actions."""
    from closure_plane import handle_closure_next

    try:
        clean_limit = max(1, min(int(limit or 10), 100))
    except Exception:
        clean_limit = 10
    try:
        clean_max_risk = float(max_risk or 0.0)
    except Exception:
        clean_max_risk = 0.0
    return handle_closure_next(clean_limit, bool(include_waiting), source, kind, state, clean_max_risk, area)


@mcp.tool
def nexo_closure_item_get(item_id: str) -> str:
    """Return one closure item with sources and events."""
    from closure_plane import handle_closure_item_get

    return handle_closure_item_get(item_id)


@mcp.tool
def nexo_closure_triage(
    item_id: str,
    state: str = "",
    kind: str = "",
    blocker_reason: str = "",
    next_action: str = "",
    evidence_required: str = "",
    owner: str = "",
    capability_required: str = "",
    capability_status: str = "",
    duplicate_of: str = "",
) -> str:
    """Triage a closure item without executing its source action."""
    from closure_plane import handle_closure_triage

    return handle_closure_triage(
        item_id,
        state,
        kind,
        blocker_reason,
        next_action,
        evidence_required,
        owner,
        capability_required,
        capability_status,
        duplicate_of,
    )


@mcp.tool
def nexo_closure_link(item_id: str, link_type: str, link_id: str, relation: str = "related") -> str:
    """Link a closure item to a task, workflow, followup, outcome, or learning."""
    from closure_plane import handle_closure_link

    return handle_closure_link(item_id, link_type, link_id, relation)


@mcp.tool
def nexo_closure_snapshot(refresh: bool = True, snapshot_date: str = "", limit: int = 10) -> str:
    """Write and return an Operational Closure Plane daily snapshot."""
    from closure_plane import handle_closure_snapshot

    try:
        clean_limit = max(1, min(int(limit or 10), 100))
    except Exception:
        clean_limit = 10
    return handle_closure_snapshot(bool(refresh), snapshot_date, clean_limit)


@mcp.tool
def nexo_closure_verify(item_id: str, evidence: str) -> str:
    """Record verification evidence for a closure item."""
    from closure_plane import handle_closure_verify

    return handle_closure_verify(item_id, evidence)


@mcp.tool
def nexo_closure_close(item_id: str, reason: str = "completed") -> str:
    """Close a verified closure item or reject/stale it explicitly."""
    from closure_plane import handle_closure_close

    return handle_closure_close(item_id, reason)


@mcp.tool
def nexo_opportunity_refresh(
    dry_run: bool = True,
    sources: str = "",
    limit_per_source: int = 250,
    write_report: bool = False,
) -> str:
    """Generate Opportunity Orchestrator candidates from existing evidence."""
    from opportunity_orchestrator import handle_opportunity_refresh

    try:
        clean_limit = max(1, min(int(limit_per_source or 250), 500))
    except Exception:
        clean_limit = 250
    return handle_opportunity_refresh(bool(dry_run), sources, clean_limit, bool(write_report))


@mcp.tool
def nexo_opportunity_queue(
    surface: str = "home",
    limit: int = 3,
    refresh: bool = False,
    include_snoozed: bool = False,
) -> str:
    """Return at most three evidence-backed proposals for a user surface."""
    from opportunity_orchestrator import handle_opportunity_queue

    try:
        clean_limit = max(0, min(int(limit or 3), 3))
    except Exception:
        clean_limit = 3
    return handle_opportunity_queue(surface, clean_limit, bool(refresh), bool(include_snoozed))


@mcp.tool
def nexo_opportunity_get(opportunity_id: str, include_evidence: bool = True) -> str:
    """Return one opportunity with evidence and read-only preparations."""
    from opportunity_orchestrator import handle_opportunity_get

    return handle_opportunity_get(opportunity_id, bool(include_evidence))


@mcp.tool
def nexo_opportunity_feedback(proposal_id: str, feedback: str, note: str = "", snooze_until: str = "") -> str:
    """Record proposal feedback and apply suppression/snooze when requested."""
    from opportunity_orchestrator import handle_opportunity_feedback

    return handle_opportunity_feedback(proposal_id, feedback, note, snooze_until)


@mcp.tool
def nexo_opportunity_suppress(scope_type: str, scope_key: str, reason: str = "", expires_at: str = "") -> str:
    """Suppress repeated opportunity suggestions by scope."""
    from opportunity_orchestrator import handle_opportunity_suppress

    return handle_opportunity_suppress(scope_type, scope_key, reason, expires_at)


@mcp.tool
def nexo_local_index_status() -> str:
    """Return local memory index status for Desktop settings and support diagnostics."""
    return json.dumps(local_context_api.status(), ensure_ascii=False)


@mcp.tool
def nexo_local_index_control(
    action: str = "run_once",
    root: str = "",
    limit: int = 0,
    process_limit: int = 0,
    performance_profile: str = "",
) -> str:
    """Control the local memory index.

    Args:
        action: one of run_once, pause, resume, clear_index, set_performance.
        root: optional folder to add and scan when action=run_once.
        limit: optional per-root scan limit for cooperative background cycles.
        process_limit: max pending jobs to process in this cycle.
        performance_profile: low, medium, high or extreme when action=set_performance.
    """
    normalized = str(action or "run_once").strip().lower()
    if normalized == "pause":
        result = local_context_api.pause()
    elif normalized == "resume":
        result = local_context_api.resume()
    elif normalized == "clear_index":
        result = local_context_api.clear_index()
    elif normalized == "set_performance":
        result = local_context_api.set_performance_profile(performance_profile)
    elif normalized == "run_once":
        result = local_context_api.run_once(root=root or None, limit=limit or None, process_limit=process_limit or None)
    else:
        result = {"ok": False, "error": "unknown_action", "allowed": ["run_once", "pause", "resume", "clear_index", "set_performance"]}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_index_roots(action: str = "list", path: str = "", mode: str = "normal", depth: int = 2) -> str:
    """List, add or remove local memory roots."""
    normalized = str(action or "list").strip().lower()
    if normalized == "list":
        result = {"ok": True, "roots": local_context_api.list_roots(readonly=True)}
    elif normalized == "add":
        result = local_context_api.add_root(path, mode=mode, depth=depth)
    elif normalized == "remove":
        result = local_context_api.remove_root(path)
    else:
        result = {"ok": False, "error": "unknown_action", "allowed": ["list", "add", "remove"]}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_index_exclusions(action: str = "list", path: str = "", reason: str = "user") -> str:
    """List, add or remove local memory exclusions."""
    normalized = str(action or "list").strip().lower()
    if normalized == "list":
        result = {"ok": True, "exclusions": local_context_api.list_exclusions(readonly=True)}
    elif normalized == "add":
        result = local_context_api.add_exclusion(path, reason=reason)
    elif normalized == "remove":
        result = local_context_api.remove_exclusion(path)
    else:
        result = {"ok": False, "error": "unknown_action", "allowed": ["list", "add", "remove"]}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_index_filetypes(action: str = "list", extension: str = "", mode: str = "extract", reason: str = "user") -> str:
    """List, include, exclude or reset local memory file extension rules."""
    normalized = str(action or "list").strip().lower()
    if normalized == "list":
        result = local_context_api.list_file_type_rules(readonly=True)
    elif normalized in {"include", "add"}:
        result = local_context_api.set_file_type_rule(extension, action=mode or "extract", reason=reason)
    elif normalized in {"exclude", "ignore"}:
        result = local_context_api.set_file_type_rule(extension, action="ignore", reason=reason)
    elif normalized in {"remove", "delete"}:
        result = local_context_api.remove_file_type_rule(extension)
    elif normalized == "reset":
        result = local_context_api.reset_file_type_rules()
    else:
        result = {"ok": False, "error": "unknown_action", "allowed": ["list", "include", "exclude", "remove", "reset"]}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_index_migrate_roots_v2(apply: bool = False) -> str:
    """Plan or apply Local Memory roots v2 cleanup."""
    result = local_context_api.migrate_roots_seed_v2(dry_run=not bool(apply))
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_context(
    query: str,
    intent: str = "answer",
    limit: int = 8,
    evidence_required: bool = True,
    current_context: str = "",
    mode: str = "compact",
    max_chars: int = 20000,
    include_entities: bool = False,
    include_relations: bool = False,
) -> str:
    """Retrieve local evidence before answering or acting.

    Use mode='compact' for normal answers. Use mode='full' only for deep
    debugging, ideally with a higher max_chars and a specific query.
    """
    result = local_context_api.context_query(
        query,
        intent=intent,
        limit=limit,
        evidence_required=evidence_required,
        current_context=current_context,
        mode=mode,
        max_chars=max_chars,
        include_entities=include_entities,
        include_relations=include_relations,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_entity_dossier(
    query: str,
    max_assets: int = 500,
    max_chunks: int = 1200,
    max_facts: int = 3000,
    max_chars: int = 20000,
) -> str:
    """Build a full local dossier for one entity with aggregates and evidence."""
    result = local_context_api.entity_dossier(
        query,
        max_assets=max_assets,
        max_chunks=max_chunks,
        max_facts=max_facts,
        max_chars=max_chars,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_context_router(query: str, intent: str = "answer", limit: int = 4, current_context: str = "", max_chars: int = 6000) -> str:
    """Return compact local context evidence suitable for injection before a reply."""
    result = local_context_api.context_router(
        query,
        intent=intent,
        limit=limit,
        current_context=current_context,
        max_chars=max_chars,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_cognitive_control_observatory(window_seconds: int = 86400) -> str:
    """Read-only metrics for Local Context, learnings, followups and intraday memory."""
    from cognitive_control_observatory import build_cognitive_control_observatory

    return json.dumps(
        build_cognitive_control_observatory(window_seconds=window_seconds),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


@mcp.tool
def nexo_local_asset_get(asset_id: str) -> str:
    """Return one indexed local asset by asset id."""
    return json.dumps(local_context_api.get_asset(asset_id), ensure_ascii=False)


@mcp.tool
def nexo_local_asset_neighbors(asset_id: str, limit: int = 30) -> str:
    """Return graph relations around one indexed local asset."""
    return json.dumps(local_context_api.get_neighbors(asset_id, limit=limit), ensure_ascii=False)


@mcp.tool
def nexo_local_index_diagnostics_tail(limit: int = 100) -> str:
    """Return recent local memory diagnostic log entries."""
    return json.dumps(local_context_api.diagnostics_tail(limit), ensure_ascii=False)


@mcp.tool
def nexo_local_index_purge(asset_id: str = "", clear_all: bool = False) -> str:
    """Purge one indexed asset or clear the full local index."""
    if clear_all:
        result = local_context_api.clear_index()
    elif asset_id:
        result = local_context_api.purge_asset(asset_id)
    else:
        result = {"ok": False, "error": "asset_id_required"}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_local_index_service_config(platform_name: str = "") -> str:
    """Render service configuration metadata for macOS, Windows or Linux."""
    return json.dumps(local_context_api.render_service_config(platform_name or None), ensure_ascii=False)


@mcp.tool
def nexo_local_index_models(action: str = "status", local_files_only: bool = True) -> str:
    """Return or warm local model state for the Local Context Layer."""
    normalized = str(action or "status").strip().lower()
    if normalized == "status":
        result = local_context_api.model_status()
    elif normalized == "warmup":
        result = local_context_api.warmup_models(local_files_only=local_files_only)
    else:
        result = {"ok": False, "error": "unknown_action", "allowed": ["status", "warmup"]}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_embedding_migration_status() -> str:
    """Return read-only status for the cognitive embedding migration."""
    from cognitive import embedding_migration_status

    return json.dumps(embedding_migration_status(), ensure_ascii=False)


@mcp.tool
def nexo_continuity_snapshot_write(
    conversation_id: str,
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    event_type: str = "turn_end",
    payload: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
) -> str:
    """Write a durable continuity snapshot for Desktop/Brain handoff."""
    return _continuity_json_dumps(
        write_snapshot(
            conversation_id=conversation_id,
            session_id=session_id,
            external_session_id=external_session_id,
            client=client,
            event_type=event_type,
            payload=payload,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool
def nexo_continuity_snapshot_read(conversation_id: str = "", session_id: str = "", limit: int = 20) -> str:
    """Read recent continuity snapshots by conversation_id or session_id."""
    return _continuity_json_dumps(
        read_snapshot(conversation_id=conversation_id, session_id=session_id, limit=limit)
    )


@mcp.tool
def nexo_continuity_resume_bundle(
    conversation_id: str = "",
    session_id: str = "",
    external_session_id: str = "",
    client: str = "",
    token_budget: int = 2000,
) -> str:
    """Build the small continuity bundle Desktop injects after restore or stale resume loss."""
    bundle = build_resume_bundle(
        conversation_id=conversation_id,
        session_id=session_id,
        external_session_id=external_session_id,
        client=client,
        token_budget=token_budget,
    )
    if not bundle.get("unsafe_sid"):
        bundle["message"] = format_bundle_text(bundle)
    return _continuity_json_dumps(bundle)


@mcp.tool
def nexo_continuity_compaction_event(
    conversation_id: str,
    session_id: str = "",
    payload: str = "",
    trace_id: str = "",
    event_type: str = "post_compact",
) -> str:
    """Persist a compaction-related continuity event into the canonical snapshot stream."""
    return _continuity_json_dumps(
        record_compaction_event(
            conversation_id=conversation_id,
            session_id=session_id,
            payload=payload,
            trace_id=trace_id,
            event_type=event_type,
        )
    )


@mcp.tool
def nexo_continuity_audit(conversation_id: str, limit: int = 50) -> str:
    """Return the forensic continuity timeline for a conversation."""
    return _continuity_json_dumps(continuity_audit(conversation_id=conversation_id, limit=limit))


@mcp.tool
def nexo_context_packet(area: str, files: str = "") -> str:
    """Build a context packet for subagent injection. Returns learnings + changes + followups + preferences + cognitive memories for a specific area.

    MUST call before delegating ANY task to a subagent. Inject the result into the subagent's prompt.

    Args:
        area: Project/area name (e.g., 'ecommerce', 'shopify', 'backend', 'mobile-app', 'nexo', 'infrastructure').
        files: Optional comma-separated file paths for additional context.
    """
    return handle_context_packet(area, files)


@mcp.tool
def nexo_recent_context_capture(
    title: str,
    summary: str = "",
    details: str = "",
    topic: str = "",
    context_key: str = "",
    state: str = "active",
    owner: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    actor: str = "nexo",
    ttl_hours: int = 24,
    metadata: str = "",
) -> str:
    """Capture/update a recent 24h context item and append an event.

    Use this for important ongoing threads that should stay mentally fresh across sessions/clients.
    """
    return handle_recent_context_capture(
        title, summary, details, topic, context_key, state, owner,
        source_type, source_id, session_id, actor, ttl_hours, metadata,
    )


@mcp.tool
def nexo_recent_context(query: str = "", context_key: str = "", hours: int = 24, limit: int = 8) -> str:
    """Read recent hot context and continuity events from the last N hours."""
    return handle_recent_context(query, context_key, hours, limit)


@mcp.tool
def nexo_pre_action_context(query: str = "", context_key: str = "", session_id: str = "", hours: int = 24, limit: int = 8) -> str:
    """Build the 24h recent-context bundle that should be reviewed before acting.

    Especially useful for emails, orchestrators, and any work where the same topic may reappear hours later.
    """
    return handle_pre_action_context(query, context_key, session_id, hours, limit)


@mcp.tool
def nexo_pre_answer_route(
    query: str = "",
    intent: str = "auto",
    sid: str = "",
    conversation_id: str = "",
    area: str = "",
    files: str = "",
    surface: str = "pre_answer",
    budget_ms: int = 0,
    token_budget: int = 0,
    current_context: str = "",
    client: str = "mcp",
) -> str:
    """Route a user turn through pre-answer continuity sources before responding."""
    from pre_answer_runtime import run_pre_answer_route

    result = run_pre_answer_route(
        {
            "query": query,
            "intent": intent or "auto",
            "sid": sid,
            "conversation_id": conversation_id,
            "area": area,
            "files": files,
            "surface": surface or "pre_answer",
            "budget_ms": budget_ms,
            "token_budget": token_budget,
            "current_context": current_context,
            "client": client or "mcp",
            "source": client or "mcp",
        }
    )
    result["injection_text"] = result.get("rendered") or ""
    return json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_recent_context_resolve(
    context_key: str = "",
    topic: str = "",
    resolution: str = "",
    actor: str = "nexo",
    session_id: str = "",
    source_type: str = "",
    source_id: str = "",
    ttl_hours: int = 24,
) -> str:
    """Resolve a recent hot-context item and append a resolution event."""
    return handle_recent_context_resolve(
        context_key, topic, resolution, actor, session_id, source_type, source_id, ttl_hours
    )


@mcp.tool
def nexo_hot_context_list(hours: int = 24, limit: int = 10, state: str = "") -> str:
    """List hot-context items currently alive in the recent continuity window."""
    return handle_hot_context_list(hours, limit, state)


@mcp.tool
def nexo_transcript_recent(hours: int = 24, client: str = "", limit: int = 10) -> str:
    """List recent Claude Code / Codex transcripts visible to NEXO."""
    return handle_transcript_recent(hours, client, limit)


@mcp.tool
def nexo_transcript_search(query: str = "", hours: int = 24, client: str = "", limit: int = 10) -> str:
    """Search recent transcripts directly when recall/hot-context are not enough."""
    return handle_transcript_search(query, hours, client, limit)


@mcp.tool
def nexo_transcript_read(session_ref: str = "", transcript_path: str = "", client: str = "", max_messages: int = 80) -> str:
    """Read a full transcript fallback by session id, transcript display name, session_uid, or exact path."""
    return handle_transcript_read(session_ref, transcript_path, client, max_messages)


@mcp.tool
def nexo_memory_event_list(
    query: str = "",
    event_type: str = "",
    source_type: str = "",
    source_id: str = "",
    session_id: str = "",
    project_key: str = "",
    limit: int = 20,
) -> str:
    """List raw Memory Observations v2 events captured by hooks/tasks."""
    return handle_memory_event_list(query, event_type, source_type, source_id, session_id, project_key, limit)


@mcp.tool
def nexo_memory_event_stats(days: int = 7) -> str:
    """Summarize raw Memory Observations v2 event counts."""
    return handle_memory_event_stats(days)


@mcp.tool
def nexo_memory_observation_process(limit: int = 25, backfill_limit: int = 100, pending_sla_seconds: int = 3600) -> str:
    """Process pending raw memory events into passive observations."""
    from memory_observation_processor import process_incremental

    return json.dumps(
        process_incremental(
            process_limit=limit,
            backfill_limit=backfill_limit,
            pending_sla_seconds=pending_sla_seconds,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


@mcp.tool
def nexo_intraday_memory_cycle(limit: int = 20, backfill_limit: int = 20, pending_sla_seconds: int = 3600) -> str:
    """Run a low-limit daytime memory observation cycle that only publishes evidence-backed intraday facts."""
    from memory_observation_processor import process_intraday_cycle

    return json.dumps(
        process_intraday_cycle(
            process_limit=limit,
            backfill_limit=backfill_limit,
            pending_sla_seconds=pending_sla_seconds,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


@mcp.tool
def nexo_memory_observation_list(
    query: str = "",
    observation_type: str = "",
    session_id: str = "",
    project_key: str = "",
    status: str = "",
    limit: int = 20,
) -> str:
    """List passive Memory Observations v2 rows."""
    return handle_memory_observation_list(query, observation_type, session_id, project_key, status, limit)


@mcp.tool
def nexo_memory_observation_stats(days: int = 7) -> str:
    """Summarize passive Memory Observations v2 rows and queue status."""
    return handle_memory_observation_stats(days)


@mcp.tool
def nexo_memory_backfill(sources: str = "", limit: int = 100) -> str:
    """Backfill Memory Observations v2 from existing Brain tables."""
    return handle_memory_backfill(sources, limit)


@mcp.tool
def nexo_memory_health() -> str:
    """Return Memory Observations v2 health and table status."""
    return handle_memory_health()


@mcp.tool
def nexo_memory_maintenance(
    process_limit: int = 100,
    retry_failed: bool = True,
    backfill_sources: str = "",
    backfill_limit: int = 0,
) -> str:
    """Run safe Memory Observations v2 maintenance."""
    return handle_memory_maintenance(process_limit, retry_failed, backfill_sources, backfill_limit)


@mcp.tool
def nexo_memory_search(
    query: str,
    project_hint: str = "",
    time_range: str = "",
    depth: str = "brief",
    limit: int = 10,
) -> str:
    """Search Memory Observations v2 with evidence-first results."""
    return handle_memory_search(query, project_hint, time_range, depth, limit)


@mcp.tool
def nexo_memory_answer(
    query: str,
    project_hint: str = "",
    time_range: str = "",
    limit: int = 5,
) -> str:
    """Answer a memory question only when evidence exists."""
    return handle_memory_answer(query, project_hint, time_range, limit)


@mcp.tool
def nexo_memory_timeline(
    query: str = "",
    project_hint: str = "",
    time_range: str = "",
    limit: int = 20,
) -> str:
    """Return a chronological Memory Observations v2 timeline."""
    return handle_memory_timeline(query, project_hint, time_range, limit)


@mcp.tool
def nexo_evidence_search(
    query: str = "",
    artifact: str = "",
    task_id: str = "",
    workflow_id: str = "",
    conversation_id: str = "",
    file_path: str = "",
    limit: int = 20,
) -> str:
    """Search concrete evidence across tasks, workflows, change logs, diaries and continuity stores."""
    from evidence_ledger import evidence_to_dicts, search_evidence

    return json.dumps(
        {
            "ok": True,
            "evidence": evidence_to_dicts(
                search_evidence(
                    query,
                    artifact=artifact,
                    task_id=task_id,
                    workflow_id=workflow_id,
                    conversation_id=conversation_id,
                    file_path=file_path,
                    limit=limit,
                )
            ),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


@mcp.tool
def nexo_evidence_record(
    summary: str,
    source_type: str = "evidence_ledger",
    source_id: str = "",
    object_type: str = "artifact",
    object_ref: str = "",
    action: str = "recorded",
    session_id: str = "",
    conversation_id: str = "",
    verification: str = "",
    idempotency_key: str = "",
) -> str:
    """Record a compact evidence pointer without storing raw command output."""
    from evidence_ledger import record_evidence

    try:
        entry = record_evidence(
            summary=summary,
            source_type=source_type,
            source_id=source_id,
            object_type=object_type,
            object_ref=object_ref,
            action=action,
            session_id=session_id,
            conversation_id=conversation_id,
            verification=verification,
            idempotency_key=idempotency_key,
        )
        return json.dumps({"ok": True, "evidence": entry.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": "evidence_record_failed", "detail": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


@mcp.tool
def nexo_saved_not_used_audit(markdown: bool = False) -> str:
    """Audit stores that write data without verified later consumption."""
    from saved_not_used_audit import audit_saved_not_used, format_markdown

    report = audit_saved_not_used()
    if markdown:
        return format_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


@mcp.tool
def nexo_automation_supervisor(markdown: bool = False) -> str:
    """Read-only supervisor report for automations, cron runs, cron spool and Evolution policy."""
    from automation_supervisor import audit_automation, format_markdown

    report = audit_automation()
    if markdown:
        return format_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


@mcp.tool
def nexo_automation_reconcile(apply: bool = False) -> str:
    """Build or apply the safe automation reconciliation plan."""
    from automation_reconciler import apply_reconciliation_plan, build_reconciliation_plan

    plan = build_reconciliation_plan()
    if apply:
        result = apply_reconciliation_plan(plan)
        return json.dumps({"ok": result.get("ok", False), "plan": plan, "apply": result}, ensure_ascii=False, indent=2)
    return json.dumps(plan, ensure_ascii=False, indent=2)


@mcp.tool
def nexo_system_catalog(section: str = "", query: str = "", limit: int = 20) -> str:
    """Read NEXO's live system catalog built from core tools, plugins, skills, scripts, crons, projects, and artifacts."""
    return handle_system_catalog(section, query, limit)


@mcp.tool
def nexo_tool_explain(name: str) -> str:
    """Explain a live NEXO tool/capability from the generated system catalog."""
    return handle_tool_explain(name)


@mcp.tool
def nexo_product_capabilities(query: str = "", category: str = "", status: str = "", limit: int = 20) -> str:
    """Search the structured NEXO product capability catalog."""
    return handle_product_capabilities(query, category, status, limit)


@mcp.tool
def nexo_capability_explain(capability_id: str = "", query: str = "", locale: str = "es") -> str:
    """Explain one NEXO product capability with source and safety context."""
    return handle_capability_explain(capability_id, query, locale)


@mcp.tool
def nexo_product_answer(question: str, locale: str = "es", limit: int = 5) -> str:
    """Answer a NEXO product question using the structured product catalog."""
    return handle_product_answer(question, locale, limit)


@mcp.tool
def nexo_product_surface_status(surface: str = "", limit: int = 50) -> str:
    """Show which NEXO product capabilities are exposed by a product surface."""
    return handle_product_surface_status(surface, limit)


@mcp.tool
def nexo_product_knowledge_validate() -> str:
    """Validate the structured NEXO product knowledge catalog."""
    return handle_product_knowledge_validate()


@mcp.tool
def nexo_guardian_rule_override(rule_id: str, mode: str = "", ttl: str = "24h") -> str:
    """Temporarily override a Guardian rule's mode (Plan Consolidado 0.17).

    Writes to ``~/.nexo/config/guardian-runtime-overrides.json`` which
    ``guardian_config.rule_mode`` already honours at read time. Useful when
    a rule is noisy during an incident and needs to drop to shadow for an
    hour without a server restart.

    Args:
        rule_id: The full rule identifier (e.g. ``R13_pre_edit_guard``).
        mode: One of ``off`` / ``shadow`` / ``soft`` / ``hard``. Pass empty
            string together with a rule_id to clear an existing override.
            Core rules (R13/R14/R16/R25/R30) reject ``off``.
        ttl: Window for the override. One of ``1h`` / ``24h`` / ``session``
            (session = 12 h best-effort cap). Default ``24h``.

    Returns a JSON string ``{ok, rule_id, mode, ttl_label, expires_at, path}``
    on success, ``{ok:false, error}`` on invalid arguments.
    """
    return handle_guardian_rule_override(rule_id, mode, ttl)


@mcp.tool
def nexo_smart_startup() -> str:
    """Pre-load relevant cognitive memories based on pending followups, due reminders, and last session topics.

    Call during startup (after nexo_startup) to ensure the session starts with the right context loaded.
    Returns up to 10 memories matching the current operational state.
    """
    return handle_smart_startup_query()


@mcp.tool
def nexo_session_portable_context(sid: str = "") -> str:
    """Build a portable handoff packet for another client/runtime.

    Use this when another client should continue the same work with explicit
    task/checkpoint/goal/workflow context instead of relying on memory alone.
    """
    return handle_session_portable_context(sid)


@mcp.tool
def nexo_session_export_bundle(sid: str = "", path: str = "") -> str:
    """Export a machine-readable session bundle for cross-client handoff or archival."""
    return handle_session_export_bundle(sid, path)


# ── Session Checkpoints (auto-compaction continuity) ──────────────

@mcp.tool
def nexo_checkpoint_save(
    sid: str,
    task: str = '',
    task_status: str = 'active',
    active_files: str = '[]',
    current_goal: str = '',
    decisions_summary: str = '',
    errors_found: str = '',
    reasoning_thread: str = '',
    next_step: str = ''
) -> str:
    """Save a session checkpoint for auto-compaction continuity.

    Call this BEFORE context compaction to preserve session state.
    The PostCompact hook reads this checkpoint and re-injects it as a
    Core Memory Block, so the session continues seamlessly.

    Args:
        sid: Session ID.
        task: Current task description.
        task_status: One of 'active', 'investigating', 'fixing', 'deploying', 'blocked'.
        active_files: JSON array of file paths currently being worked on.
        current_goal: What you're trying to achieve right now (1-2 sentences).
        decisions_summary: Recent decisions with brief reasoning (2-3 lines).
        errors_found: Errors encountered and their status (resolved/open).
        reasoning_thread: Your current chain of thought (1-2 sentences).
        next_step: The concrete next action to take.
    """
    from db import save_checkpoint
    result = save_checkpoint(
        sid=sid, task=task, task_status=task_status,
        active_files=active_files, current_goal=current_goal,
        decisions_summary=decisions_summary, errors_found=errors_found,
        reasoning_thread=reasoning_thread, next_step=next_step,
    )
    return f"Checkpoint saved for {sid}. Compaction #{result['compaction_count']}. PostCompact will re-inject this as Core Memory Block."


@mcp.tool
def nexo_checkpoint_read(sid: str = '') -> str:
    """Read the latest session checkpoint. Used by PostCompact hook and for manual recovery.

    Args:
        sid: Session ID. If empty, returns the most recent checkpoint from any session.
    """
    from db import read_checkpoint
    cp = read_checkpoint(sid)
    if not cp:
        return "No checkpoint found."

    lines = [f"CHECKPOINT for {cp['sid']} (compaction #{cp['compaction_count']})"]
    lines.append(f"Task: {cp['task']} ({cp['task_status']})")
    if cp.get('current_goal'):
        lines.append(f"Goal: {cp['current_goal']}")
    if cp.get('active_files') and cp['active_files'] != '[]':
        lines.append(f"Files: {cp['active_files']}")
    if cp.get('decisions_summary'):
        lines.append(f"Decisions: {cp['decisions_summary']}")
    if cp.get('errors_found'):
        lines.append(f"Errors: {cp['errors_found']}")
    if cp.get('reasoning_thread'):
        lines.append(f"Context: {cp['reasoning_thread']}")
    if cp.get('next_step'):
        lines.append(f"Next: {cp['next_step']}")
    lines.append(f"Updated: {cp['updated_at']}")
    return "\n".join(lines)


# ── File coordination (3 tools) ───────────────────────────────────

@mcp.tool
def nexo_track(sid: str, paths: list[str]) -> str:
    """Track files being edited. Detects conflicts with other sessions.

    MUST call before editing any shared file.
    Args:
        sid: Your session ID.
        paths: List of absolute file paths to track.
    """
    return handle_track(sid, paths)


@mcp.tool
def nexo_untrack(sid: str, paths: list[str] | None = None) -> str:
    """Stop tracking files. If no paths given, releases all.

    Args:
        sid: Your session ID.
        paths: File paths to release. Omit to release all.
    """
    return handle_untrack(sid, paths)


@mcp.tool
def nexo_files() -> str:
    """Show all tracked files across all active sessions with conflict detection."""
    return handle_files()


# ── Messaging (4 tools) ───────────────────────────────────────────

@mcp.tool
def nexo_send(from_sid: str, to_sid: str, text: str) -> str:
    """Send a fire-and-forget message to another session or broadcast.

    Args:
        from_sid: Your session ID.
        to_sid: Target session ID, or 'all' for broadcast.
        text: Message content.
    """
    return handle_send(from_sid, to_sid, text)


@mcp.tool
def nexo_ask(from_sid: str, to_sid: str, question: str) -> str:
    """Ask a question to another session (they see it on next heartbeat).

    Args:
        from_sid: Your session ID.
        to_sid: Target session ID.
        question: The question text.
    Returns: Question ID (qid) for checking the answer later.
    """
    return handle_ask(from_sid, to_sid, question)


@mcp.tool
def nexo_answer(qid: str, answer: str) -> str:
    """Answer a pending question from another session.

    Args:
        qid: The question ID shown in heartbeat output.
        answer: Your response.
    """
    return handle_answer(qid, answer)


@mcp.tool
def nexo_check_answer(qid: str) -> str:
    """Check if a question has been answered.

    Args:
        qid: The question ID from nexo_ask.
    """
    return handle_check_answer(qid)


# ── Operations: Reminders + Menu (2 tools, read-only) ─────────────

@mcp.tool
def nexo_reminders(filter: str = "due") -> str:
    """Check reminders and followups.

    Args:
        filter: 'due', 'all', 'followups', 'completed', 'deleted', 'history', or 'any'
    """
    return handle_reminders(filter)


@mcp.tool
def nexo_menu() -> str:
    """Generate the NEXO operations center menu with alerts and active sessions.

    Shows: date, due alerts, all menu items by category, active sessions.
    Uses box-drawing characters for formatting.
    """
    return handle_menu()


# ── Reminders CRUD (7 tools) ──────────────────────────────────────

@mcp.tool
def nexo_reminder_create(
    id: str,
    description: str,
    date: str = "",
    category: str = "general",
    internal: str = "",
    owner: str = "",
) -> str:
    """Create a new reminder for the user.

    Args:
        id: Unique ID starting with 'R' (e.g., R90).
        description: What needs to be done.
        date: Target date YYYY-MM-DD (optional).
        category: One of: decisions, tasks, waiting, ideas, general.
        internal: '1'/'true' to mark as agent bookkeeping (hidden from
                  default user views). Leave empty to auto-classify.
        owner: 'user' | 'waiting' | 'agent' | 'shared'. Leave empty to
               auto-classify by description heuristic.
    """
    return handle_reminder_create(id, description, date, category, internal, owner)


@mcp.tool
def nexo_reminder_get(id: str) -> str:
    """Read a reminder with its history and usage rules.

    IMPORTANT: before update/delete/restore/note, call this tool first and use the returned READ_TOKEN.
    """
    return handle_reminder_get(id)


@mcp.tool
def nexo_reminder_update(
    id: str,
    description: str = "",
    date: str = "",
    status: str = "",
    category: str = "",
    internal: str = "",
    owner: str = "",
    read_token: str = "",
) -> str:
    """Update fields of an existing reminder. Only non-empty fields are changed.

    IMPORTANT: call `nexo_reminder_get` first and pass its READ_TOKEN.

    Args:
        id: Reminder ID (e.g., R87).
        description: New description (optional).
        date: New date YYYY-MM-DD (optional).
        status: New status (optional).
        category: New category (optional).
        internal: '1'/'0' to re-classify visibility (optional).
        owner: New 'user'|'waiting'|'agent'|'shared' (optional).
        read_token: Token returned by `nexo_reminder_get`.
    """
    return handle_reminder_update(id, description, date, status, category, internal, owner, read_token)


@mcp.tool
def nexo_reminder_complete(id: str) -> str:
    """Mark a reminder as completed with today's date.

    Args:
        id: Reminder ID (e.g., R87).
    """
    return handle_reminder_complete(id)


@mcp.tool
def nexo_reminder_note(id: str, note: str, read_token: str = "", actor: str = "nexo") -> str:
    """Append a note to reminder history.

    IMPORTANT: call `nexo_reminder_get` first and pass its READ_TOKEN.

    Args:
        id: Reminder ID (e.g., R87).
        note: Operational note to append to history.
        read_token: Token returned by `nexo_reminder_get`.
        actor: Actor label for the history note.
    """
    return handle_reminder_note(id, note, read_token, actor)


@mcp.tool
def nexo_reminder_restore(id: str, read_token: str = "") -> str:
    """Restore a soft-deleted reminder back to PENDING.

    IMPORTANT: call `nexo_reminder_get` first and pass its READ_TOKEN.

    Args:
        id: Reminder ID (e.g., R87).
        read_token: Token returned by `nexo_reminder_get`.
    """
    return handle_reminder_restore(id, read_token)


@mcp.tool
def nexo_reminder_delete(id: str, read_token: str = "") -> str:
    """Soft-delete a reminder.

    IMPORTANT: call `nexo_reminder_get` first and pass its READ_TOKEN.

    Args:
        id: Reminder ID (e.g., R87).
        read_token: Token returned by `nexo_reminder_get`.
    """
    return handle_reminder_delete(id, read_token)


# ── Followups CRUD (7 tools) ──────────────────────────────────────

@mcp.tool
def nexo_followup_create(
    id: str,
    description: str,
    date: str = "",
    verification: str = "",
    reasoning: str = "",
    recurrence: str = "",
    priority: str = "medium",
    internal: str = "",
    owner: str = "",
    exception: str = "",
) -> str:
    """Create a new agent followup (autonomous task).

    Args:
        id: Unique ID starting with 'NF' (e.g., NF-MCP2).
        description: What to verify/do.
        date: Target date YYYY-MM-DD (optional).
        verification: How to verify completion (optional).
        reasoning: WHY this followup exists — what decision/context led to it (optional).
        recurrence: Auto-regenerate pattern (optional). Formats: 'weekly:monday', 'monthly:1', 'monthly:15', 'quarterly'.
                    When completed, a new followup is auto-created with the next date. The completed one is archived with date suffix.
        priority: critical, high, medium, low (default: medium).
        internal: '1'/'true' hides from default user views (agent
                  bookkeeping, protocol, audit). Leave empty to
                  auto-classify by ID prefix.
        owner: 'user' | 'waiting' | 'agent' | 'shared'. Leave empty
               for auto-classification.
        exception: Reason this followup should be allowed even under an
                   active autonomy mandate (NF-DS-45569A27). Valid only
                   for the three pre-approved cases: >1GB download,
                   credential the operator must physically enter, or a
                   presence-dependent session with María/Nora.
    """
    return handle_followup_create(
        id, description, date, verification, reasoning, recurrence, priority,
        internal, owner, exception=exception,
    )


@mcp.tool
def nexo_followup_get(id: str) -> str:
    """Read a followup with its history and usage rules.

    IMPORTANT: before update/delete/restore/note, call this tool first and use the returned READ_TOKEN.
    """
    return handle_followup_get(id)


@mcp.tool
def nexo_followup_lifecycle(limit: int = 500) -> str:
    """Return followups grouped by lifecycle lane for runner, dashboard and startup parity."""
    from db import followup_lifecycle_snapshot

    return json.dumps(
        followup_lifecycle_snapshot(limit=limit),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


@mcp.tool
def nexo_followup_update(
    id: str,
    description: str = "",
    date: str = "",
    verification: str = "",
    status: str = "",
    priority: str = "",
    internal: str = "",
    owner: str = "",
    read_token: str = "",
) -> str:
    """Update fields of an existing followup. Only non-empty fields are changed.

    IMPORTANT: call `nexo_followup_get` first and pass its READ_TOKEN.

    Args:
        id: Followup ID (e.g., NF45).
        description: New description (optional).
        date: New date YYYY-MM-DD (optional).
        verification: New verification text (optional).
        status: New status (optional).
        priority: critical, high, medium, low (optional).
        internal: '1'/'0' to re-classify visibility (optional).
        owner: New 'user'|'waiting'|'agent'|'shared' (optional).
        read_token: Token returned by `nexo_followup_get`.
    """
    return handle_followup_update(
        id, description, date, verification, status, priority,
        internal, owner, read_token,
    )


@mcp.tool
def nexo_followup_complete(id: str, result: str = "") -> str:
    """Mark a followup as completed. Appends result to verification field.

    Args:
        id: Followup ID (e.g., NF45).
        result: What was found/done (optional).
    """
    return handle_followup_complete(id, result)


@mcp.tool
def nexo_followup_note(id: str, note: str, read_token: str = "", actor: str = "nexo") -> str:
    """Append a note to followup history.

    IMPORTANT: call `nexo_followup_get` first and pass its READ_TOKEN.

    Args:
        id: Followup ID (e.g., NF45).
        note: Operational note to append to history.
        read_token: Token returned by `nexo_followup_get`.
        actor: Actor label for the history note.
    """
    return handle_followup_note(id, note, read_token, actor)


@mcp.tool
def nexo_followup_restore(id: str, read_token: str = "") -> str:
    """Restore a soft-deleted followup back to PENDING.

    IMPORTANT: call `nexo_followup_get` first and pass its READ_TOKEN.

    Args:
        id: Followup ID (e.g., NF45).
        read_token: Token returned by `nexo_followup_get`.
    """
    return handle_followup_restore(id, read_token)


@mcp.tool
def nexo_followup_delete(id: str, read_token: str = "") -> str:
    """Soft-delete a followup.

    IMPORTANT: call `nexo_followup_get` first and pass its READ_TOKEN.

    Args:
        id: Followup ID (e.g., NF45).
        read_token: Token returned by `nexo_followup_get`.
    """
    return handle_followup_delete(id, read_token)


# ── Learnings CRUD (5 tools) ──────────────────────────────────────

@mcp.tool
def nexo_learning_add(
    category: str,
    title: str,
    content: str,
    reasoning: str = "",
    prevention: str = "",
    applies_to: str = "",
    review_days: int = 30,
    priority: str = "medium",
    supersedes_id: int = 0,
    source_authority: str = "explicit_instruction",
) -> str:
    """Add a new learning (resolved error, pattern, gotcha).

    Args:
        category: Free-form category name (e.g., 'backend', 'frontend', 'devops', 'infrastructure', 'security'). Use consistent names across learnings.
        title: Short title for the learning.
        content: Full description with context and solution.
        reasoning: WHY this matters — what led to discovering this (optional).
        prevention: Concrete rule/check that prevents repeating this mistake (optional).
        applies_to: Files, systems, or areas this learning applies to (optional).
        review_days: Days until this learning should be reviewed again (default 30).
        priority: critical, high, medium, low (default: medium). Critical/high never decay below floor.
        supersedes_id: Existing learning ID this new canonical rule replaces (optional).
        source_authority: Authority tier for conflict resolution: francisco_correction, explicit_instruction, code_test_evidence, deep_sleep, inference.
    """
    return handle_learning_add(
        category, title, content, reasoning,
        prevention=prevention, applies_to=applies_to,
        review_days=review_days, priority=priority, supersedes_id=supersedes_id,
        source_authority=source_authority,
    )


@mcp.tool
def nexo_learning_resolve_candidate(
    category: str,
    title: str,
    content: str,
    reasoning: str = "",
    prevention: str = "",
    applies_to: str = "",
    priority: str = "medium",
    supersedes_id: int = 0,
    source_authority: str = "inference",
) -> str:
    """Dry-run the canonical learning resolver without creating or updating learnings."""
    return handle_learning_resolve_candidate(
        category=category,
        title=title,
        content=content,
        reasoning=reasoning,
        prevention=prevention,
        applies_to=applies_to,
        priority=priority,
        supersedes_id=supersedes_id,
        source_authority=source_authority,
    )


@mcp.tool
def nexo_learning_search(query: str, category: str = "") -> str:
    """Search learnings by keyword. Searches title and content.

    Args:
        query: Search term.
        category: Filter by category (optional).
    """
    return handle_learning_search(query, category)


@mcp.tool
def nexo_learning_apply_retroactively(
    learning_id: int,
    lookback_days: int = 14,
    max_matches: int = 5,
    min_score: float = 0.4,
    dry_run: bool = False,
) -> str:
    """Scan recent decisions and surface those that conflict with a learning's prevention rule.

    Closes Fase 2 item 3 of NEXO-AUDIT-2026-04-11. Use this when you add a new
    rule and want to retroactively check whether past decisions still hold.
    Creates deterministic NF-RETRO-L<learning>-D<decision> followups so the
    helper is idempotent across reruns. nexo_learning_add invokes this
    automatically when the new learning has a `prevention` field — call this
    tool manually only when you want to re-scan with a longer window or a
    different threshold.

    Args:
        learning_id: ID of the learning to apply.
        lookback_days: How many days back to scan decisions (default 14).
        max_matches: Cap on followups created per call (default 5).
        min_score: Match threshold in [0.0, 1.0] (default 0.4).
        dry_run: If True, scores matches but does not create followups.
    """
    import json as _json
    from retroactive_learnings import apply_learning_retroactively

    result = apply_learning_retroactively(
        int(learning_id),
        lookback_days=int(lookback_days),
        max_matches=int(max_matches),
        min_score=float(min_score),
        dry_run=bool(dry_run),
    )
    return _json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool
def nexo_hook_runs(
    hours: int = 24,
    hook_name: str = "",
    status: str = "",
    limit: int = 50,
    summary_only: bool = False,
) -> str:
    """List recent hook lifecycle runs and per-hook health summary.

    Closes Fase 3 item 7 of NEXO-AUDIT-2026-04-11. Each NEXO hook
    (session-start, post-compact, pre-compact, inbox-hook, etc.) writes
    a row to hook_runs when it finishes via scripts/nexo-hook-record.py.
    This tool reads them back so the agent can answer "is the hook
    pipeline healthy?" without needing the dashboard or grepping log files.

    Args:
        hours: How far back to look (default 24).
        hook_name: Optional substring filter (LIKE %name%).
        status: Optional exact status filter (ok|error|skipped|timeout|blocked).
        limit: Max raw rows to return when summary_only=False (default 50).
        summary_only: If True, return only the per-hook health summary
                       (success rate, p50/p95 duration, unhealthy hooks)
                       and skip the raw row list.
    """
    import json as _json
    from hook_observability import list_recent_hook_runs, hook_health_summary

    summary = hook_health_summary(hours=int(hours))
    if summary_only:
        return _json.dumps(summary, ensure_ascii=False, indent=2)

    runs = list_recent_hook_runs(
        hours=int(hours),
        hook_name=hook_name,
        status=status,
        limit=int(limit),
    )
    return _json.dumps({"summary": summary, "runs": runs}, ensure_ascii=False, indent=2)


@mcp.tool
def nexo_learning_update(
    id: int,
    title: str = "",
    content: str = "",
    category: str = "",
    reasoning: str = "",
    prevention: str = "",
    applies_to: str = "",
    status: str = "",
    review_days: int = 0,
    priority: str = "",
    supersedes_id: int = 0,
) -> str:
    """Update a learning entry. Only non-empty fields are changed.

    Args:
        id: Learning ID number.
        title: New title (optional).
        content: New content (optional).
        category: New category (optional).
        reasoning: New reasoning/context (optional).
        prevention: New prevention rule (optional).
        applies_to: New applies_to target(s) (optional).
        status: New status such as active/superseded (optional).
        review_days: New review interval in days (optional).
        priority: critical, high, medium, low (optional).
        supersedes_id: Existing learning ID this updated canonical rule replaces (optional).
    """
    return handle_learning_update(
        id, title, content, category,
        reasoning=reasoning, prevention=prevention, applies_to=applies_to,
        status=status, review_days=review_days, priority=priority,
        supersedes_id=supersedes_id,
    )


@mcp.tool
def nexo_learning_delete(id: int) -> str:
    """Delete a learning entry.

    Args:
        id: Learning ID number.
    """
    return handle_learning_delete(id)


@mcp.tool
def nexo_learning_list(category: str = "", created_after: str = "", created_before: str = "") -> str:
    """List all learnings, grouped by category.

    Args:
        category: Filter by category (optional). If empty, shows all grouped.
        created_after: Filter to learnings created at or after this date/time (optional).
        created_before: Filter to learnings created at or before this date/time (optional).
    """
    return handle_learning_list(category, created_after, created_before)


@mcp.tool
def nexo_learning_quality(id: int = 0, category: str = "", status: str = "active", limit: int = 20) -> str:
    """Score learning quality so fragile rules can be strengthened before they mislead guard or retrieval.

    Args:
        id: Specific learning ID to inspect (optional).
        category: Filter by category (optional).
        status: Filter by lifecycle status such as active/superseded (default active).
        limit: Max learnings to score when listing (default 20).
    """
    return handle_learning_quality(id=id, category=category, status=status, limit=limit)


# ── Search index ──────────────────────────────────────────────────

@mcp.tool
def nexo_reindex() -> str:
    """Force full rebuild of the FTS5 search index. Use after bulk changes or if search seems stale."""
    conn = get_db()
    rebuild_fts_index(conn)
    count = conn.execute("SELECT COUNT(*) FROM unified_search").fetchone()[0]
    sources = conn.execute("SELECT source, COUNT(*) as cnt FROM unified_search GROUP BY source ORDER BY cnt DESC").fetchall()
    lines = [f"Index rebuilt: {count} documentos"]
    for s in sources:
        lines.append(f"  {s[0]:12s} → {s[1]}")
    return "\n".join(lines)


@mcp.tool
def nexo_index_add_dir(path: str, dir_type: str = "code",
                       patterns: str = "*.php,*.js,*.json,*.py,*.ts,*.tsx",
                       notes: str = "") -> str:
    """Register a new directory for FTS5 search indexing. Survives restarts.

    Args:
        path: Absolute path to directory (supports ~).
        dir_type: 'code' for source files, 'md' for markdown docs.
        patterns: Comma-separated glob patterns (only for code type).
        notes: Description of what this directory contains.
    """
    result = fts_add_dir(path, dir_type, patterns, notes)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Directory registered: {result['path']} ({result['dir_type']}, patterns: {result['patterns']})\nUse nexo_reindex to index now."


@mcp.tool
def nexo_index_remove_dir(path: str) -> str:
    """Remove a directory from FTS5 indexing and clean up its entries.

    Args:
        path: Path to directory to remove.
    """
    result = fts_remove_dir(path)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Directory removed from index: {result['removed']}"


@mcp.tool
def nexo_index_dirs() -> str:
    """List all directories being indexed by FTS5 (builtin + dynamic)."""
    dirs = fts_list_dirs()
    if not dirs:
        return "No directories configured."
    lines = ["INDEXED DIRECTORIES:"]
    for d in dirs:
        source_tag = "⚙️" if d["source"] == "builtin" else "➕"
        notes = f" — {d['notes']}" if d.get("notes") else ""
        lines.append(f"  {source_tag} [{d['type']}] {d['path']}")
        lines.append(f"       patterns: {d['patterns']}{notes}")
    return "\n".join(lines)


# ── Credentials CRUD (5 tools) ────────────────────────────────────

@mcp.tool
def nexo_credential_get(service: str, key: str = "") -> str:
    """Get credential value(s) for a service.

    Args:
        service: Service name (e.g., google-ads, meta-ads, shopify).
        key: Specific key (optional). If empty, returns all for the service.
    """
    return handle_credential_get(service, key)


@mcp.tool
def nexo_credential_create(service: str, key: str, value: str, notes: str = "") -> str:
    """Store a new credential.

    Args:
        service: Service name (e.g., google-ads, cloudflare).
        key: Key name (e.g., api_key, token, ssh).
        value: The secret value.
        notes: Description or context (optional).
    """
    return handle_credential_create(service, key, value, notes)


@mcp.tool
def nexo_credential_update(service: str, key: str, value: str = "", notes: str = "") -> str:
    """Update a credential's value and/or notes.

    Args:
        service: Service name.
        key: Key name.
        value: New value (optional).
        notes: New notes (optional).
    """
    return handle_credential_update(service, key, value, notes)


@mcp.tool
def nexo_credential_delete(service: str, key: str = "") -> str:
    """Delete credential(s). If no key, deletes all for the service.

    Args:
        service: Service name.
        key: Specific key (optional). If empty, deletes ALL for service.
    """
    return handle_credential_delete(service, key)


@mcp.tool
def nexo_credential_list(service: str = "") -> str:
    """List credentials (names and notes only, no values).

    Args:
        service: Filter by service (optional). If empty, shows all.
    """
    return handle_credential_list(service)


# ── Task History (3 tools) ────────────────────────────────────────

@mcp.tool
def nexo_task_log(task_num: str, task_name: str, notes: str = "", reasoning: str = "") -> str:
    """Record that an operational task was executed.

    Args:
        task_num: Task number from the checklist (e.g., '7', '7b').
        task_name: Task name (e.g., 'Google Ads').
        notes: Execution summary (optional).
        reasoning: WHY this task was executed now — what triggered it (optional).
    """
    return handle_task_log(task_num, task_name, notes, reasoning)


@mcp.tool
def nexo_task_list(task_num: str = "", days: int = 30) -> str:
    """Show execution history for operational tasks.

    Args:
        task_num: Filter by task number (optional).
        days: How many days back to show (default 30).
    """
    return handle_task_list(task_num, days)


@mcp.tool
def nexo_task_frequency() -> str:
    """Check which operational tasks are overdue based on their frequency.

    Compares last execution date vs configured frequency.
    Returns overdue tasks or 'all tasks up to date'.
    """
    return handle_task_frequency()


# ── Plugin Management (3 tools) ─────────────────────────────────

@mcp.tool
def nexo_plugin_load(filename: str) -> str:
    """Load or reload a plugin. Searches repo plugins/ first, then NEXO_HOME/personal/plugins/.

    Args:
        filename: Plugin filename (e.g., 'entities.py').
    """
    try:
        n = load_plugin(mcp, filename)
        return f"Plugin {filename}: {n} tools registered."
    except Exception as e:
        return f"Error loading plugin {filename}: {e}"


@mcp.tool
def nexo_plugin_list() -> str:
    """List all loaded plugins and their tools, showing source (repo/personal)."""
    plugins = list_plugins()
    if not plugins:
        return "No plugins loaded."
    lines = ["LOADED PLUGINS:"]
    for p in plugins:
        names = p["tool_names"] or "(no tools)"
        source = p.get("source", "repo")
        lines.append(f"  [{source}] {p['filename']} — {p['tools_count']} tools: {names}")
    return "\n".join(lines)


@mcp.tool
def nexo_plugin_remove(filename: str) -> str:
    """Unregister a plugin's tools from MCP (does not delete files).

    Args:
        filename: Plugin filename (e.g., 'entities.py').
    """
    try:
        removed = remove_plugin(mcp, filename)
        if removed:
            return f"Plugin {filename} unregistered. Tools removed: {', '.join(removed)}"
        return f"Plugin {filename} unregistered (had no registered tools)."
    except Exception as e:
        return f"Error removing plugin {filename}: {e}"


# ── Drive / Curiosity (4 tools) ──────────────────────────────────

@mcp.tool
def nexo_drive_signals(status: str = "", area: str = "", limit: int = 20) -> str:
    """List autonomous drive/curiosity signals.

    Drive signals are observations NEXO accumulates during normal work.
    When tension crosses threshold, NEXO investigates silently.

    Args:
        status: Filter by status (latent, rising, ready, acted, dismissed). Default: active only.
        area: Filter by operational area (shopify, google-ads, wazion, nexo, etc.).
        limit: Max signals to return (default 20).
    """
    return handle_drive_signals(status, area, limit)


@mcp.tool
def nexo_drive_reinforce(signal_id: int, observation: str) -> str:
    """Reinforce a drive signal with a new observation.

    Increases tension and may promote the signal status (latent → rising → ready).

    Args:
        signal_id: Signal ID to reinforce.
        observation: New observation that supports this signal.
    """
    return handle_drive_reinforce(signal_id, observation)


@mcp.tool
def nexo_drive_act(signal_id: int, outcome: str) -> str:
    """Mark a drive signal as investigated with an outcome.

    Call this after NEXO has autonomously investigated a READY signal.

    Args:
        signal_id: Signal ID that was investigated.
        outcome: What was found during investigation.
    """
    return handle_drive_act(signal_id, outcome)


@mcp.tool
def nexo_drive_dismiss(signal_id: int, reason: str) -> str:
    """Dismiss a drive signal (archived, not deleted).

    Call this when a signal is not worth investigating.

    Args:
        signal_id: Signal ID to dismiss.
        reason: Why this signal was dismissed.
    """
    return handle_drive_dismiss(signal_id, reason)


@mcp.tool
def nexo_session_log_create(
    caller: str,
    backend: str,
    session_type: str = "interactive_desktop",
    model: str = "",
    reasoning_effort: str = "",
    resonance_tier: str = "",
    cwd: str = "",
    pid: str = "",
    context_excerpt: str = "",
) -> str:
    """Open an automation_runs row for an interactive Claude/Codex session.

    Designed for clients that spawn Claude/Codex directly (notably NEXO
    Desktop, which runs a TypeScript process that shells out to the CLI
    without going through run_automation_prompt). Call this BEFORE
    spawning the child, store the returned session_id, then call
    nexo_session_log_close when the session ends.

    Args:
        caller: Registered caller id (see src/resonance_map.py). For
                Desktop's "new conversation" button, use
                "desktop_new_session".
        backend: "claude_code" or "codex".
        session_type: "interactive_chat" | "interactive_desktop" — how
                     the session is shaped. Default "interactive_desktop".
        model: Concrete model the client resolved, e.g. "claude-opus-4-7[1m]".
        reasoning_effort: Concrete effort string, e.g. "xhigh".
        resonance_tier: Tier label ("maximo"/"alto"/"medio"/"bajo"). If
                        left empty the Brain resolves it from caller.
        cwd: Working directory the session is anchored to.
        pid: Child process PID if available.
        context_excerpt: Optional first-prompt preview (used to size
                         prompt_chars in telemetry).
    """
    import json as _json
    result = handle_session_log_create(
        caller=caller,
        backend=backend,
        session_type=session_type,
        model=model,
        reasoning_effort=reasoning_effort,
        resonance_tier=resonance_tier,
        cwd=cwd,
        pid=pid,
        context_excerpt=context_excerpt,
    )
    return _json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_session_log_close(
    session_id: int,
    returncode: int = 0,
    duration_ms: int = 0,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    total_cost_usd: str = "",
    telemetry_source: str = "",
    cost_source: str = "",
    error: str = "",
) -> str:
    """Close an automation_runs row opened by nexo_session_log_create.

    Args:
        session_id: id returned by the create call.
        returncode: child exit code (0 = ok).
        duration_ms: wall-clock duration in milliseconds.
        input_tokens / cached_input_tokens / output_tokens: client-side
                     usage counters.
        total_cost_usd: cost in USD as a string (parsed to float).
        telemetry_source: short label identifying where the counts came
                          from ("desktop_stream", "codex_json", ...).
        cost_source: short label for cost provenance.
        error: short error message if the session failed.
    """
    import json as _json
    try:
        cost = float(total_cost_usd) if total_cost_usd else None
    except ValueError:
        cost = None
    result = handle_session_log_close(
        session_id=session_id,
        returncode=returncode,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_cost_usd=cost,
        telemetry_source=telemetry_source,
        cost_source=cost_source,
        error=error,
    )
    return _json.dumps(result, ensure_ascii=False)


@mcp.tool
def nexo_api_call(
    method: str,
    path: str,
    body_json: str = "",
    idempotency_key: str = "",
    headers_json: str = "",
    base_url: str = "",
) -> str:
    """Make an authenticated HTTP request to the NEXO Desktop backend (nexo-desktop.com).

    The session bearer is auto-loaded from the OS keychain — the agent never
    sees or handles tokens. Use this for any /api/* endpoint the user has
    permission for: provider-proxy/*, credits/*, cards/*, auth/app-tokens, etc.

    Args:
        method: HTTP method (GET / POST / PUT / DELETE / PATCH).
        path: path starting with '/' (e.g. '/api/provider-proxy/call').
        body_json: JSON string of the request body. Empty for GET.
        idempotency_key: UUID v4 to dedupe POST/PUT retries (avoids double-charge).
        headers_json: optional extra headers as a JSON object. Authorization is ignored.
        base_url: override default base (default: https://nexo-desktop.com).

    Returns formatted text with HTTP status + parsed JSON body. Bearer is never echoed.
    """
    return handle_api_call(method, path, body_json, idempotency_key, headers_json, base_url)


@mcp.tool
def nexo_support_ticket_list(status: str = "", limit: int = 20) -> str:
    """List real support tickets from the NEXO backend for the signed-in Desktop user.

    Use this when the user asks about support tickets or bug reports. Do not
    substitute a private followup for an actual product support ticket.
    """
    return handle_support_ticket_list(status, limit)


@mcp.tool
def nexo_support_ticket_read(ticket_id: str) -> str:
    """Read one real support ticket from the NEXO backend by id."""
    return handle_support_ticket_read(ticket_id)


@mcp.tool
def nexo_support_ticket_create(subject: str, message: str, priority: str = "normal") -> str:
    """Create a real NEXO support ticket for a product bug/setup issue."""
    return handle_support_ticket_create(subject, message, priority)


@mcp.tool
def nexo_create_app_token(
    name: str,
    abilities: str = "",
    allowed_platforms: str = "",
    expires_at: str = "",
) -> str:
    """Create a persistent AppToken for the current user via POST /api/auth/app-tokens.

    Use this when a card needs to mint a token that will live inside a snippet
    the user pastes on their own website (chatbot widget, embed, public API
    autoresponder). The plain-text token is returned ONCE — embed it in the
    generated snippet and never store it elsewhere.

    Args:
        name: human label for the token (e.g. 'chatbot-mitienda-com').
        abilities: comma-separated abilities. Allowed:
                   provider-proxy:call, provider-proxy:estimate, credits:read.
                   Defaults to 'provider-proxy:call' if empty.
        allowed_platforms: comma-separated platform keys (openai, anthropic, gemini, ...).
                           Empty = all platforms the user has access to.
        expires_at: ISO 8601 future date, empty for non-expiring token.

    Returns the created token summary + plain_text_token (one-time disclosure).
    """
    return handle_create_app_token(name, abilities, allowed_platforms, expires_at)


if __name__ == "__main__":
    if should_use_mcp_adapter():
        run_mcp_proxy_adapter(
            name="nexo",
            instructions=render_core_prompt(
                "server-mcp-instructions",
                assistant_name=_get_ctx().assistant_name,
            ),
            run_kwargs=_run_kwargs_from_env(),
        )
    else:
        _server_init()
        run_kwargs = _run_kwargs_from_env()
        if is_runtime_service_process():
            host = str(run_kwargs.get("host") or os.environ.get("NEXO_MCP_HOST", "127.0.0.1"))
            port = int(run_kwargs.get("port") or os.environ.get("NEXO_MCP_PORT", "0") or 0)
            path = str(run_kwargs.get("path") or os.environ.get("NEXO_MCP_PATH", "/mcp"))
            write_service_state(
                {
                    "pid": os.getpid(),
                    "port": port,
                    "host": host,
                    "path": path,
                    "url": f"http://{host}:{port}{path}",
                    "server_path": str(os.path.abspath(__file__)),
                    "started_at": time.time(),
                    "mode": "runtime-service",
                }
            )
            # Phase 2.1/2.2 — retire this resident gracefully if the on-disk
            # runtime is updated under it AND no clients remain connected.
            # The current-generation resident never self-terminates: a warm
            # Brain is what keeps conversation starts fast.
            from runtime_service import start_resident_obsolescence_watch

            start_resident_obsolescence_watch(
                port=port,
                on_exit=lambda: (close_local_context_db(), close_db()),
            )
        mcp.run(**run_kwargs)
