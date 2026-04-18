from __future__ import annotations

"""Post-tool guardrails for conditioned file learnings."""

import json
import os
import sys
from pathlib import Path

from db import create_protocol_debt, get_db
from plugins.guard import _load_conditioned_learnings, _normalize_path_token
from protocol_settings import get_protocol_strictness

READ_LIKE_TOOLS = {"Read"}
WRITE_LIKE_TOOLS = {"Edit", "MultiEdit", "Write"}
DELETE_LIKE_TOOLS = {"Delete"}
NON_TRIVIAL_PROTOCOL_TOOLS = {"Read", "Bash", "Grep", "Glob", "Edit", "MultiEdit", "Write", "Delete"}
PROTOCOL_SKIP_TOOLS = {
    "nexo_startup",
    "nexo_smart_startup",
    "nexo_stop",
    "nexo_heartbeat",
    "nexo_task_open",
    "nexo_task_close",
    "nexo_workflow_open",
    "nexo_workflow_update",
    "nexo_guard_check",
    "nexo_guard_file_check",
    "nexo_rules_check",
}
ACTION_TASK_TYPES = {"edit", "execute", "delegate"}
NEXO_CODE_ROOT = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent))).expanduser().resolve()
LIVE_REPO_ROOT = NEXO_CODE_ROOT.parent if NEXO_CODE_ROOT.name == "src" else NEXO_CODE_ROOT
PUBLIC_REPO_DIRS = {
    ".claude-plugin",
    ".github",
    "bin",
    "clawhub-skill",
    "community",
    "docs",
    "hooks",
    "openclaw-plugin",
    "src",
    "templates",
    "tests",
}
PUBLIC_REPO_FILES = {
    ".mcp.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "docker-compose.yml",
    "package-lock.json",
    "package.json",
}


def _operation_kind(tool_name: str) -> str:
    if tool_name in READ_LIKE_TOOLS:
        return "read"
    if tool_name in WRITE_LIKE_TOOLS:
        return "write"
    if tool_name in DELETE_LIKE_TOOLS:
        return "delete"
    return "other"


def _short_tool_name(tool_name: str) -> str:
    clean = str(tool_name or "").strip()
    return clean.rsplit("__", 1)[-1] if "__" in clean else clean


def _normalize_file_path(path: str) -> str:
    return _normalize_path_token(str(Path(path)))


def _resolve_runtime_path(path: str) -> Path:
    candidate = Path(str(path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _automation_live_repo_guard_enabled() -> bool:
    return (
        os.environ.get("NEXO_AUTOMATION", "").strip() == "1"
        and os.environ.get("NEXO_PUBLIC_CONTRIBUTION", "").strip() != "1"
    )


def _has_git_marker(root: Path) -> bool:
    return (root / ".git").exists()


def _is_public_repo_surface(candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(LIVE_REPO_ROOT)
    except ValueError:
        return False

    parts = relative.parts
    if not parts:
        return False
    if parts[0] in PUBLIC_REPO_DIRS:
        return True
    return len(parts) == 1 and parts[0] in PUBLIC_REPO_FILES


def _is_live_repo_path(path: str) -> bool:
    if not str(path or "").strip():
        return False
    try:
        if not _has_git_marker(LIVE_REPO_ROOT):
            return False
        return _is_public_repo_surface(_resolve_runtime_path(path))
    except Exception:
        return False


def _extract_touched_files(tool_input) -> list[str]:
    files: list[str] = []
    if not isinstance(tool_input, dict):
        return files

    def add(candidate) -> None:
        if isinstance(candidate, str) and candidate.strip():
            files.append(candidate.strip())

    add(tool_input.get("file_path"))
    add(tool_input.get("path"))

    for key in ("paths", "file_paths", "files"):
        value = tool_input.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    add(item.get("file_path"))
                    add(item.get("path"))

    unique: list[str] = []
    seen = set()
    for item in files:
        normalized = _normalize_file_path(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(item)
    return unique


def _resolve_nexo_sid(conn, external_session_id: str) -> str:
    """Resolve a Claude Code UUID to the NEXO session SID it belongs to.

    Primary correlation: exact match on external_session_id / claude_session_id.

    v6.0.7 hotfix — secondary correlation: when no exact match is found AND
    there is exactly ONE NEXO session with a fresh heartbeat (last update in
    the past 5 minutes), fall back to that session. This closes the "unknown
    target" edge case where Claude Code rotated its internal session_id
    mid-session (e.g. after a compaction) without rewriting the coordination
    file. The single-session gate prevents mis-attribution when two Claude
    instances run concurrently.
    """
    clean_external = external_session_id.strip()
    if clean_external:
        row = conn.execute(
            """SELECT sid
               FROM sessions
               WHERE external_session_id = ? OR claude_session_id = ?
               ORDER BY last_update_epoch DESC
               LIMIT 1""",
            (clean_external, clean_external),
        ).fetchone()
        if row:
            return str(row["sid"])

    # Fallback: exactly one session heartbeated in the last 5 minutes.
    # We prefer this narrow window so we never silently attribute work to
    # a stale session. If the caller has zero or multiple active sessions,
    # fail closed (return "") and let the caller raise missing_startup.
    import time as _time
    cutoff_epoch = _time.time() - 300.0
    rows = conn.execute(
        """SELECT sid
           FROM sessions
           WHERE last_update_epoch >= ?
           ORDER BY last_update_epoch DESC""",
        (cutoff_epoch,),
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["sid"])
    return ""


def _find_open_task_for_file(conn, sid: str, filepath: str) -> dict | None:
    target = _normalize_file_path(filepath)
    rows = conn.execute(
        """SELECT task_id, files, guard_has_blocking, task_type, plan, unknowns,
                  verification_step, opened_with_guard, must_change_log, must_verify
           FROM protocol_tasks
           WHERE session_id = ? AND status = 'open'
           ORDER BY opened_at DESC""",
        (sid,),
    ).fetchall()
    for row in rows:
        try:
            files = json.loads(row["files"] or "[]")
        except Exception:
            files = []
        for item in files if isinstance(files, list) else []:
            if _normalize_file_path(str(item)) == target:
                return dict(row)
    return None


def _find_any_open_task(conn, sid: str) -> dict | None:
    row = conn.execute(
        """SELECT task_id, files, guard_has_blocking, task_type, plan, unknowns,
                  verification_step, opened_with_guard, must_change_log, must_verify
           FROM protocol_tasks
           WHERE session_id = ? AND status = 'open'
           ORDER BY opened_at DESC
           LIMIT 1""",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def _find_any_open_workflow(conn, sid: str) -> dict | None:
    row = conn.execute(
        """SELECT run_id, protocol_task_id, current_step_key
           FROM workflow_runs
           WHERE session_id = ? AND status IN ('open', 'running', 'blocked', 'waiting_approval')
           ORDER BY updated_at DESC, run_id DESC
           LIMIT 1""",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def _session_has_guard_check(conn, sid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM guard_checks WHERE session_id = ? LIMIT 1",
        (sid,),
    ).fetchone()
    return bool(row)


def _session_has_guard_for_file(conn, sid: str, filepath: str) -> bool:
    """Check if guard_check was called for a specific file in this session."""
    if not filepath:
        return False
    normalized = _normalize_file_path(filepath)
    basename = os.path.basename(filepath)
    # guard_checks.files is a comma-separated or JSON list of paths/areas
    row = conn.execute(
        """SELECT 1 FROM guard_checks
           WHERE session_id = ?
             AND (files LIKE ? OR files LIKE ? OR files LIKE ?)
           LIMIT 1""",
        (sid, f"%{normalized}%", f"%{basename}%", f"%{filepath}%"),
    ).fetchone()
    return bool(row)


def _find_open_debt(conn, *, session_id: str, task_id: str, debt_type: str, file_token: str) -> dict | None:
    row = conn.execute(
        """SELECT *
           FROM protocol_debt
           WHERE status = 'open'
             AND session_id = ?
             AND task_id = ?
             AND debt_type = ?
             AND INSTR(evidence, ?) > 0
           ORDER BY id DESC
           LIMIT 1""",
        (session_id, task_id, debt_type, file_token),
    ).fetchone()
    return dict(row) if row else None


def _find_task_guard_blocking_debt(conn, task_id: str) -> dict | None:
    row = conn.execute(
        """SELECT *
           FROM protocol_debt
           WHERE status = 'open'
             AND task_id = ?
             AND debt_type = 'unacknowledged_guard_blocking'
           ORDER BY id DESC
           LIMIT 1""",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def _ensure_protocol_debt(
    conn,
    *,
    session_id: str,
    task_id: str,
    debt_type: str,
    severity: str,
    evidence: str,
    file_token: str,
) -> dict:
    existing = _find_open_debt(
        conn,
        session_id=session_id,
        task_id=task_id,
        debt_type=debt_type,
        file_token=file_token,
    )
    if existing:
        return existing
    return create_protocol_debt(
        session_id,
        debt_type,
        severity=severity,
        task_id=task_id,
        evidence=evidence,
    )


def _task_list_field(task: dict | None, key: str) -> list:
    if not task:
        return []
    try:
        parsed = json.loads(task.get(key) or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _task_needs_workflow(task: dict | None) -> bool:
    if not task:
        return False
    if str(task.get("task_type") or "").strip() not in ACTION_TASK_TYPES:
        return False
    if len(_task_list_field(task, "plan")) > 1:
        return True
    if len(_task_list_field(task, "unknowns")) > 0:
        return True
    if len(_task_list_field(task, "files")) > 1:
        return True
    return bool(str(task.get("verification_step") or "").strip())


def _append_protocol_warning(warnings: list[dict], message: str) -> None:
    clean = (message or "").strip()
    if not clean:
        return
    if any((item.get("message") or "").strip() == clean for item in warnings):
        return
    warnings.append({"message": clean})


def _collect_protocol_warnings(conn, *, sid: str, tool_name: str) -> list[dict]:
    short_name = _short_tool_name(tool_name)
    if short_name in PROTOCOL_SKIP_TOOLS or short_name not in NON_TRIVIAL_PROTOCOL_TOOLS:
        return []

    warnings: list[dict] = []
    if not sid:
        _append_protocol_warning(
            warnings,
            "Trabajo no trivial detectado antes de `nexo_startup(...)`. Arranca NEXO, abre `nexo_task_open(...)`, y si esto va a durar varias fases abre también `nexo_workflow_open(...)` antes de seguir.",
        )
        return warnings

    task = _find_any_open_task(conn, sid)
    has_guard = _session_has_guard_check(conn, sid)
    if not task:
        guard_note = (
            " Ejecuta `nexo_guard_check(...)` antes de leer código condicionado o compartido."
            if short_name in {"Read", "Bash", "Grep", "Glob"} and not has_guard
            else ""
        )
        _append_protocol_warning(
            warnings,
            "Trabajo no trivial detectado sin `nexo_task_open(...)`. Ábrelo ahora y, si esto va a cruzar varios pasos o mensajes, añade `nexo_workflow_open(...)`." + guard_note,
        )
        _append_protocol_warning(
            warnings,
            "Recordatorio protocolario: mantén `nexo_heartbeat(...)` al día y no cierres en optimista; si hay cambios reales, registra `nexo_change_log(...)` o cierra con `nexo_task_close(...)` más evidencia.",
        )
        return warnings

    task_id = str(task.get("task_id") or "").strip()
    if str(task.get("task_type") or "").strip() in ACTION_TASK_TYPES and not (task.get("opened_with_guard") or has_guard):
        _append_protocol_warning(
            warnings,
            f"La tarea {task_id} está activa sin guard visible. Ejecuta `nexo_guard_check(...)` antes de tocar código condicionado o compartido.",
        )

    workflow = _find_any_open_workflow(conn, sid)
    if _task_needs_workflow(task) and not workflow:
        _append_protocol_warning(
            warnings,
            f"La tarea {task_id} ya tiene pinta de multi-step y sigue sin `nexo_workflow_open(...)`. Ábrelo para que checkpoints, resume y replay no dependan de memoria implícita.",
        )

    if str(task.get("task_type") or "").strip() in ACTION_TASK_TYPES and short_name in {"Bash", "Edit", "MultiEdit", "Write", "Delete"}:
        change_note = (
            " Si editas de verdad y no vas a usar `nexo_task_close(...)` inmediatamente, captura `nexo_change_log(...)`."
            if task.get("must_change_log")
            else ""
        )
        _append_protocol_warning(
            warnings,
            f"Recordatorio protocolario para {task_id}: mantén `nexo_heartbeat(...)` al día y ciérrala con `nexo_task_close(...)` más evidencia antes de decir que está resuelta.{change_note}",
        )

    return warnings


def _collect_automation_live_repo_blocks(
    conn,
    *,
    sid: str,
    tool_name: str,
    files: list[str],
) -> list[dict]:
    if not _automation_live_repo_guard_enabled():
        return []
    blocks: list[dict] = []
    for filepath in files:
        if not _is_live_repo_path(filepath):
            continue
        debt = _ensure_protocol_debt(
            conn,
            session_id=sid,
            task_id="",
            debt_type="automation_live_repo_write_blocked",
            severity="error",
            evidence=(
                f"{tool_name} attempted on {filepath} from an automation session against the live NEXO repo. "
                "Use an isolated checkout/worktree or the public contribution Draft PR flow instead."
            ),
            file_token=filepath,
        )
        blocks.append(
            {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "automation_live_repo_write_blocked",
                "reason_code": "automation_live_repo",
            }
        )
    return blocks


def _read_claude_session_id_from_coordination() -> str:
    """Fallback claude_session_id when Claude Code's PreToolUse payload omits it.

    SessionStart hook writes the active Claude Code session UUID to
    ``<NEXO_HOME>/coordination/.claude-session-id``. When the PreToolUse
    payload omits ``session_id`` (observed across several Claude Code
    versions), the pre-tool guardrail would lose correlation with the open
    NEXO session and block every write with "unknown target" (learning
    #411). Reading the coordination file restores the correlation without
    relaxing fail-closed semantics: if the file is missing or empty the
    caller still blocks.
    """
    candidates = []
    nexo_home = os.environ.get("NEXO_HOME", "").strip()
    if nexo_home:
        candidates.append(Path(nexo_home).expanduser() / "coordination" / ".claude-session-id")
    candidates.append(Path.home() / ".nexo" / "coordination" / ".claude-session-id")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            value = path.read_text().strip()
        except (FileNotFoundError, OSError):
            continue
        if value:
            return value
    return ""


def process_pre_tool_event(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name", "")).strip()
    op = _operation_kind(tool_name)
    if op not in {"write", "delete"}:
        return {"ok": True, "skipped": True, "reason": "operation not blocked", "strictness": get_protocol_strictness()}

    tool_input = payload.get("tool_input")
    files = _extract_touched_files(tool_input)
    strictness = get_protocol_strictness()
    conn = get_db()
    claude_sid = str(payload.get("session_id", "") or "").strip()
    if not claude_sid:
        claude_sid = _read_claude_session_id_from_coordination()
    sid = _resolve_nexo_sid(conn, claude_sid)
    automation_blocks = _collect_automation_live_repo_blocks(
        conn,
        sid=sid,
        tool_name=tool_name,
        files=files,
    )
    if automation_blocks:
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": automation_blocks,
            "status": "blocked",
        }

    if strictness == "lenient":
        return {"ok": True, "skipped": True, "reason": "lenient mode", "strictness": strictness}

    blocks: list[dict] = []

    if not sid:
        debt = _ensure_protocol_debt(
            conn,
            session_id="",
            task_id="",
            debt_type="strict_protocol_write_without_startup",
            severity="error",
            evidence=f"{tool_name} attempted before nexo_startup/session mapping.",
            file_token="startup",
        )
        blocks.append(
            {
                "file": "",
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "strict_protocol_write_without_startup",
                "reason_code": "missing_startup",
            }
        )
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": blocks,
            "status": "blocked",
        }

    if not files:
        task = _find_any_open_task(conn, sid)
        if not task:
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="strict_protocol_write_without_task",
                severity="error",
                evidence=f"{tool_name} attempted without a detectable file path and without an open protocol task.",
                file_token="unknown-target",
            )
            blocks.append(
                {
                    "file": "",
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_task",
                    "reason_code": "missing_task",
                }
            )
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": blocks,
            "status": "blocked" if blocks else "clean",
        }

    for filepath in files:
        task = _find_open_task_for_file(conn, sid, filepath)
        if not task:
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="strict_protocol_write_without_task",
                severity="error",
                evidence=f"{tool_name} attempted on {filepath} without an open protocol task for that file.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_task",
                    "reason_code": "missing_task",
                }
            )
            continue

        guard_debt = _find_task_guard_blocking_debt(conn, task["task_id"])
        if guard_debt:
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="strict_protocol_write_without_guard_ack",
                severity="error",
                evidence=f"{tool_name} attempted on {filepath} before acknowledging guard debt for task {task['task_id']}.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_guard_ack",
                    "reason_code": "guard_unacknowledged",
                }
            )
            continue

        # Check if guard_check was called for this specific file
        if not _session_has_guard_for_file(conn, sid, filepath):
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="write_without_file_guard_check",
                severity="warn",
                evidence=f"{tool_name} attempted on {filepath} without a prior guard_check covering that file.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "write_without_file_guard_check",
                    "reason_code": "missing_file_guard",
                }
            )

    return {
        "ok": True,
        "session_id": sid,
        "tool_name": tool_name,
        "operation": op,
        "strictness": strictness,
        "blocks": blocks,
        "status": "blocked" if blocks else "clean",
    }


def process_tool_event(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name", "")).strip()
    op = _operation_kind(tool_name)
    tool_input = payload.get("tool_input")
    files = _extract_touched_files(tool_input)
    conn = get_db()
    sid = _resolve_nexo_sid(conn, str(payload.get("session_id", "")))
    warnings = _collect_protocol_warnings(conn, sid=sid, tool_name=tool_name)

    if op == "other" and not warnings:
        return {"ok": True, "skipped": True, "reason": "tool not monitored"}
    if not files and op in {"read", "write", "delete"} and not warnings:
        return {"ok": True, "skipped": True, "reason": "no touched files found"}
    if not sid and not warnings:
        return {"ok": True, "skipped": True, "reason": "session not mapped to nexo"}

    conditioned = _load_conditioned_learnings(conn, files) if sid else {}
    violations: list[dict] = []

    for filepath in files:
        hits = conditioned.get(filepath) or []
        if not hits:
            continue
        learning_ids = [int(row["id"]) for row in hits]
        task = _find_open_task_for_file(conn, sid, filepath)

        if op == "read":
            if not task:
                evidence = (
                    f"{tool_name} read conditioned file {filepath} linked to learning IDs {learning_ids} "
                    "without an open protocol task."
                )
                debt = _ensure_protocol_debt(
                    conn,
                    session_id=sid,
                    task_id="",
                    debt_type="conditioned_file_read_without_protocol",
                    severity="warn",
                    evidence=evidence,
                    file_token=filepath,
                )
                warnings.append(
                    {
                        "file": filepath,
                        "learning_ids": learning_ids,
                        "debt_id": debt.get("id"),
                        "debt_type": "conditioned_file_read_without_protocol",
                        "message": "Read conditioned file outside protocol task; review the file rules before any write/delete step.",
                    }
                )
            continue

        if not task:
            evidence = (
                f"{tool_name} touched conditioned file {filepath} linked to learning IDs {learning_ids} "
                f"without an open protocol task."
            )
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="conditioned_file_touch_without_protocol",
                severity="error",
                evidence=evidence,
                file_token=filepath,
            )
            violations.append(
                {
                    "file": filepath,
                    "learning_ids": learning_ids,
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "conditioned_file_touch_without_protocol",
                }
            )
            continue

        guard_debt = _find_task_guard_blocking_debt(conn, task["task_id"])
        if guard_debt:
            evidence = (
                f"{tool_name} touched conditioned file {filepath} linked to learning IDs {learning_ids} "
                f"before acknowledging blocking guard debt for task {task['task_id']}."
            )
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="conditioned_file_touch_without_guard_ack",
                severity="error",
                evidence=evidence,
                file_token=filepath,
            )
            violations.append(
                {
                    "file": filepath,
                    "learning_ids": learning_ids,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "conditioned_file_touch_without_guard_ack",
                }
            )

    return {
        "ok": True,
        "session_id": sid,
        "tool_name": tool_name,
        "operation": op,
        "warnings": warnings,
        "violations": violations,
        "status": "violation" if violations else ("warn" if warnings else "clean"),
    }


def format_hook_message(result: dict) -> str:
    if not result.get("violations") and not result.get("warnings"):
        return ""
    lines = ["NEXO DISCIPLINE:"]
    for item in result.get("warnings", []):
        if item.get("message") and not item.get("learning_ids"):
            lines.append(f"- PROTOCOL REMINDER: {item['message']}")
            continue
        if item.get("debt_id"):
            lines.append(
                f"- REVIEW FILE RULES: {item['file']} -> learnings {item['learning_ids']}. "
                f"{item['message']} (debt={item['debt_type']}, debt_id={item['debt_id']})"
            )
        else:
            lines.append(
                f"- REVIEW FILE RULES: {item['file']} -> learnings {item['learning_ids']}. "
                f"{item['message']}"
            )
    for item in result.get("violations", []):
        lines.append(
            f"- DEBT RECORDED: {item['debt_type']} on {item['file']} "
            f"(task={item['task_id'] or 'none'}, debt_id={item['debt_id']}, learnings={item['learning_ids']})"
        )
    return "\n".join(lines)


def format_pretool_block_message(result: dict) -> str:
    blocks = result.get("blocks") or []
    if not blocks:
        return ""
    strictness = str(result.get("strictness") or "strict")
    if any(item.get("reason_code") == "automation_live_repo" for item in blocks):
        header = "NEXO AUTOMATION SAFETY BLOCKED THIS EDIT:"
    else:
        header = (
            "NEXO LEARNING MODE BLOCKED THIS EDIT:"
            if strictness == "learning"
            else "NEXO STRICT MODE BLOCKED THIS EDIT:"
        )
    lines = [header]
    for item in blocks:
        file_note = item["file"] or "(unknown target)"
        if item.get("reason_code") == "missing_startup":
            lines.append(
                f"- Start the shared-brain session first: call `nexo_startup`, then `nexo_task_open`, before editing {file_note}."
            )
        elif item.get("reason_code") == "automation_live_repo":
            lines.append(
                f"- {file_note}: automation sessions cannot write to the live NEXO repo. "
                "Use an isolated checkout/worktree or the public contribution Draft PR flow."
            )
        elif item.get("reason_code") == "guard_unacknowledged":
            lines.append(
                f"- {file_note}: task {item['task_id']} still has blocking guard debt. Acknowledge it with `nexo_task_acknowledge_guard` before retrying."
            )
        elif item.get("reason_code") == "missing_file_guard":
            lines.append(
                f"- {file_note}: `nexo_guard_check` obligatorio antes de editar. "
                f"Run `nexo_guard_check(files='{file_note}')` first, then retry the edit."
            )
        elif strictness == "learning":
            lines.append(
                f"- {file_note}: open `nexo_task_open(task_type='edit', files=['{file_note}'])` first, then rerun the edit."
            )
        else:
            lines.append(
                f"- {file_note}: open `nexo_task_open(... files=['{file_note}'])` before editing."
            )
    return "\n".join(lines)


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    if os.environ.get("NEXO_HOOK_PHASE", "").strip().lower() == "pre":
        result = process_pre_tool_event(payload)
        message = format_pretool_block_message(result)
        if message:
            print(message, file=sys.stderr)
        return 2 if result.get("status") == "blocked" else 0
    result = process_tool_event(payload)
    message = format_hook_message(result)
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
