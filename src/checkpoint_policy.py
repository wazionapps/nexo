"""Durable checkpoint policy for long-running multi-step work.

This module turns task/workflow milestones into a small persistent state file
and periodically flushes that state into ``session_checkpoints`` so compaction
and phase switches recover a richer next-step snapshot than the heartbeat-only
goal stub.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
STATE_PATH = NEXO_HOME / "runtime" / "data" / "durable_checkpoint_state.json"
DEFAULT_MILESTONE_INTERVAL = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_all() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_all(payload: dict[str, dict[str, Any]]) -> None:
    _ensure_dir()
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _blank_session_state(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "milestone_count": 0,
        "updated_at": _now_iso(),
        "last_reason": "",
        "task": "",
        "task_status": "active",
        "active_files": [],
        "current_goal": "",
        "decisions_summary": "",
        "blockers": "",
        "reasoning_thread": "",
        "next_step": "",
        "last_flushed_at": "",
        "last_flush_reason": "",
    }


def _coalesce_text(new_value: str, old_value: str = "") -> str:
    clean = str(new_value or "").strip()
    return clean or str(old_value or "").strip()


def _normalize_active_files(active_files: Any) -> list[str]:
    if active_files is None:
        return []
    if isinstance(active_files, str):
        stripped = active_files.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = [part.strip() for part in stripped.split(",") if part.strip()]
        else:
            parsed = [part.strip() for part in stripped.split(",") if part.strip()]
    elif isinstance(active_files, (list, tuple, set)):
        parsed = list(active_files)
    else:
        parsed = [str(active_files).strip()]

    seen: list[str] = []
    for item in parsed:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.append(clean)
    return seen


def _session_state(all_state: dict[str, dict[str, Any]], session_id: str) -> dict[str, Any]:
    existing = all_state.get(session_id)
    if not isinstance(existing, dict):
        return _blank_session_state(session_id)
    base = _blank_session_state(session_id)
    base.update(existing)
    base["session_id"] = session_id
    base["active_files"] = _normalize_active_files(base.get("active_files"))
    try:
        base["milestone_count"] = max(0, int(base.get("milestone_count", 0)))
    except (TypeError, ValueError):
        base["milestone_count"] = 0
    return base


def _extract_active_files_from_payload(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    for key in ("active_files", "files", "tracked_files"):
        files = _normalize_active_files(payload.get(key))
        if files:
            return files
    return []


def _flush_state(
    all_state: dict[str, dict[str, Any]],
    session_id: str,
    state: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    from db import save_checkpoint

    decisions_summary = _coalesce_text(state.get("decisions_summary", ""))
    if reason:
        reason_note = f"checkpoint_reason={reason}"
        decisions_summary = f"{decisions_summary} | {reason_note}".strip(" |")

    active_files = _normalize_active_files(state.get("active_files"))
    save_result = save_checkpoint(
        sid=session_id,
        task=_coalesce_text(state.get("task", ""), state.get("current_goal", "")),
        task_status=_coalesce_text(state.get("task_status", ""), "active"),
        active_files=json.dumps(active_files, ensure_ascii=False),
        current_goal=_coalesce_text(state.get("current_goal", ""), state.get("task", "")),
        decisions_summary=decisions_summary,
        errors_found=_coalesce_text(state.get("blockers", "")),
        reasoning_thread=_coalesce_text(state.get("reasoning_thread", "")),
        next_step=_coalesce_text(state.get("next_step", "")),
    )

    state["active_files"] = active_files
    state["milestone_count"] = 0
    state["last_flushed_at"] = _now_iso()
    state["last_flush_reason"] = reason
    state["updated_at"] = _now_iso()
    all_state[session_id] = state
    _save_all(all_state)
    return {
        "ok": True,
        "checkpoint_written": True,
        "session_id": session_id,
        "milestone_count": 0,
        "last_flush_reason": reason,
        "compaction_count": save_result.get("compaction_count", 0),
    }


def record_milestone(
    session_id: str,
    *,
    reason: str,
    task: str = "",
    task_status: str = "active",
    active_files: Any = None,
    current_goal: str = "",
    decisions_summary: str = "",
    blockers: str = "",
    reasoning_thread: str = "",
    next_step: str = "",
    interval: int = DEFAULT_MILESTONE_INTERVAL,
    force_flush: bool = False,
) -> dict[str, Any]:
    clean_sid = str(session_id or "").strip()
    if not clean_sid:
        return {"ok": False, "error": "session_id is required"}

    all_state = _load_all()
    state = _session_state(all_state, clean_sid)

    state["last_reason"] = str(reason or "").strip()
    state["updated_at"] = _now_iso()
    state["task"] = _coalesce_text(task, state.get("task", ""))
    state["task_status"] = _coalesce_text(task_status, state.get("task_status", "active"))
    state["current_goal"] = _coalesce_text(current_goal, state.get("current_goal", ""))
    state["decisions_summary"] = _coalesce_text(decisions_summary, state.get("decisions_summary", ""))
    state["blockers"] = _coalesce_text(blockers, state.get("blockers", ""))
    state["reasoning_thread"] = _coalesce_text(reasoning_thread, state.get("reasoning_thread", ""))
    state["next_step"] = _coalesce_text(next_step, state.get("next_step", ""))

    files = _normalize_active_files(active_files)
    if files:
        state["active_files"] = files

    state["milestone_count"] = max(0, int(state.get("milestone_count", 0))) + 1
    all_state[clean_sid] = state

    flush_every = max(1, int(interval or DEFAULT_MILESTONE_INTERVAL))
    if force_flush or state["milestone_count"] >= flush_every:
        return _flush_state(all_state, clean_sid, state, reason=str(reason or "").strip())

    _save_all(all_state)
    return {
        "ok": True,
        "checkpoint_written": False,
        "session_id": clean_sid,
        "milestone_count": state["milestone_count"],
        "flush_interval": flush_every,
        "pending_reason": state["last_reason"],
    }


def force_runtime_checkpoint(session_id: str, *, reason: str = "pre-compact") -> dict[str, Any]:
    clean_sid = str(session_id or "").strip()
    if not clean_sid:
        return {"ok": False, "error": "session_id is required"}

    from db import get_db, read_checkpoint

    all_state = _load_all()
    state = _session_state(all_state, clean_sid)
    conn = get_db()

    session_row = conn.execute(
        "SELECT task FROM sessions WHERE sid = ? LIMIT 1",
        (clean_sid,),
    ).fetchone()
    existing_checkpoint = read_checkpoint(clean_sid) or {}
    workflow_row = conn.execute(
        """SELECT goal, status, current_step_key, next_action, shared_state
           FROM workflow_runs
           WHERE session_id = ?
           ORDER BY updated_at DESC LIMIT 1""",
        (clean_sid,),
    ).fetchone()

    workflow_state: dict[str, Any] = {}
    if workflow_row and workflow_row["shared_state"]:
        try:
            parsed = json.loads(workflow_row["shared_state"])
            if isinstance(parsed, dict):
                workflow_state = parsed
        except json.JSONDecodeError:
            workflow_state = {}

    workflow_blocker = ""
    if workflow_row and workflow_row["status"] in {"blocked", "waiting_approval"}:
        workflow_blocker = (
            f"Workflow {workflow_row['status']} at "
            f"{workflow_row['current_step_key'] or 'current-step'}"
        )

    merged_files = (
        _normalize_active_files(state.get("active_files"))
        or _extract_active_files_from_payload(workflow_state)
        or _normalize_active_files(existing_checkpoint.get("active_files"))
    )

    state["task"] = _coalesce_text(
        state.get("task", ""),
        (session_row["task"] if session_row else "") or existing_checkpoint.get("task", ""),
    )
    state["current_goal"] = _coalesce_text(
        state.get("current_goal", ""),
        (workflow_row["goal"] if workflow_row else "") or existing_checkpoint.get("current_goal", "") or state.get("task", ""),
    )
    state["decisions_summary"] = _coalesce_text(
        state.get("decisions_summary", ""),
        existing_checkpoint.get("decisions_summary", "") or f"Forced durable checkpoint before {reason}.",
    )
    current_blockers = _coalesce_text(
        state.get("blockers", ""),
        existing_checkpoint.get("errors_found", ""),
    )
    if workflow_blocker:
        if workflow_blocker not in current_blockers:
            current_blockers = f"{workflow_blocker} | {current_blockers}".strip(" |")
    state["blockers"] = current_blockers
    state["reasoning_thread"] = _coalesce_text(
        state.get("reasoning_thread", ""),
        existing_checkpoint.get("reasoning_thread", "") or f"Auto-flushed by checkpoint_policy ({reason}).",
    )
    state["next_step"] = _coalesce_text(
        state.get("next_step", ""),
        (workflow_row["next_action"] if workflow_row else "") or existing_checkpoint.get("next_step", ""),
    )
    state["task_status"] = _coalesce_text(
        state.get("task_status", ""),
        (workflow_row["status"] if workflow_row else "") or existing_checkpoint.get("task_status", "") or "active",
    )
    state["active_files"] = merged_files
    state["updated_at"] = _now_iso()
    state["last_reason"] = reason
    all_state[clean_sid] = state
    return _flush_state(all_state, clean_sid, state, reason=reason)
