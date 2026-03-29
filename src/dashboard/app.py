"""NEXO Brain Dashboard — FastAPI app for inspecting cognitive state.

Local dashboard: graphs, memories, somatic markers, trust, adaptive personality.
Runs on-demand (not embedded in MCP stdio). Opens browser automatically.

Usage:
    python3 -m dashboard.app [--port 6174] [--no-browser]
"""

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

# Add parent dir to path so we can import nexo-mcp modules
_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

app = FastAPI(title="NEXO Brain Dashboard", version="1.0.0")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# ---------------------------------------------------------------------------
# Lazy imports — modules live in the parent nexo-mcp directory
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
async def page_overview():
    return _render_template("overview.html")


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
# API endpoints — JSON
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
        # Fallback: try with type prefix (refs stored as "area:my-project", "file:path")
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
    """Recent session diaries."""
    db = _db()
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM session_diary ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    diaries = [dict(r) for r in rows]
    return {"count": len(diaries), "sessions": diaries}


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
