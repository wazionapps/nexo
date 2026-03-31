"""NEXO Brain Dashboard — FastAPI app for inspecting cognitive state.

Local dashboard: graphs, memories, somatic markers, trust, adaptive personality.
Runs on-demand (not embedded in MCP stdio). Opens browser automatically.

Usage:
    python3 -m dashboard.app [--port 6174] [--no-browser]
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add parent dir to path so we can import NEXO modules
_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

app = FastAPI(title="NEXO Brain Dashboard", version="2.0.0")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Mount static files
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Startup — create dashboard_notes table
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def create_tables():
    db = _db()
    conn = db.get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL,
            content TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            reply_to INTEGER DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migration: add reply_to if missing
    try:
        conn.execute("SELECT reply_to FROM dashboard_notes LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE dashboard_notes ADD COLUMN reply_to INTEGER DEFAULT NULL")
    conn.commit()


# ---------------------------------------------------------------------------
# Lazy imports — modules live in the parent source directory
# ---------------------------------------------------------------------------

def _cognitive():
    import cognitive
    return cognitive

def _knowledge_graph():
    import knowledge_graph as kg
    return kg

def _db():
    import db as nexo_db
    return nexo_db

def _adaptive():
    from plugins import adaptive_mode
    return adaptive_mode


# ---------------------------------------------------------------------------
# Pydantic models for request bodies
# ---------------------------------------------------------------------------

class ReminderCreate(BaseModel):
    description: str
    date: Optional[str] = None
    category: Optional[str] = "general"

class ReminderUpdate(BaseModel):
    description: Optional[str] = None
    date: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None

class FollowupCreate(BaseModel):
    description: str
    date: Optional[str] = None
    verification: Optional[str] = None
    reasoning: Optional[str] = None

class FollowupUpdate(BaseModel):
    description: Optional[str] = None
    date: Optional[str] = None
    status: Optional[str] = None
    verification: Optional[str] = None
    reasoning: Optional[str] = None

class MoveRequest(BaseModel):
    id: str
    direction: str  # "to_followup" | "to_reminder"

class InboxCreate(BaseModel):
    direction: str  # "to_nexo" | "to_user"
    content: str
    reply_to: Optional[int] = None


# ---------------------------------------------------------------------------
# HTML page routes — serve template files
# ---------------------------------------------------------------------------

def _render_template(name: str) -> HTMLResponse:
    """Read a template file and return as HTML."""
    path = TEMPLATES_DIR / name
    if not path.exists():
        return HTMLResponse(
            f"<html><body><h1>Template not found: {name}</h1>"
            f"<p>Create it at <code>{path}</code></p></body></html>",
            status_code=200,
        )
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def page_dashboard():
    return _render_template("dashboard.html")


@app.get("/ops", response_class=HTMLResponse)
async def page_ops():
    return _render_template("operations.html")


@app.get("/calendar", response_class=HTMLResponse)
async def page_calendar():
    return _render_template("calendar.html")


@app.get("/inbox", response_class=HTMLResponse)
async def page_inbox():
    return _render_template("inbox.html")


@app.get("/graph", response_class=HTMLResponse)
async def page_graph():
    return _render_template("graph.html")


@app.get("/memory", response_class=HTMLResponse)
async def page_memory():
    return _render_template("memory.html")


@app.get("/somatic", response_class=HTMLResponse)
async def page_somatic():
    return _render_template("somatic.html")


@app.get("/adaptive", response_class=HTMLResponse)
async def page_adaptive():
    return _render_template("adaptive.html")


@app.get("/sessions", response_class=HTMLResponse)
async def page_sessions():
    return _render_template("sessions.html")


# ---------------------------------------------------------------------------
# API endpoints — JSON (existing)
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def api_stats():
    """Overview: trust score, memory counts, KG stats."""
    cog = _cognitive()
    kg = _knowledge_graph()

    trust = cog.get_trust_score()
    cog_stats = cog.get_stats()
    kg_stats = kg.stats()
    gate_stats = cog.get_gate_stats()

    return {
        "trust_score": trust,
        "cognitive": cog_stats,
        "knowledge_graph": kg_stats,
        "prediction_gate": gate_stats,
    }


@app.get("/api/graph")
async def api_graph(
    center: int = Query(None, description="Center node ID for subgraph"),
    depth: int = Query(2, ge=1, le=5, description="Traversal depth"),
    node_type: str = Query(None, description="Filter by node type"),
    node_ref: str = Query(None, description="Find node by type+ref"),
):
    """Subgraph for D3 visualization."""
    kg = _knowledge_graph()

    # If node_type+node_ref given, resolve to center ID
    if center is None and node_type and node_ref:
        node = kg.get_node(node_type, node_ref)
        # Fallback: try with type prefix (refs stored as "area:project-a", "file:path")
        if not node:
            node = kg.get_node(node_type, f"{node_type}:{node_ref}")
        if node:
            center = node["id"]

    if center is None:
        # Return full graph stats + top connected nodes as starting points
        s = kg.stats()
        return {
            "nodes": [],
            "edges": [],
            "hints": s.get("most_connected", []),
            "stats": {
                "total_nodes": s["nodes"],
                "total_edges": s["edges_active"],
            },
        }

    subgraph = kg.extract_subgraph(center, depth=depth)
    return subgraph


@app.get("/api/memories")
async def api_memories(
    q: str = Query("", description="Search query"),
    store: str = Query("both", description="stm, ltm, or both"),
    limit: int = Query(20, ge=1, le=100),
):
    """Memory search via cognitive engine."""
    cog = _cognitive()

    if not q:
        return {"results": [], "message": "Provide ?q= parameter to search"}

    results = cog.search(q, top_k=limit, stores=store)
    # Serialize — results may contain numpy arrays or sqlite Rows
    serialized = []
    for r in results:
        item = dict(r) if hasattr(r, "keys") else r
        # Remove embedding blob if present
        item.pop("embedding", None)
        item.pop("vec", None)
        serialized.append(item)
    return {"query": q, "store": store, "count": len(serialized), "results": serialized}


@app.get("/api/somatic")
async def api_somatic():
    """Somatic marker risk scores."""
    cog = _cognitive()
    top_risks = cog.somatic_top_risks(limit=20)
    return {"risks": top_risks}


@app.get("/api/trust")
async def api_trust():
    """Trust score history (last 30 days)."""
    cog = _cognitive()
    current = cog.get_trust_score()
    history = cog.get_trust_history(days=30)
    return {
        "current_score": current,
        "history": history,
    }


@app.get("/api/adaptive")
async def api_adaptive():
    """Adaptive personality: current weight state + mode history."""
    adp = _adaptive()
    state = adp._load_state()
    # Get recent history from DB
    db = _db()
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM adaptive_log ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    history = [dict(r) for r in rows]
    return {
        "state": state,
        "weights": adp.WEIGHTS,
        "modes": {k: v["description"] for k, v in adp.MODES.items()},
        "history": history,
    }


@app.get("/api/sessions")
async def api_sessions(limit: int = Query(10, ge=1, le=50)):
    """Recent session diaries + active sessions from sessions table."""
    db = _db()
    conn = db.get_db()
    # Active sessions (from sessions table, not diaries)
    active_rows = conn.execute(
        "SELECT sid as session_id, task, last_update_epoch, claude_session_id "
        "FROM sessions WHERE last_update_epoch > (strftime('%s','now') - 900) "
        "ORDER BY last_update_epoch DESC"
    ).fetchall()
    active = [dict(r) for r in active_rows]
    # Add last_heartbeat as ISO string for frontend
    for a in active:
        epoch = a.get("last_update_epoch", 0)
        if epoch:
            import datetime
            a["last_heartbeat"] = datetime.datetime.fromtimestamp(epoch).isoformat()
    # Recent diaries
    rows = conn.execute(
        "SELECT * FROM session_diary ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    diaries = [dict(r) for r in rows]
    return {"count": len(diaries), "sessions": active, "diaries": diaries}


@app.get("/api/kg/nodes")
async def api_kg_nodes(
    node_type: str = Query(None, description="Filter by node type"),
    limit: int = Query(100, ge=1, le=500),
):
    """List KG nodes, optionally filtered by type."""
    kg = _knowledge_graph()
    db = kg._get_db()
    if node_type:
        rows = db.execute(
            "SELECT * FROM kg_nodes WHERE node_type = ? ORDER BY id DESC LIMIT ?",
            (node_type, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM kg_nodes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    nodes = [dict(r) for r in rows]
    return {"count": len(nodes), "nodes": nodes}


# ---------------------------------------------------------------------------
# Reminders CRUD
# ---------------------------------------------------------------------------

def _next_reminder_id(conn) -> str:
    """Generate next R-prefixed ID."""
    row = conn.execute(
        "SELECT id FROM reminders WHERE id LIKE 'R%' ORDER BY CAST(SUBSTR(id,2) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            num = int(str(row[0])[1:]) + 1
        except (ValueError, IndexError):
            num = 1
    else:
        num = 1
    return f"R{num}"


@app.get("/api/reminders")
async def api_reminders_list(
    status: str = Query(None, description="Filter by status"),
    category: str = Query(None, description="Filter by category"),
):
    """List reminders."""
    db = _db()
    conn = db.get_db()
    query = "SELECT * FROM reminders WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    reminders = [dict(r) for r in rows]
    return {"count": len(reminders), "reminders": reminders}


@app.post("/api/reminders")
async def api_reminders_create(body: ReminderCreate):
    """Create a reminder."""
    db = _db()
    conn = db.get_db()
    rid = _next_reminder_id(conn)
    now = time.time()
    conn.execute(
        "INSERT INTO reminders (id, description, date, status, category, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (rid, body.description, body.date, "PENDING", body.category or "general", now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    return {"success": True, "reminder": dict(row)}


@app.put("/api/reminders/{rid}")
async def api_reminders_update(rid: str, body: ReminderUpdate):
    """Update a reminder."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Reminder {rid} not found"}, status_code=404)
    fields = []
    params = []
    if body.description is not None:
        fields.append("description = ?")
        params.append(body.description)
    if body.date is not None:
        fields.append("date = ?")
        params.append(body.date)
    if body.status is not None:
        fields.append("status = ?")
        params.append(body.status)
    if body.category is not None:
        fields.append("category = ?")
        params.append(body.category)
    if not fields:
        return {"success": True, "reminder": dict(row)}
    fields.append("updated_at = ?")
    params.append(time.time())
    params.append(rid)
    conn.execute(f"UPDATE reminders SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    return {"success": True, "reminder": dict(row)}


@app.delete("/api/reminders/{rid}")
async def api_reminders_delete(rid: str):
    """Delete a reminder."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Reminder {rid} not found"}, status_code=404)
    conn.execute("DELETE FROM reminders WHERE id = ?", (rid,))
    conn.commit()
    return {"success": True, "deleted_id": rid}


# ---------------------------------------------------------------------------
# Followups CRUD
# ---------------------------------------------------------------------------

def _next_followup_id(conn) -> str:
    """Generate next NF-prefixed ID."""
    row = conn.execute(
        "SELECT id FROM followups WHERE id LIKE 'NF%' ORDER BY CAST(SUBSTR(id,3) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            num = int(str(row[0])[2:]) + 1
        except (ValueError, IndexError):
            num = 1
    else:
        num = 1
    return f"NF{num}"


@app.get("/api/followups")
async def api_followups_list(
    status: str = Query(None, description="Filter by status"),
):
    """List followups."""
    db = _db()
    conn = db.get_db()
    query = "SELECT * FROM followups WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    followups = [dict(r) for r in rows]
    return {"count": len(followups), "followups": followups}


@app.post("/api/followups")
async def api_followups_create(body: FollowupCreate):
    """Create a followup."""
    db = _db()
    conn = db.get_db()
    fid = _next_followup_id(conn)
    now = time.time()
    conn.execute(
        "INSERT INTO followups (id, description, date, verification, status, reasoning, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (fid, body.description, body.date, body.verification, "PENDING", body.reasoning, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (fid,)).fetchone()
    return {"success": True, "followup": dict(row)}


@app.put("/api/followups/{fid}")
async def api_followups_update(fid: str, body: FollowupUpdate):
    """Update a followup."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (fid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    fields = []
    params = []
    if body.description is not None:
        fields.append("description = ?")
        params.append(body.description)
    if body.date is not None:
        fields.append("date = ?")
        params.append(body.date)
    if body.status is not None:
        fields.append("status = ?")
        params.append(body.status)
    if body.verification is not None:
        fields.append("verification = ?")
        params.append(body.verification)
    if body.reasoning is not None:
        fields.append("reasoning = ?")
        params.append(body.reasoning)
    if not fields:
        return {"success": True, "followup": dict(row)}
    fields.append("updated_at = ?")
    params.append(time.time())
    params.append(fid)
    conn.execute(f"UPDATE followups SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (fid,)).fetchone()
    return {"success": True, "followup": dict(row)}


@app.delete("/api/followups/{fid}")
async def api_followups_delete(fid: str):
    """Delete a followup."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (fid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    conn.execute("DELETE FROM followups WHERE id = ?", (fid,))
    conn.commit()
    return {"success": True, "deleted_id": fid}


# ---------------------------------------------------------------------------
# Ops: Move and Execute
# ---------------------------------------------------------------------------

@app.post("/api/ops/move")
async def api_ops_move(body: MoveRequest):
    """Move an item between reminders and followups."""
    db = _db()
    conn = db.get_db()
    now = time.time()

    if body.direction == "to_followup":
        # Read from reminders
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (body.id,)).fetchone()
        if not row:
            return JSONResponse({"error": f"Reminder {body.id} not found"}, status_code=404)
        item = dict(row)
        fid = _next_followup_id(conn)
        conn.execute(
            "INSERT INTO followups (id, description, date, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (fid, item["description"], item.get("date"), "PENDING", now, now),
        )
        conn.execute("DELETE FROM reminders WHERE id = ?", (body.id,))
        conn.commit()
        return {"success": True, "new_id": fid, "direction": "to_followup"}

    elif body.direction == "to_reminder":
        # Read from followups
        row = conn.execute("SELECT * FROM followups WHERE id = ?", (body.id,)).fetchone()
        if not row:
            return JSONResponse({"error": f"Followup {body.id} not found"}, status_code=404)
        item = dict(row)
        rid = _next_reminder_id(conn)
        conn.execute(
            "INSERT INTO reminders (id, description, date, status, category, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (rid, item["description"], item.get("date"), "PENDING", "general", now, now),
        )
        conn.execute("DELETE FROM followups WHERE id = ?", (body.id,))
        conn.commit()
        return {"success": True, "new_id": rid, "direction": "to_reminder"}

    else:
        return JSONResponse(
            {"error": f"Invalid direction: {body.direction}. Use 'to_followup' or 'to_reminder'"},
            status_code=400,
        )


@app.post("/api/ops/execute/{fid}")
async def api_ops_execute(fid: str):
    """Execute a followup by opening Terminal with claude command."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM followups WHERE id = ?", (fid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    item = dict(row)
    description = item["description"].replace('"', '\\"').replace("'", "\\'")
    if platform.system() != "Darwin":
        return JSONResponse(
            {"error": "This operation requires macOS (uses osascript to open Terminal)"},
            status_code=501,
        )
    script = f'tell application "Terminal" to do script "claude \\"NEXO: execute followup #{fid} — {description}\\""'
    subprocess.Popen(["osascript", "-e", script])
    return {"success": True, "followup_id": fid}


# ---------------------------------------------------------------------------
# Inbox endpoints
# ---------------------------------------------------------------------------

@app.get("/api/inbox")
async def api_inbox_list(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
):
    """List inbox notes."""
    db = _db()
    conn = db.get_db()
    query = "SELECT * FROM dashboard_notes WHERE 1=1"
    params = []
    if unread_only:
        query += " AND read = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    notes = [dict(r) for r in rows]
    return {"count": len(notes), "notes": notes}


@app.post("/api/inbox")
async def api_inbox_create(body: InboxCreate):
    """Create an inbox note."""
    if body.direction not in ("to_nexo", "to_user"):
        return JSONResponse(
            {"error": "direction must be 'to_nexo' or 'to_user'"},
            status_code=400,
        )
    db = _db()
    conn = db.get_db()
    conn.execute(
        "INSERT INTO dashboard_notes (direction, content, reply_to) VALUES (?, ?, ?)",
        (body.direction, body.content, body.reply_to),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM dashboard_notes ORDER BY id DESC LIMIT 1").fetchone()
    return {"success": True, "note": dict(row)}


@app.put("/api/inbox/{nid}/read")
async def api_inbox_mark_read(nid: int):
    """Mark a note as read."""
    db = _db()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM dashboard_notes WHERE id = ?", (nid,)).fetchone()
    if not row:
        return JSONResponse({"error": f"Note {nid} not found"}, status_code=404)
    conn.execute("UPDATE dashboard_notes SET read = 1 WHERE id = ?", (nid,))
    conn.commit()
    row = conn.execute("SELECT * FROM dashboard_notes WHERE id = ?", (nid,)).fetchone()
    return {"success": True, "note": dict(row)}


@app.get("/api/inbox/unread")
async def api_inbox_unread():
    """Count unread notes per direction."""
    db = _db()
    conn = db.get_db()
    rows = conn.execute(
        "SELECT direction, COUNT(*) as count FROM dashboard_notes WHERE read = 0 GROUP BY direction"
    ).fetchall()
    counts = {r["direction"]: r["count"] for r in rows}
    return {
        "to_nexo": counts.get("to_nexo", 0),
        "to_user": counts.get("to_user", 0),
        "total": sum(counts.values()),
    }


# ---------------------------------------------------------------------------
# Calendar endpoint
# ---------------------------------------------------------------------------

@app.get("/api/calendar")
async def api_calendar(
    year: int = Query(..., description="Year (e.g. 2026)"),
    month: int = Query(..., ge=1, le=12, description="Month (1-12)"),
):
    """Return all reminders and followups with dates in the given month."""
    db = _db()
    conn = db.get_db()

    # Format month prefix for LIKE query (dates stored as text YYYY-MM-DD or similar)
    month_prefix = f"{year}-{month:02d}%"

    reminder_rows = conn.execute(
        "SELECT *, 'reminder' as item_type FROM reminders WHERE date LIKE ? ORDER BY date ASC",
        (month_prefix,),
    ).fetchall()

    followup_rows = conn.execute(
        "SELECT *, 'followup' as item_type FROM followups WHERE date LIKE ? ORDER BY date ASC",
        (month_prefix,),
    ).fetchall()

    reminders = [dict(r) for r in reminder_rows]
    followups = [dict(r) for r in followup_rows]

    # Merge and sort by date
    all_items = sorted(reminders + followups, key=lambda x: x.get("date") or "")

    return {
        "year": year,
        "month": month,
        "count": len(all_items),
        "items": all_items,
        "reminders": reminders,
        "followups": followups,
    }


# ---------------------------------------------------------------------------
# Watchdog endpoint
# ---------------------------------------------------------------------------

@app.get("/api/watchdog")
async def api_watchdog():
    """Read watchdog status from file."""
    nexo_home = os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))
    watchdog_path = Path(nexo_home) / "operations" / "watchdog-status.json"
    if not watchdog_path.exists():
        return JSONResponse(
            {"error": "watchdog-status.json not found", "path": str(watchdog_path)},
            status_code=404,
        )
    try:
        data = json.loads(watchdog_path.read_text(encoding="utf-8"))
        return data
    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=500)


# ---------------------------------------------------------------------------
# Main — run with uvicorn
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NEXO Brain Dashboard")
    parser.add_argument("--port", type=int, default=6174, help="Port (default: 6174)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    if not args.no_browser:
        # Open browser after a short delay (uvicorn will be starting)
        import threading
        def _open():
            import time
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
