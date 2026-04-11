"""Observability for the NEXO hook lifecycle pipeline.

Closes Fase 3 item 7 of NEXO-AUDIT-2026-04-11. Before this module, NEXO
had 12 hook scripts (session-start.sh, post-compact.sh, pre-compact.sh,
inbox-hook.sh, etc.) but no central record of when they ran, how long
they took, or whether they succeeded. The audit lifecycle was a black box
— a hook could silently fail for weeks before anyone noticed.

This module is the API layer on top of the m39 hook_runs table:

  record_hook_run(hook_name, ...)  -> int (rowid)
  list_recent_hook_runs(hours=24, hook_name='', status='', limit=200)
  hook_health_summary(hours=24)    -> dict with success rate per hook

It is consumed by:
  - src/scripts/nexo-hook-record.py: a tiny shell-friendly CLI so any
    bash hook can pipe its result back into the database with one line.
  - src/server.py:nexo_hook_runs: an MCP tool so the agent can read the
    hook lifecycle without needing the dashboard.

Best-effort throughout: every helper wraps the DB call in try/except so
the hook itself never fails because observability could not write.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from db import get_db


_VALID_STATUS = {"ok", "error", "skipped", "timeout", "blocked"}


def _coerce_status(exit_code: int, status: str = "") -> str:
    """Derive status from exit_code when not explicitly provided."""
    s = (status or "").strip().lower()
    if s in _VALID_STATUS:
        return s
    if exit_code == 0:
        return "ok"
    return "error"


def record_hook_run(
    hook_name: str,
    *,
    started_at: float | None = None,
    duration_ms: int = 0,
    exit_code: int = 0,
    status: str = "",
    session_id: str = "",
    summary: str = "",
    metadata: dict | None = None,
) -> int:
    """Insert a single row into hook_runs and return its id.

    Args:
        hook_name: The hook identifier (e.g. 'session-start', 'post-compact').
        started_at: Unix epoch when the hook started. Defaults to now.
        duration_ms: Wall-clock duration in milliseconds.
        exit_code: Process exit code (0 = ok). When status is empty, it is
            derived from this value.
        status: One of {ok, error, skipped, timeout, blocked}. Optional.
        session_id: Claude Code session id when known.
        summary: Short human-readable summary (truncated to 500 chars).
        metadata: Extra JSON-serializable payload (truncated to 4 KB serialized).

    Returns:
        The new hook_runs row id, or 0 if the insert failed.

    This helper never raises. A failure here must never block the hook.
    """
    name = (hook_name or "").strip()
    if not name:
        return 0
    if started_at is None:
        started_at = time.time()
    try:
        duration_ms = max(0, int(duration_ms))
    except (TypeError, ValueError):
        duration_ms = 0
    try:
        exit_code = int(exit_code)
    except (TypeError, ValueError):
        exit_code = 0
    final_status = _coerce_status(exit_code, status)
    summary_clean = (summary or "")[:500]
    try:
        metadata_blob = json.dumps(metadata or {}, ensure_ascii=False)
    except Exception:
        metadata_blob = "{}"
    if len(metadata_blob) > 4096:
        metadata_blob = metadata_blob[:4096]
    now_epoch = time.time()
    try:
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO hook_runs (hook_name, started_at, duration_ms, exit_code, "
            "status, session_id, summary, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name[:120],
                float(started_at),
                duration_ms,
                exit_code,
                final_status,
                (session_id or "")[:80],
                summary_clean,
                metadata_blob,
                now_epoch,
            ),
        )
        try:
            conn.commit()
        except Exception:
            pass
        return int(cur.lastrowid or 0)
    except Exception:
        return 0


def list_recent_hook_runs(
    *,
    hours: int = 24,
    hook_name: str = "",
    status: str = "",
    limit: int = 200,
) -> list[dict]:
    """Return recent hook_runs filtered by time window, name, and status.

    Args:
        hours: How far back to look. Default 24h.
        hook_name: Optional substring filter on hook_name (LIKE %name%).
        status: Optional exact match on status field.
        limit: Max rows. Default 200.

    Returns ordered list (newest first) of dicts. Empty list on any error.
    """
    try:
        cutoff = time.time() - max(60, int(hours)) * 3600
    except (TypeError, ValueError):
        cutoff = time.time() - 86400
    clauses = ["started_at >= ?"]
    params: list[Any] = [cutoff]
    if hook_name:
        clauses.append("hook_name LIKE ?")
        params.append(f"%{hook_name.strip()}%")
    if status:
        clauses.append("status = ?")
        params.append(status.strip().lower())
    where = " AND ".join(clauses)
    try:
        conn = get_db()
        rows = conn.execute(
            f"SELECT id, hook_name, started_at, duration_ms, exit_code, status, "
            f"session_id, summary, metadata, created_at FROM hook_runs "
            f"WHERE {where} ORDER BY started_at DESC LIMIT ?",
            params + [max(1, int(limit))],
        ).fetchall()
    except Exception:
        return []
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result


def hook_health_summary(hours: int = 24) -> dict:
    """Aggregate per-hook health stats over a time window.

    Returns dict with shape:
        {
          "window_hours": N,
          "total_runs": N,
          "by_hook": [
            {"hook_name": str, "runs": int, "ok": int, "errors": int,
             "p50_duration_ms": int, "p95_duration_ms": int,
             "success_rate": float (0..1), "last_run_at": float},
            ...
          ],
          "unhealthy_hooks": [hook_name, ...]   # success rate < 0.8 with >= 3 runs
        }

    Used by:
      - nexo_hook_runs MCP tool
      - dashboard widgets
      - the daily self-audit's hook health column
    """
    try:
        cutoff = time.time() - max(60, int(hours)) * 3600
    except (TypeError, ValueError):
        cutoff = time.time() - 86400
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT hook_name, status, duration_ms, started_at "
            "FROM hook_runs WHERE started_at >= ? ORDER BY hook_name, started_at",
            (cutoff,),
        ).fetchall()
    except Exception:
        return {"window_hours": hours, "total_runs": 0, "by_hook": [], "unhealthy_hooks": []}

    by_hook: dict[str, dict] = {}
    for row in rows:
        name = row["hook_name"]
        bucket = by_hook.setdefault(
            name,
            {"hook_name": name, "runs": 0, "ok": 0, "errors": 0, "_durations": [], "last_run_at": 0.0},
        )
        bucket["runs"] += 1
        status = row["status"]
        if status == "ok":
            bucket["ok"] += 1
        elif status in {"error", "timeout", "blocked"}:
            bucket["errors"] += 1
        bucket["_durations"].append(int(row["duration_ms"] or 0))
        if row["started_at"] > bucket["last_run_at"]:
            bucket["last_run_at"] = float(row["started_at"])

    summary_rows = []
    unhealthy = []
    for name, bucket in by_hook.items():
        durations = sorted(bucket.pop("_durations"))
        n = len(durations)
        if n:
            p50 = durations[n // 2]
            p95 = durations[min(n - 1, int(n * 0.95))]
        else:
            p50 = p95 = 0
        success_rate = (bucket["ok"] / bucket["runs"]) if bucket["runs"] else 0.0
        bucket["p50_duration_ms"] = p50
        bucket["p95_duration_ms"] = p95
        bucket["success_rate"] = round(success_rate, 3)
        summary_rows.append(bucket)
        if bucket["runs"] >= 3 and success_rate < 0.8:
            unhealthy.append(name)

    summary_rows.sort(key=lambda b: b["runs"], reverse=True)
    return {
        "window_hours": hours,
        "total_runs": sum(b["runs"] for b in summary_rows),
        "by_hook": summary_rows,
        "unhealthy_hooks": unhealthy,
    }


def main_cli(argv: list[str]) -> int:
    """Tiny CLI shim so bash hooks can call this module directly.

    Usage from a hook:
      python3 -m hook_observability record \
          --hook session-start --duration-ms 142 --exit 0 --session abc

    The first positional verb selects the action (`record` only for now).
    Returns 0 always — the recorder must never break the hook itself.
    """
    if len(argv) < 1 or argv[0] != "record":
        print("usage: hook_observability record --hook NAME [--duration-ms N] [--exit N] [--session SID] [--summary TEXT]")
        return 0
    args: dict[str, str] = {}
    i = 1
    while i < len(argv):
        token = argv[i]
        if token.startswith("--") and i + 1 < len(argv):
            args[token[2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    try:
        record_hook_run(
            args.get("hook", ""),
            duration_ms=int(args.get("duration-ms", "0") or 0),
            exit_code=int(args.get("exit", "0") or 0),
            status=args.get("status", ""),
            session_id=args.get("session", ""),
            summary=args.get("summary", ""),
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:]))
