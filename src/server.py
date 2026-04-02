"""NEXO MCP Server — Phase 4: Hot-Reload Plugin System."""

import os
import signal
import sys

from fastmcp import FastMCP
from db import init_db, rebuild_fts_index, get_db, close_db, fts_add_dir, fts_remove_dir, fts_list_dirs
from tools_sessions import handle_startup, handle_heartbeat, handle_status, handle_context_packet, handle_smart_startup_query
from tools_coordination import (
    handle_track, handle_untrack, handle_files,
    handle_send, handle_ask, handle_answer, handle_check_answer,
)
from tools_reminders import handle_reminders
from tools_menu import handle_menu
from tools_reminders_crud import (
    handle_reminder_create, handle_reminder_update,
    handle_reminder_complete, handle_reminder_delete,
    handle_followup_create, handle_followup_update,
    handle_followup_complete, handle_followup_delete,
)
from tools_learnings import (
    handle_learning_add, handle_learning_search,
    handle_learning_update, handle_learning_delete, handle_learning_list,
)
from tools_credentials import (
    handle_credential_get, handle_credential_create,
    handle_credential_update, handle_credential_delete, handle_credential_list,
)
from tools_task_history import (
    handle_task_log, handle_task_list, handle_task_frequency,
)
from plugin_loader import load_all_plugins, load_plugin, remove_plugin, list_plugins


# ── Graceful shutdown: close DB on any termination signal ──────────
def _shutdown_handler(signum, frame):
    close_db()
    sys.exit(0)


def _server_init():
    """Run all side effects: signals, PID, DB, auto-update, plugins.

    Called only when the server is actually started (not on import).
    """
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # ── Write PID file for stale process detection ─────────────────
    _data_dir = os.path.join(os.environ.get("NEXO_HOME", os.path.join(os.path.expanduser("~"), ".nexo")), "data")
    os.makedirs(_data_dir, exist_ok=True)
    _pid_file = os.path.join(_data_dir, "nexo.pid")
    with open(_pid_file, "w") as f:
        f.write(str(os.getpid()))

    init_db()

    # ── Auto-update check (non-blocking, max 5s) ──────────────────
    try:
        from auto_update import auto_update_check
        import threading

        def _bg_update():
            try:
                result = auto_update_check()
                if result.get("git_update"):
                    print(f"[NEXO] {result['git_update']}", file=sys.stderr)
                if result.get("npm_notice"):
                    print(f"[NEXO] {result['npm_notice']}", file=sys.stderr)
                if result.get("claude_md_update"):
                    print(f"[NEXO] {result['claude_md_update']}", file=sys.stderr)
                for m in result.get("migrations", []):
                    if m["status"] == "failed":
                        print(f"[NEXO] Migration {m['file']} FAILED: {m['message']}", file=sys.stderr)
            except Exception as e:
                print(f"[NEXO auto-update] error: {e}", file=sys.stderr)

        _update_thread = threading.Thread(target=_bg_update, daemon=True)
        _update_thread.start()
        _update_thread.join(timeout=5)  # Wait at most 5 seconds
    except Exception:
        pass  # Never break startup

    # ── Load plugins ───────────────────────────────────────────────
    load_all_plugins(mcp)


mcp = FastMCP(
    name="nexo",
    instructions=(
        "NEXO — cognitive co-operator. Save important info from tool results before they clear.\n\n"
        "## Rules\n"
        "- **Heartbeat:** `nexo_heartbeat(sid=SID, task='...', context_hint='...')` every user msg. "
        "React: DIARY REMINDER→write diary, VIBE:NEGATIVE→ultra-concise, AUTO-PRIME→read learnings\n"
        "- **Guard:** `nexo_guard_check(files='...', area='...')` BEFORE editing code. "
        "Blocking rules→resolve first. `nexo_track(sid=SID, paths=[...])` before shared files\n"
        "- **Followups:** NEXO tasks, execute silently. 'done'/'all set'→`nexo_followup_complete` NOW. "
        "Reminders=user's, alert when due\n"
        "- **Observe:** correction→learning. 'tomorrow'→followup. person→entity. open topic→followup 3d\n"
        "- **Trust events:** When user expresses satisfaction/thanks (any language)→`nexo_cognitive_trust(event='explicit_thanks')`. "
        "When user corrects you→`nexo_cognitive_trust(event='correction')`. "
        "When user delegates without micromanaging→`nexo_cognitive_trust(event='delegation')`. "
        "When you catch something the user missed→`nexo_cognitive_trust(event='proactive_action')`. "
        "Detect intent, not keywords — works in ALL languages.\n"
        "- **Delegate:** prefer direct. If needed: `nexo_context_packet(area)` + guard + 'if unsure STOP'\n"
        "- **Memory:** `nexo_recall` searches all. Capture: errors→`nexo_learning_add`, prefs, entities, decisions\n"
        "- **Change log:** `nexo_change_log(...)` after production edits. NOT for config dir\n"
        "- **Diary:** When user signals end of session (any language, any style — 'bye', 'done', 'cierro', etc.), "
        "write `nexo_session_diary_write(...)` with self_critique BEFORE responding. "
        "Detect intent, not keywords. If session closes without diary, auto_close handles it.\n"
        "- **Cortex:** `nexo_cortex_check` before budget/campaign/architecture changes\n"
        "- **Skills:** before multi-step tasks, `nexo_skill_match(task)` to find reusable procedures. "
        "If match found, read it and follow the steps. After completion, `nexo_skill_result(id, success, context)` to record outcome.\n"
        "- **Dissonance:** user contradicts memory→`nexo_cognitive_dissonance`. Frustrated→force=True\n"
        "- **Trust:** <40=paranoid verify twice, >80=fluid. Check: `nexo_cognitive_trust`"
    ),
)


# ── Session management (3 tools) ──────────────────────────────────

@mcp.tool
def nexo_startup(task: str = "Startup", claude_session_id: str = "") -> str:
    """Register new session, clean stale ones, return active sessions + alerts.

    Call this ONCE at the start of every conversation.
    Returns the session ID (SID) — store it for use in all other nexo_ tools.

    Args:
        task: Initial task description.
        claude_session_id: The Claude Code session UUID (from session-briefing or hook).
                          Pass this to enable automatic inter-terminal inbox detection.
    """
    return handle_startup(task, claude_session_id=claude_session_id)


@mcp.tool
def nexo_heartbeat(sid: str, task: str, context_hint: str = '') -> str:
    """Update session task, check inbox and pending questions. Auto-detects trust events.

    Call this at the START of every user interaction (before doing work).
    Args:
        sid: Your session ID from nexo_startup.
        task: Brief description of current work (5-10 words).
        context_hint: Last 2-3 sentences from the user or current topic. Used for sentiment detection, trust auto-scoring, and mid-session RAG. ALWAYS provide this for best results.
    """
    return handle_heartbeat(sid, task, context_hint)


@mcp.tool
def nexo_stop(sid: str) -> str:
    """Cleanly close a session. Removes it from active sessions immediately.

    Call this when ending a conversation to avoid ghost sessions.
    Args:
        sid: Session ID to close."""
    from tools_sessions import handle_stop
    return handle_stop(sid)

@mcp.tool
def nexo_status(keyword: str = "") -> str:
    """List active sessions. Filter by keyword if provided."""
    return handle_status(keyword if keyword else None)


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
def nexo_smart_startup() -> str:
    """Pre-load relevant cognitive memories based on pending followups, due reminders, and last session topics.

    Call during startup (after nexo_startup) to ensure the session starts with the right context loaded.
    Returns up to 10 memories matching the current operational state.
    """
    return handle_smart_startup_query()


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
        filter: 'due' (vencidos/hoy), 'all' (todos activos), 'followups' (solo NEXO followups)
    """
    return handle_reminders(filter)


@mcp.tool
def nexo_menu() -> str:
    """Generate the NEXO operations center menu with alerts and active sessions.

    Shows: date, due alerts, all menu items by category, active sessions.
    Uses box-drawing characters for formatting.
    """
    return handle_menu()


# ── Reminders CRUD (4 tools) ──────────────────────────────────────

@mcp.tool
def nexo_reminder_create(id: str, description: str, date: str = "", category: str = "general") -> str:
    """Create a new reminder for the user.

    Args:
        id: Unique ID starting with 'R' (e.g., R90).
        description: What needs to be done.
        date: Target date YYYY-MM-DD (optional).
        category: One of: decisions, tasks, waiting, ideas, general.
    """
    return handle_reminder_create(id, description, date, category)


@mcp.tool
def nexo_reminder_update(id: str, description: str = "", date: str = "", status: str = "", category: str = "") -> str:
    """Update fields of an existing reminder. Only non-empty fields are changed.

    Args:
        id: Reminder ID (e.g., R87).
        description: New description (optional).
        date: New date YYYY-MM-DD (optional).
        status: New status (optional).
        category: New category (optional).
    """
    return handle_reminder_update(id, description, date, status, category)


@mcp.tool
def nexo_reminder_complete(id: str) -> str:
    """Mark a reminder as completed with today's date.

    Args:
        id: Reminder ID (e.g., R87).
    """
    return handle_reminder_complete(id)


@mcp.tool
def nexo_reminder_delete(id: str) -> str:
    """Delete a reminder permanently.

    Args:
        id: Reminder ID (e.g., R87).
    """
    return handle_reminder_delete(id)


# ── Followups CRUD (4 tools) ──────────────────────────────────────

@mcp.tool
def nexo_followup_create(id: str, description: str, date: str = "", verification: str = "", reasoning: str = "", recurrence: str = "", priority: str = "medium") -> str:
    """Create a new NEXO followup (autonomous task).

    Args:
        id: Unique ID starting with 'NF' (e.g., NF-MCP2).
        description: What to verify/do.
        date: Target date YYYY-MM-DD (optional).
        verification: How to verify completion (optional).
        reasoning: WHY this followup exists — what decision/context led to it (optional).
        recurrence: Auto-regenerate pattern (optional). Formats: 'weekly:monday', 'monthly:1', 'monthly:15', 'quarterly'.
                    When completed, a new followup is auto-created with the next date. The completed one is archived with date suffix.
        priority: critical, high, medium, low (default: medium).
    """
    result = handle_followup_create(id, description, date, verification, reasoning, recurrence)
    if priority in ('critical', 'high', 'low') and 'created' in result:
        from db import get_db
        get_db().execute("UPDATE followups SET priority = ? WHERE id = ?", (priority, id))
        get_db().commit()
    return result


@mcp.tool
def nexo_followup_update(id: str, description: str = "", date: str = "", verification: str = "", status: str = "", priority: str = "") -> str:
    """Update fields of an existing followup. Only non-empty fields are changed.

    Args:
        id: Followup ID (e.g., NF45).
        description: New description (optional).
        date: New date YYYY-MM-DD (optional).
        verification: New verification text (optional).
        status: New status (optional).
        priority: critical, high, medium, low (optional).
    """
    result = handle_followup_update(id, description, date, verification, status)
    if priority in ('critical', 'high', 'medium', 'low'):
        from db import get_db
        get_db().execute("UPDATE followups SET priority = ? WHERE id = ?", (priority, id))
        get_db().commit()
    return result


@mcp.tool
def nexo_followup_complete(id: str, result: str = "") -> str:
    """Mark a followup as completed. Appends result to verification field.

    Args:
        id: Followup ID (e.g., NF45).
        result: What was found/done (optional).
    """
    return handle_followup_complete(id, result)


@mcp.tool
def nexo_followup_delete(id: str) -> str:
    """Delete a followup permanently.

    Args:
        id: Followup ID (e.g., NF45).
    """
    return handle_followup_delete(id)


# ── Learnings CRUD (5 tools) ──────────────────────────────────────

@mcp.tool
def nexo_learning_add(category: str, title: str, content: str, reasoning: str = "", priority: str = "medium") -> str:
    """Add a new learning (resolved error, pattern, gotcha).

    Args:
        category: Free-form category name (e.g., 'backend', 'frontend', 'devops', 'infrastructure', 'security'). Use consistent names across learnings.
        title: Short title for the learning.
        content: Full description with context and solution.
        reasoning: WHY this matters — what led to discovering this (optional).
        priority: critical, high, medium, low (default: medium). Critical/high never decay below floor.
    """
    return handle_learning_add(category, title, content, reasoning, priority=priority)


@mcp.tool
def nexo_learning_search(query: str, category: str = "") -> str:
    """Search learnings by keyword. Searches title and content.

    Args:
        query: Search term.
        category: Filter by category (optional).
    """
    return handle_learning_search(query, category)


@mcp.tool
def nexo_learning_update(id: int, title: str = "", content: str = "", category: str = "", priority: str = "") -> str:
    """Update a learning entry. Only non-empty fields are changed.

    Args:
        id: Learning ID number.
        title: New title (optional).
        content: New content (optional).
        category: New category (optional).
        priority: critical, high, medium, low (optional).
    """
    return handle_learning_update(id, title, content, category, priority=priority)


@mcp.tool
def nexo_learning_delete(id: int) -> str:
    """Delete a learning entry.

    Args:
        id: Learning ID number.
    """
    return handle_learning_delete(id)


@mcp.tool
def nexo_learning_list(category: str = "") -> str:
    """List all learnings, grouped by category.

    Args:
        category: Filter by category (optional). If empty, shows all grouped.
    """
    return handle_learning_list(category)


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
    """Load or reload a plugin. Searches repo plugins/ first, then NEXO_HOME/plugins/.

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


if __name__ == "__main__":
    _server_init()
    mcp.run(transport="stdio")
