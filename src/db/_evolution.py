"""NEXO DB — Evolution module."""
from db._core import get_db

# ── Evolution helpers ─────────────────────────────────────────────────────

def insert_evolution_metric(dimension: str, score: int, evidence: str, delta: int = 0):
    conn = get_db()
    conn.execute(
        "INSERT INTO evolution_metrics (dimension, score, evidence, delta) VALUES (?, ?, ?, ?)",
        (dimension, score, evidence, delta)
    )


def get_latest_metrics() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT dimension, score, delta, measured_at FROM evolution_metrics "
        "WHERE id IN (SELECT MAX(id) FROM evolution_metrics GROUP BY dimension)"
    ).fetchall()
    return {r["dimension"]: dict(r) for r in rows}


def insert_evolution_log(cycle_number: int, dimension: str, proposal: str,
                         classification: str, reasoning: str, **kwargs) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, reasoning, "
        "files_changed, snapshot_ref, test_result, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cycle_number, dimension, proposal, classification, reasoning,
         kwargs.get("files_changed"), kwargs.get("snapshot_ref"),
         kwargs.get("test_result"), kwargs.get("status", "pending"))
    )
    return cur.lastrowid


def get_evolution_history(limit: int = 20) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM evolution_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_evolution_log_status(log_id: int, status: str, **kwargs):
    conn = get_db()
    sets = ["status = ?"]
    vals = [status]
    for k in ("test_result", "impact", "files_changed", "snapshot_ref"):
        if k in kwargs:
            sets.append(f"{k} = ?")
            vals.append(kwargs[k])
    vals.append(log_id)
    conn.execute(f"UPDATE evolution_log SET {', '.join(sets)} WHERE id = ?", vals)
