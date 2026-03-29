"""NEXO DB — Credentials CRUD."""
import sqlite3
import time


def _get_db():
    from db import get_db
    return get_db()


def _now_epoch():
    return time.time()


def create_credential(service: str, key: str, value: str, notes: str = '') -> dict:
    """Create a new credential entry."""
    conn = _get_db()
    now = _now_epoch()
    try:
        conn.execute(
            "INSERT INTO credentials (service, key, value, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (service, key, value, notes, now, now)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return {"error": f"Credential {service}/{key} already exists. Use update instead."}
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    return dict(row)


def update_credential(service: str, key: str, value: str = None, notes: str = None) -> dict:
    """Update value and/or notes for a credential."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    if not row:
        return {"error": f"Credential {service}/{key} not found"}
    updates = {"updated_at": _now_epoch()}
    if value is not None:
        updates["value"] = value
    if notes is not None:
        updates["notes"] = notes
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [service, key]
    conn.execute(
        f"UPDATE credentials SET {set_clause} WHERE service = ? AND key = ?", values
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
    ).fetchone()
    return dict(row)


def delete_credential(service: str, key: str = None) -> bool:
    """Delete credential(s). If key=None, delete all for the service."""
    conn = _get_db()
    if key:
        result = conn.execute(
            "DELETE FROM credentials WHERE service = ? AND key = ?", (service, key)
        )
    else:
        result = conn.execute(
            "DELETE FROM credentials WHERE service = ?", (service,)
        )
    conn.commit()
    return result.rowcount > 0


def get_credential(service: str, key: str = None) -> list[dict]:
    """Get credential(s). Fuzzy fallback if exact match fails."""
    conn = _get_db()
    if key:
        rows = conn.execute(
            "SELECT * FROM credentials WHERE service = ? AND key = ?", (service, key)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM credentials WHERE service = ?", (service,)
        ).fetchall()
    if rows:
        return [dict(r) for r in rows]

    # Fuzzy fallback
    term = f"%{service}%"
    fuzzy_rows = conn.execute(
        "SELECT *, "
        "CASE WHEN service LIKE ? THEN 0 "
        "     WHEN key LIKE ? THEN 1 "
        "     ELSE 2 END AS _rank "
        "FROM credentials WHERE "
        "service LIKE ? OR key LIKE ? OR notes LIKE ? "
        "ORDER BY _rank ASC, service ASC, key ASC",
        (term, term, term, term, term),
    ).fetchall()
    results = []
    for r in fuzzy_rows:
        d = dict(r)
        d["_fuzzy"] = True
        d.pop("_rank", None)
        results.append(d)
    return results


def list_credentials(service: str = None) -> list[dict]:
    """List service+key only (NO values) for security."""
    conn = _get_db()
    if service:
        rows = conn.execute(
            "SELECT id, service, key, notes, created_at, updated_at "
            "FROM credentials WHERE service = ? ORDER BY key ASC",
            (service,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, service, key, notes, created_at, updated_at "
            "FROM credentials ORDER BY service ASC, key ASC"
        ).fetchall()
    return [dict(r) for r in rows]
