from __future__ import annotations
"""NEXO DB — Drive/Curiosity signals for autonomous investigation."""

import importlib
import json
import sys
from datetime import datetime, timezone


def _core():
    module = sys.modules.get("db._core")
    if module is None:
        module = importlib.import_module("db._core")
    return module


MAX_ACTIVE_SIGNALS = 30
REINFORCE_BOOST = 0.15
RISING_THRESHOLD = 0.4
READY_THRESHOLD = 0.7
RISING_DECAY_RATE = 0.03  # slower decay once rising

VALID_SIGNAL_TYPES = {"anomaly", "pattern", "connection", "gap", "opportunity"}
VALID_STATUSES = {"latent", "rising", "ready", "acted", "dismissed"}


def _table_exists(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='drive_signals' LIMIT 1"
    ).fetchone()
    return row is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create_drive_signal(
    signal_type: str,
    source: str,
    summary: str,
    source_id: str = "",
    area: str = "",
    tension: float = 0.3,
    decay_rate: float = 0.05,
    evidence: list[str] | None = None,
) -> dict:
    """Create a new drive signal. Returns the created row as dict."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return {"ok": False, "error": "drive_signals table not yet created"}

    if signal_type not in VALID_SIGNAL_TYPES:
        return {"ok": False, "error": f"Invalid signal_type: {signal_type}. Must be one of {VALID_SIGNAL_TYPES}"}

    tension = max(0.0, min(1.0, tension))
    evidence_json = json.dumps(evidence or [summary[:200]], ensure_ascii=False)
    now = _now_iso()

    # Enforce max active signals — drop weakest latent if at limit
    active_count = conn.execute(
        "SELECT COUNT(*) FROM drive_signals WHERE status IN ('latent', 'rising', 'ready')"
    ).fetchone()[0]
    if active_count >= MAX_ACTIVE_SIGNALS:
        conn.execute(
            "DELETE FROM drive_signals WHERE id = ("
            "  SELECT id FROM drive_signals"
            "  WHERE status = 'latent'"
            "  ORDER BY tension ASC, first_seen ASC"
            "  LIMIT 1"
            ")"
        )
        conn.commit()

    cursor = conn.execute(
        "INSERT INTO drive_signals "
        "(signal_type, source, source_id, area, summary, tension, evidence, "
        " status, first_seen, last_reinforced, decay_rate) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'latent', ?, ?, ?)",
        (signal_type, source, source_id, area, summary, tension,
         evidence_json, now, now, decay_rate),
    )
    conn.commit()
    signal_id = cursor.lastrowid
    return {"ok": True, "id": signal_id, "tension": tension, "status": "latent"}


def reinforce_drive_signal(signal_id: int, observation: str) -> dict:
    """Reinforce an existing signal: add evidence, boost tension, maybe promote status."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return {"ok": False, "error": "drive_signals table not yet created"}

    row = conn.execute(
        "SELECT * FROM drive_signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Signal {signal_id} not found"}

    status = row["status"]
    if status in ("acted", "dismissed"):
        return {"ok": False, "error": f"Signal {signal_id} is already {status}"}

    # Update evidence
    try:
        evidence = json.loads(row["evidence"] or "[]")
    except (json.JSONDecodeError, TypeError):
        evidence = []
    evidence.append(observation[:500])

    # Boost tension
    old_tension = float(row["tension"] or 0.3)
    new_tension = min(1.0, old_tension + REINFORCE_BOOST)

    # Status promotion
    new_status = status
    reinforce_count = len(evidence)
    if new_tension >= READY_THRESHOLD or reinforce_count >= 3:
        new_status = "ready"
    elif new_tension >= RISING_THRESHOLD:
        new_status = "rising"

    # Rising signals decay slower
    new_decay = RISING_DECAY_RATE if new_status in ("rising", "ready") else float(row["decay_rate"] or 0.05)

    now = _now_iso()
    conn.execute(
        "UPDATE drive_signals SET tension = ?, evidence = ?, status = ?, "
        "decay_rate = ?, last_reinforced = ? WHERE id = ?",
        (new_tension, json.dumps(evidence, ensure_ascii=False),
         new_status, new_decay, now, signal_id),
    )
    conn.commit()
    return {
        "ok": True, "id": signal_id,
        "old_tension": old_tension, "new_tension": new_tension,
        "old_status": status, "new_status": new_status,
        "evidence_count": reinforce_count,
    }


def get_drive_signals(
    status: str | None = None,
    area: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """List active drive signals, optionally filtered."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return []

    clauses = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    else:
        # Default: only active signals
        clauses.append("status IN ('latent', 'rising', 'ready')")
    if area:
        clauses.append("area = ?")
        params.append(area)

    where = " AND ".join(clauses) if clauses else "1=1"
    params.append(min(limit, 100))

    rows = conn.execute(
        f"SELECT * FROM drive_signals WHERE {where} ORDER BY tension DESC, last_reinforced DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_drive_signal(signal_id: int) -> dict | None:
    """Get a single signal with full details."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return None

    row = conn.execute(
        "SELECT * FROM drive_signals WHERE id = ?", (signal_id,)
    ).fetchone()
    return dict(row) if row else None


def update_drive_signal_status(
    signal_id: int,
    status: str,
    outcome: str = "",
) -> dict:
    """Transition a signal to a new status (acted/dismissed)."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return {"ok": False, "error": "drive_signals table not yet created"}

    if status not in VALID_STATUSES:
        return {"ok": False, "error": f"Invalid status: {status}"}

    row = conn.execute(
        "SELECT * FROM drive_signals WHERE id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"Signal {signal_id} not found"}

    now = _now_iso()
    updates = {"status": status}
    if status == "acted":
        updates["acted_at"] = now
    if outcome:
        updates["outcome"] = outcome

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [signal_id]
    conn.execute(f"UPDATE drive_signals SET {set_clause} WHERE id = ?", params)
    conn.commit()
    return {"ok": True, "id": signal_id, "new_status": status}


def decay_drive_signals() -> dict:
    """Apply daily decay to all active signals. Kill those at or below 0."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return {"decayed": 0, "killed": 0}

    # Ready signals don't decay
    rows = conn.execute(
        "SELECT id, tension, decay_rate, status FROM drive_signals "
        "WHERE status IN ('latent', 'rising')"
    ).fetchall()

    decayed = 0
    killed = 0
    for row in rows:
        new_tension = float(row["tension"]) - float(row["decay_rate"] or 0.05)
        if new_tension <= 0:
            conn.execute("DELETE FROM drive_signals WHERE id = ?", (row["id"],))
            killed += 1
        else:
            conn.execute(
                "UPDATE drive_signals SET tension = ? WHERE id = ?",
                (new_tension, row["id"]),
            )
            decayed += 1

    conn.commit()
    return {"decayed": decayed, "killed": killed}


def find_similar_drive_signal(summary: str, area: str = "") -> dict | None:
    """Find an existing active signal similar to the given summary.

    Uses keyword overlap heuristic to avoid duplicates.
    """
    conn = _core().get_db()
    if not _table_exists(conn):
        return None

    # Extract meaningful words (4+ chars) from summary
    words = {w.lower() for w in summary.split() if len(w) >= 4}
    if not words:
        return None

    clauses = ["status IN ('latent', 'rising', 'ready')"]
    params: list = []
    if area:
        clauses.append("area = ?")
        params.append(area)

    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM drive_signals WHERE {where} ORDER BY tension DESC",
        params,
    ).fetchall()

    best_match = None
    best_score = 0.0
    for row in rows:
        row_words = {w.lower() for w in (row["summary"] or "").split() if len(w) >= 4}
        if not row_words:
            continue
        overlap = len(words & row_words)
        score = overlap / max(len(words | row_words), 1)
        if score > best_score and score >= 0.4:  # 40% word overlap threshold
            best_score = score
            best_match = dict(row)

    return best_match


def drive_signal_stats() -> dict:
    """Return aggregate stats about drive signals."""
    conn = _core().get_db()
    if not _table_exists(conn):
        return {"total": 0, "by_status": {}, "by_type": {}, "by_area": {}}

    total = conn.execute("SELECT COUNT(*) FROM drive_signals").fetchone()[0]

    by_status = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as cnt FROM drive_signals GROUP BY status"
    ).fetchall():
        by_status[row["status"]] = row["cnt"]

    by_type = {}
    for row in conn.execute(
        "SELECT signal_type, COUNT(*) as cnt FROM drive_signals "
        "WHERE status IN ('latent', 'rising', 'ready') GROUP BY signal_type"
    ).fetchall():
        by_type[row["signal_type"]] = row["cnt"]

    by_area = {}
    for row in conn.execute(
        "SELECT area, COUNT(*) as cnt FROM drive_signals "
        "WHERE status IN ('latent', 'rising', 'ready') AND area != '' GROUP BY area"
    ).fetchall():
        by_area[row["area"]] = row["cnt"]

    return {"total": total, "by_status": by_status, "by_type": by_type, "by_area": by_area}
