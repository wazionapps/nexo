from __future__ import annotations

"""Post-tool guardrails for conditioned file learnings."""

import json
import sys
from pathlib import Path

from db import create_protocol_debt, get_db
from plugins.guard import _load_conditioned_learnings, _normalize_path_token

READ_LIKE_TOOLS = {"Read"}
WRITE_LIKE_TOOLS = {"Edit", "MultiEdit", "Write"}
DELETE_LIKE_TOOLS = {"Delete"}


def _operation_kind(tool_name: str) -> str:
    if tool_name in READ_LIKE_TOOLS:
        return "read"
    if tool_name in WRITE_LIKE_TOOLS:
        return "write"
    if tool_name in DELETE_LIKE_TOOLS:
        return "delete"
    return "other"


def _normalize_file_path(path: str) -> str:
    return _normalize_path_token(str(Path(path)))


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
    if not external_session_id.strip():
        return ""
    row = conn.execute(
        """SELECT sid
           FROM sessions
           WHERE external_session_id = ? OR claude_session_id = ?
           ORDER BY last_update_epoch DESC
           LIMIT 1""",
        (external_session_id.strip(), external_session_id.strip()),
    ).fetchone()
    return str(row["sid"]) if row else ""


def _find_open_task_for_file(conn, sid: str, filepath: str) -> dict | None:
    target = _normalize_file_path(filepath)
    rows = conn.execute(
        """SELECT task_id, files, guard_has_blocking
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


def process_tool_event(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name", "")).strip()
    op = _operation_kind(tool_name)
    if op == "other":
        return {"ok": True, "skipped": True, "reason": "tool not monitored"}

    tool_input = payload.get("tool_input")
    files = _extract_touched_files(tool_input)
    if not files:
        return {"ok": True, "skipped": True, "reason": "no touched files found"}

    conn = get_db()
    sid = _resolve_nexo_sid(conn, str(payload.get("session_id", "")))
    if not sid:
        return {"ok": True, "skipped": True, "reason": "session not mapped to nexo"}

    conditioned = _load_conditioned_learnings(conn, files)
    warnings: list[dict] = []
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


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    result = process_tool_event(payload)
    message = format_hook_message(result)
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
