"""Morning briefing persistence and Desktop-facing accessors."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import db as nexo_db
from paths import operations_dir


LATEST_MARKDOWN_FILE = operations_dir() / "morning-briefing-latest.md"
LATEST_HTML_FILE = operations_dir() / "morning-briefing-latest.html"
LATEST_JSON_FILE = operations_dir() / "morning-briefing-latest.json"

PRESENTATION_COLUMNS = {
    "body_text": "TEXT DEFAULT ''",
    "body_html": "TEXT DEFAULT ''",
    "artifact_json": "TEXT DEFAULT ''",
    "desktop_shown_at": "TEXT DEFAULT NULL",
    "desktop_opened_at": "TEXT DEFAULT NULL",
    "desktop_dismissed_at": "TEXT DEFAULT NULL",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _conn():
    nexo_db.init_db()
    return nexo_db.get_db()


def ensure_morning_briefing_runs_table(conn=None) -> None:
    conn = conn or _conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS morning_briefing_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_date TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'in_progress',
            subject TEXT DEFAULT '',
            body_text TEXT DEFAULT '',
            body_html TEXT DEFAULT '',
            artifact_json TEXT DEFAULT '',
            send_output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            desktop_shown_at TEXT DEFAULT NULL,
            desktop_opened_at TEXT DEFAULT NULL,
            desktop_dismissed_at TEXT DEFAULT NULL,
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT DEFAULT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(local_date, recipient)
        )"""
    )
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(morning_briefing_runs)").fetchall()
    }
    for name, ddl in PRESENTATION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE morning_briefing_runs ADD COLUMN {name} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_date "
        "ON morning_briefing_runs(local_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_status "
        "ON morning_briefing_runs(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_morning_briefing_runs_desktop "
        "ON morning_briefing_runs(status, desktop_shown_at, finished_at)"
    )


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _artifact_paths() -> dict[str, str]:
    return {
        "markdown": str(LATEST_MARKDOWN_FILE),
        "html": str(LATEST_HTML_FILE),
        "json": str(LATEST_JSON_FILE),
    }


def write_latest_briefing_artifacts(
    *,
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str,
    local_date: str = "",
    run_id: int | None = None,
) -> dict[str, Any]:
    generated_at = _now()
    LATEST_MARKDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexo.morning_briefing.v1",
        "generated_at": generated_at,
        "local_date": local_date,
        "run_id": run_id,
        "recipient": recipient,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "artifacts": _artifact_paths(),
    }
    markdown = (
        "# Morning briefing\n\n"
        f"- Generated at: {generated_at}\n"
        f"- To: {recipient}\n"
        f"- Subject: {subject}\n\n"
        f"{body_text}\n"
    )
    LATEST_MARKDOWN_FILE.write_text(markdown, encoding="utf-8")
    LATEST_HTML_FILE.write_text(body_html, encoding="utf-8")
    LATEST_JSON_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def mark_morning_briefing_sent(
    *,
    local_date: str,
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str,
    send_output: str = "",
    artifact_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = _conn()
    ensure_morning_briefing_runs_table(conn)
    now = _now()
    artifact_json = json.dumps(artifact_payload or {}, ensure_ascii=False)
    conn.execute(
        """
        UPDATE morning_briefing_runs
        SET status = 'sent',
            subject = ?,
            body_text = ?,
            body_html = ?,
            artifact_json = ?,
            send_output = ?,
            error = '',
            finished_at = ?,
            updated_at = ?
        WHERE local_date = ? AND recipient = ?
        """,
        (
            str(subject or ""),
            str(body_text or ""),
            str(body_html or ""),
            artifact_json,
            str(send_output or ""),
            now,
            now,
            str(local_date or ""),
            str(recipient or ""),
        ),
    )
    conn.commit()
    return latest_morning_briefing()


def latest_morning_briefing(*, include_non_sent: bool = False) -> dict[str, Any]:
    conn = _conn()
    ensure_morning_briefing_runs_table(conn)
    if include_non_sent:
        row = conn.execute(
            """
            SELECT * FROM morning_briefing_runs
            ORDER BY COALESCE(finished_at, updated_at, started_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM morning_briefing_runs
            WHERE status = 'sent'
            ORDER BY COALESCE(finished_at, updated_at, started_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    payload = public_briefing_payload(_row_to_dict(row))
    return {"ok": True, "briefing": payload}


def public_briefing_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row:
        return None
    artifact_payload: dict[str, Any] = {}
    try:
        parsed = json.loads(row.get("artifact_json") or "{}")
        if isinstance(parsed, dict):
            artifact_payload = parsed
    except Exception:
        artifact_payload = {}
    return {
        "id": row.get("id"),
        "local_date": row.get("local_date") or "",
        "recipient": row.get("recipient") or "",
        "status": row.get("status") or "",
        "subject": row.get("subject") or "",
        "body_text": row.get("body_text") or "",
        "body_html": row.get("body_html") or "",
        "send_output": row.get("send_output") or "",
        "error": row.get("error") or "",
        "started_at": row.get("started_at") or "",
        "finished_at": row.get("finished_at") or "",
        "updated_at": row.get("updated_at") or "",
        "desktop_shown_at": row.get("desktop_shown_at") or "",
        "desktop_opened_at": row.get("desktop_opened_at") or "",
        "desktop_dismissed_at": row.get("desktop_dismissed_at") or "",
        "unseen": not bool(row.get("desktop_shown_at")),
        "artifacts": artifact_payload.get("artifacts") or _artifact_paths(),
        "schema": "nexo.morning_briefing.v1",
    }


def mark_desktop_state(action: str, *, briefing_id: int | None = None) -> dict[str, Any]:
    field_by_action = {
        "shown": "desktop_shown_at",
        "opened": "desktop_opened_at",
        "dismissed": "desktop_dismissed_at",
    }
    field = field_by_action.get(str(action or "").strip().lower())
    if not field:
        return {"ok": False, "error": f"Unknown briefing mark action: {action}"}
    conn = _conn()
    ensure_morning_briefing_runs_table(conn)
    if briefing_id:
        row = _row_to_dict(conn.execute(
            "SELECT * FROM morning_briefing_runs WHERE id = ? LIMIT 1",
            (int(briefing_id),),
        ).fetchone())
    else:
        row = (latest_morning_briefing().get("briefing") or {})
        if row:
            row = _row_to_dict(conn.execute(
                "SELECT * FROM morning_briefing_runs WHERE id = ? LIMIT 1",
                (int(row.get("id") or 0),),
            ).fetchone())
    if not row:
        return {"ok": False, "error": "No morning briefing found."}
    now = _now()
    conn.execute(
        f"UPDATE morning_briefing_runs SET {field} = ?, updated_at = ? WHERE id = ?",
        (now, now, int(row.get("id"))),
    )
    conn.commit()
    updated = _row_to_dict(conn.execute(
        "SELECT * FROM morning_briefing_runs WHERE id = ? LIMIT 1",
        (int(row.get("id")),),
    ).fetchone())
    return {"ok": True, "briefing": public_briefing_payload(updated), "marked": action}


__all__ = [
    "LATEST_HTML_FILE",
    "LATEST_JSON_FILE",
    "LATEST_MARKDOWN_FILE",
    "ensure_morning_briefing_runs_table",
    "latest_morning_briefing",
    "mark_desktop_state",
    "mark_morning_briefing_sent",
    "public_briefing_payload",
    "write_latest_briefing_artifacts",
]
