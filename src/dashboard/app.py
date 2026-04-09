"""NEXO Brain Dashboard v3.0 — Full 23-module cognitive dashboard.

Usage:
    python3 -m dashboard.app [--port 6174] [--no-browser]
"""

import argparse
import datetime
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

# Add parent dir to path so we can import NEXO modules
_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from agent_runner import AgentRunnerError, build_followup_terminal_shell_command

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Jinja2 environment
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
)

def _create_tables() -> None:
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


@asynccontextmanager
async def _dashboard_lifespan(_: FastAPI):
    _create_tables()
    yield


app = FastAPI(title="NEXO Brain Dashboard", version="3.0.1", lifespan=_dashboard_lifespan)

# Mount static files
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
    read_token: Optional[str] = None

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
    read_token: Optional[str] = None

class MoveRequest(BaseModel):
    id: str
    direction: str  # "to_followup" | "to_reminder"
    read_token: Optional[str] = None

class InboxCreate(BaseModel):
    direction: str  # "to_nexo" | "to_user"
    content: str
    reply_to: Optional[int] = None

class ChatMessage(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helper DB connections
# ---------------------------------------------------------------------------

def _cognitive_db():
    """Direct connection to cognitive.db."""
    nexo_home = os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))
    db_path = Path(nexo_home) / "data" / "cognitive.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def _email_db():
    """Direct connection to nexo-email.db."""
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    db_path = nexo_home / "nexo-email" / "nexo-email.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _deep_sleep_dir() -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    return nexo_home / "operations" / "deep-sleep"


def _normalize_item_status(status: object) -> str:
    return str(status or "").strip().upper()


def _dashboard_status_matches(status: object, requested: str | None) -> bool:
    normalized = _normalize_item_status(status)
    requested_key = str(requested or "").strip().lower()
    if not requested_key:
        return normalized != "DELETED"
    if requested_key in {"any", "history"}:
        return True
    if requested_key == "all":
        return normalized != "DELETED"
    if requested_key == "completed":
        return normalized.startswith("COMPLETED")
    if requested_key == "deleted":
        return normalized == "DELETED"
    return normalized == requested_key.upper()


def _require_dashboard_item_read(item_type: str, item_id: str, read_token: str | None):
    db = _db()
    ok, message = db.validate_item_read_token(read_token or "", item_type, item_id)
    if ok:
        return None
    prefix = "followup" if item_type == "followup" else "reminder"
    return JSONResponse(
        {
            "error": f"{message} Read /api/{prefix}s/{item_id} first and reuse its read_token.",
            "item_type": item_type,
            "item_id": item_id,
        },
        status_code=409,
    )


def _latest_periodic_summary(kind: str) -> dict:
    root = _deep_sleep_dir()
    pattern = f"*-{kind}-summary.json"
    candidates = []
    for path in root.glob(pattern):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        label = str(payload.get("label", "") or "")
        if label:
            candidates.append((label, payload))
    if not candidates:
        return {}
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _summarize_engineering_loop(weekly: dict, monthly: dict) -> dict:
    matters_now = []
    for item in (weekly.get("project_pulse") or weekly.get("top_projects") or [])[:4]:
        matters_now.append(
            {
                "title": str(item.get("project", "") or "unknown"),
                "detail": f"score {item.get('score', 0)}",
                "tone": str(item.get("status", "watch") or "watch"),
                "meta": ", ".join(item.get("reasons", [])[:2]) if isinstance(item.get("reasons"), list) else "",
            }
        )

    drifting = []
    protocol = weekly.get("protocol_summary") or {}
    for key, label in (
        ("guard_check", "guard_check"),
        ("heartbeat", "heartbeat"),
        ("change_log", "change_log"),
    ):
        item = protocol.get(key) or {}
        pct = item.get("compliance_pct")
        if isinstance(pct, (int, float)) and pct < 70:
            drifting.append(
                {
                    "title": label,
                    "detail": f"{pct:.1f}% compliance",
                    "tone": "critical" if pct < 45 else "elevated",
                    "meta": "",
                }
            )
    for item in (weekly.get("top_patterns") or [])[:3]:
        pattern = str(item.get("pattern", "") or "")
        if pattern:
            drifting.append(
                {
                    "title": pattern,
                    "detail": f"{item.get('count', 0)}x this period",
                    "tone": "watch",
                    "meta": "recurring pattern",
                }
            )
        if len(drifting) >= 4:
            break

    improving = []
    trend = weekly.get("trend") or {}
    trust_delta = trend.get("avg_trust_delta")
    if isinstance(trust_delta, (int, float)) and trust_delta > 0:
        improving.append({"title": "Trust", "detail": f"{trust_delta:+.1f}", "tone": "healthy", "meta": "vs previous window"})
    delivery = weekly.get("delivery_metrics") or {}
    if int(delivery.get("engineering_followups", 0) or 0) > 0:
        improving.append(
            {
                "title": "Engineering followups",
                "detail": str(delivery.get("engineering_followups", 0)),
                "tone": "healthy",
                "meta": "guardrails created from recurring patterns",
            }
        )
    protocol_delta = trend.get("protocol_compliance_delta")
    if isinstance(protocol_delta, (int, float)) and protocol_delta > 0:
        improving.append({"title": "Protocol", "detail": f"{protocol_delta:+.1f}%", "tone": "healthy", "meta": "vs previous window"})
    duplicate_followup_delta = trend.get("followup_duplicate_open_delta")
    if isinstance(duplicate_followup_delta, int) and duplicate_followup_delta < 0:
        improving.append({"title": "Followup duplication", "detail": f"{duplicate_followup_delta:+d}", "tone": "healthy", "meta": "open duplicates"})
    learning_noise_delta = trend.get("learning_noise_delta")
    if isinstance(learning_noise_delta, int) and learning_noise_delta < 0:
        improving.append({"title": "Learning noise", "detail": f"{learning_noise_delta:+d}", "tone": "healthy", "meta": "active noise pressure"})
    corrections_delta = trend.get("total_corrections_delta")
    if isinstance(corrections_delta, int) and corrections_delta < 0:
        improving.append({"title": "Corrections", "detail": f"{corrections_delta:+d}", "tone": "healthy", "meta": "lower is better"})
    mood_delta = trend.get("avg_mood_delta")
    if isinstance(mood_delta, (int, float)) and mood_delta > 0:
        improving.append({"title": "Mood", "detail": f"{mood_delta:+.3f}", "tone": "healthy", "meta": "vs previous window"})

    duplicate_followup_rate_delta = trend.get("followup_duplicate_rate_delta")
    if isinstance(duplicate_followup_rate_delta, (int, float)) and duplicate_followup_rate_delta > 0:
        drifting.append({"title": "followup_duplicates", "detail": f"{duplicate_followup_rate_delta:+.1f}%", "tone": "critical" if duplicate_followup_rate_delta >= 5 else "watch", "meta": "open duplicate rate"})
    learning_noise_rate_delta = trend.get("learning_noise_rate_delta")
    if isinstance(learning_noise_rate_delta, (int, float)) and learning_noise_rate_delta > 0:
        drifting.append({"title": "learning_noise", "detail": f"{learning_noise_rate_delta:+.1f}%", "tone": "critical" if learning_noise_rate_delta >= 5 else "watch", "meta": "active noise rate"})

    return {
        "weekly": weekly,
        "monthly": monthly,
        "matters_now": matters_now[:4],
        "drifting": drifting[:4],
        "improving": improving[:4],
    }


def _safe_json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _protocol_explainability_snapshot(limit: int = 20) -> dict:
    db = _db()
    conn = db.get_db()
    max_limit = max(5, min(int(limit or 20), 100))

    protocol_summary = db.protocol_compliance_summary(7)
    recent_tasks = []
    for row in conn.execute(
        """SELECT * FROM protocol_tasks
           ORDER BY opened_at DESC
           LIMIT ?""",
        (max_limit,),
    ).fetchall():
        item = dict(row)
        for field in (
            "files",
            "plan",
            "known_facts",
            "unknowns",
            "constraints",
            "evidence_refs",
            "response_reasons",
        ):
            item[field] = _safe_json(item.get(field), [])
        item["has_evidence"] = bool(str(item.get("close_evidence") or "").strip())
        item["guarded_open"] = bool(item.get("opened_with_guard") or item.get("opened_with_rules"))
        recent_tasks.append(item)

    recent_debts = [dict(row) for row in conn.execute(
        """SELECT * FROM protocol_debt
           ORDER BY created_at DESC
           LIMIT ?""",
        (max_limit,),
    ).fetchall()]

    debt_summary = {"open_total": 0, "by_severity": {}, "by_type": {}}
    for debt in recent_debts:
        if debt.get("status") != "open":
            continue
        debt_summary["open_total"] += 1
        severity = str(debt.get("severity") or "warn")
        debt_type = str(debt.get("debt_type") or "unknown")
        debt_summary["by_severity"][severity] = debt_summary["by_severity"].get(severity, 0) + 1
        debt_summary["by_type"][debt_type] = debt_summary["by_type"].get(debt_type, 0) + 1

    recent_runs = db.list_workflow_runs(include_closed=True, limit=max_limit)
    workflow_summary = {
        "total": len(recent_runs),
        "open_runs": sum(1 for run in recent_runs if run.get("status") not in {"completed", "failed", "cancelled"}),
        "blocked_runs": sum(1 for run in recent_runs if run.get("status") == "blocked"),
        "waiting_approval": sum(1 for run in recent_runs if run.get("status") == "waiting_approval"),
    }

    recent_goals = db.list_workflow_goals(include_closed=True, limit=max_limit)
    goal_summary = {
        "total": len(recent_goals),
        "active": sum(1 for goal in recent_goals if goal.get("status") == "active"),
        "blocked": sum(1 for goal in recent_goals if goal.get("status") == "blocked"),
        "closed": sum(1 for goal in recent_goals if goal.get("status") in {"completed", "cancelled", "abandoned"}),
    }

    guard_checks = [dict(row) for row in conn.execute(
        """SELECT area, files, learnings_returned, blocking_rules_returned, created_at
           FROM guard_checks
           ORDER BY created_at DESC
           LIMIT ?""",
        (max_limit,),
    ).fetchall()]
    areas = {}
    blocking_hits = 0
    for check in guard_checks:
        area = str(check.get("area") or "unknown")
        areas[area] = areas.get(area, 0) + 1
        blocking_hits += int(check.get("blocking_rules_returned") or 0)

    conditioned_learnings = [dict(row) for row in conn.execute(
        """SELECT id, title, applies_to, priority, status, weight, guard_hits, updated_at
           FROM learnings
           WHERE status = 'active' AND applies_to IS NOT NULL AND TRIM(applies_to) != ''
           ORDER BY COALESCE(guard_hits, 0) DESC, updated_at DESC
           LIMIT ?""",
        (max_limit,),
    ).fetchall()]

    return {
        "protocol_summary": protocol_summary,
        "debt_summary": debt_summary,
        "recent_tasks": recent_tasks,
        "recent_debts": recent_debts,
        "workflow_summary": workflow_summary,
        "recent_runs": recent_runs,
        "goal_summary": goal_summary,
        "recent_goals": recent_goals,
        "guard_summary": {
            "recent_checks": len(guard_checks),
            "blocking_hits": blocking_hits,
            "areas": areas,
        },
        "guard_checks": guard_checks,
        "conditioned_learnings": conditioned_learnings,
    }


# ---------------------------------------------------------------------------
# HTML page routes — Jinja2 with fallback to plain file
# ---------------------------------------------------------------------------

def _render(name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template with context."""
    try:
        tmpl = jinja_env.get_template(name)
        return HTMLResponse(tmpl.render(**ctx))
    except Exception as exc:
        import logging
        logging.getLogger("dashboard").error("Template render failed for %s: %s", name, exc)
        path = TEMPLATES_DIR / name
        if path.exists():
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse(f"<h1>Template not found: {name}</h1>", status_code=200)

# Overview
@app.get("/", response_class=HTMLResponse)
async def page_dashboard():
    return _render("dashboard.html")

@app.get("/feed", response_class=HTMLResponse)
async def page_feed():
    return _render("feed.html")

# Operations
@app.get("/crons", response_class=HTMLResponse)
async def page_crons():
    return _render("crons.html")

@app.get("/ops", response_class=HTMLResponse)
async def page_ops():
    return _render("operations.html")

@app.get("/calendar", response_class=HTMLResponse)
async def page_calendar():
    return _render("calendar.html")

# Intelligence
@app.get("/chat", response_class=HTMLResponse)
async def page_chat():
    return _render("chat.html")

@app.get("/memory", response_class=HTMLResponse)
async def page_memory():
    return _render("memory.html")

@app.get("/dreams", response_class=HTMLResponse)
async def page_dreams():
    return _render("dreams.html")

@app.get("/skills", response_class=HTMLResponse)
async def page_skills():
    return _render("skills.html")

@app.get("/trust", response_class=HTMLResponse)
async def page_trust():
    return _render("trust.html")

# Security
@app.get("/guard", response_class=HTMLResponse)
async def page_guard():
    return _render("guard.html")

@app.get("/protocol", response_class=HTMLResponse)
async def page_protocol():
    return _render("protocol.html", snapshot=_protocol_explainability_snapshot())

@app.get("/cortex", response_class=HTMLResponse)
async def page_cortex():
    return _render("cortex.html")

@app.get("/rules", response_class=HTMLResponse)
async def page_rules():
    return _render("rules.html")

@app.get("/plugins", response_class=HTMLResponse)
async def page_plugins():
    return _render("plugins.html")

# Advanced
@app.get("/evolution", response_class=HTMLResponse)
async def page_evolution():
    return _render("evolution.html")

@app.get("/claims", response_class=HTMLResponse)
async def page_claims():
    return _render("claims.html")

@app.get("/sentiment", response_class=HTMLResponse)
async def page_sentiment():
    return _render("sentiment.html")

@app.get("/sessions", response_class=HTMLResponse)
async def page_sessions():
    return _render("sessions.html")

@app.get("/triggers", response_class=HTMLResponse)
async def page_triggers():
    return _render("triggers.html")

@app.get("/artifacts", response_class=HTMLResponse)
async def page_artifacts():
    return _render("artifacts.html")

# System
@app.get("/inbox", response_class=HTMLResponse)
async def page_inbox():
    return _render("inbox.html")

@app.get("/email", response_class=HTMLResponse)
async def page_email():
    return _render("email.html")

@app.get("/credentials", response_class=HTMLResponse)
async def page_credentials():
    return _render("credentials.html")

@app.get("/backups", response_class=HTMLResponse)
async def page_backups():
    return _render("backups.html")

@app.get("/followup-health", response_class=HTMLResponse)
async def page_followup_health():
    return _render("followup_health.html")

# Knowledge
@app.get("/graph", response_class=HTMLResponse)
async def page_graph():
    return _render("graph.html")

@app.get("/somatic", response_class=HTMLResponse)
async def page_somatic():
    return _render("somatic.html")

@app.get("/adaptive", response_class=HTMLResponse)
async def page_adaptive():
    return _render("adaptive.html")


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


@app.get("/api/project-pulse")
async def api_project_pulse(kind: str = Query("weekly", pattern="^(weekly|monthly)$")):
    """Latest project pressure snapshot from Deep Sleep summaries."""
    summary = _latest_periodic_summary(kind)
    if not summary:
        return JSONResponse({"error": f"No {kind} summary found"}, status_code=404)
    return {
        "kind": kind,
        "label": summary.get("label"),
        "project_pulse": summary.get("project_pulse", []),
        "top_projects": summary.get("top_projects", []),
    }


@app.get("/api/engineering-loop")
async def api_engineering_loop():
    """Dashboard narrative: what matters now, what is drifting, what is improving."""
    weekly = _latest_periodic_summary("weekly")
    monthly = _latest_periodic_summary("monthly")
    if not weekly and not monthly:
        return JSONResponse({"error": "No periodic Deep Sleep summaries found"}, status_code=404)
    return _summarize_engineering_loop(weekly or {}, monthly or {})


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
        "SELECT sid as session_id, task, last_update_epoch, claude_session_id, external_session_id, session_client "
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
    reminders = db.get_reminders("history")
    reminders = [r for r in reminders if _dashboard_status_matches(r.get("status"), status)]
    if category:
        reminders = [r for r in reminders if r.get("category") == category]
    reminders = sorted(reminders, key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    return {"count": len(reminders), "reminders": reminders}


@app.post("/api/reminders")
async def api_reminders_create(body: ReminderCreate):
    """Create a reminder."""
    db = _db()
    conn = db.get_db()
    rid = _next_reminder_id(conn)
    result = db.create_reminder(
        rid,
        body.description,
        date=body.date,
        category=body.category or "general",
    )
    if not result or "error" in result:
        return JSONResponse({"error": result.get("error", "Failed to create reminder")}, status_code=400)
    return {"success": True, "reminder": result}


@app.get("/api/reminders/{rid}")
async def api_reminders_get(rid: str):
    """Get a reminder with history."""
    db = _db()
    row = db.get_reminder(rid, include_history=True)
    if not row:
        return JSONResponse({"error": f"Reminder {rid} not found"}, status_code=404)
    return {"success": True, "reminder": row}


@app.put("/api/reminders/{rid}")
async def api_reminders_update(rid: str, body: ReminderUpdate):
    """Update a reminder."""
    db = _db()
    row = db.get_reminder(rid)
    if not row:
        return JSONResponse({"error": f"Reminder {rid} not found"}, status_code=404)
    read_error = _require_dashboard_item_read("reminder", rid, body.read_token)
    if read_error:
        return read_error
    fields = {}
    if body.description is not None:
        fields["description"] = body.description
    if body.date is not None:
        fields["date"] = body.date
    if body.status is not None:
        fields["status"] = body.status
    if body.category is not None:
        fields["category"] = body.category
    if not fields:
        return {"success": True, "reminder": row}
    result = db.update_reminder(rid, history_actor="dashboard", **fields)
    if not result or "error" in result:
        return JSONResponse({"error": result.get("error", f"Reminder {rid} not found")}, status_code=400)
    return {"success": True, "reminder": result}


@app.delete("/api/reminders/{rid}")
async def api_reminders_delete(rid: str, read_token: str = Query("", description="Read token from GET /api/reminders/{rid}")):
    """Soft-delete a reminder."""
    db = _db()
    row = db.get_reminder(rid)
    if not row:
        return JSONResponse({"error": f"Reminder {rid} not found"}, status_code=404)
    read_error = _require_dashboard_item_read("reminder", rid, read_token)
    if read_error:
        return read_error
    db.add_reminder_note(rid, "Soft-deleted from dashboard.", actor="dashboard")
    db.delete_reminder(rid)
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
    followups = db.get_followups("history")
    followups = [r for r in followups if _dashboard_status_matches(r.get("status"), status)]
    followups = sorted(followups, key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    return {"count": len(followups), "followups": followups}


@app.post("/api/followups")
async def api_followups_create(body: FollowupCreate):
    """Create a followup."""
    db = _db()
    conn = db.get_db()
    fid = _next_followup_id(conn)
    result = db.create_followup(
        fid,
        body.description,
        date=body.date,
        verification=body.verification or "",
        reasoning=body.reasoning or "",
    )
    if not result or "error" in result:
        return JSONResponse({"error": result.get("error", "Failed to create followup")}, status_code=400)
    return {"success": True, "followup": result}


@app.get("/api/followups/{fid}")
async def api_followups_get(fid: str):
    """Get a followup with history."""
    db = _db()
    row = db.get_followup(fid, include_history=True)
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    return {"success": True, "followup": row}


@app.put("/api/followups/{fid}")
async def api_followups_update(fid: str, body: FollowupUpdate):
    """Update a followup."""
    db = _db()
    row = db.get_followup(fid)
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    read_error = _require_dashboard_item_read("followup", fid, body.read_token)
    if read_error:
        return read_error
    fields = {}
    if body.description is not None:
        fields["description"] = body.description
    if body.date is not None:
        fields["date"] = body.date
    if body.status is not None:
        fields["status"] = body.status
    if body.verification is not None:
        fields["verification"] = body.verification
    if body.reasoning is not None:
        fields["reasoning"] = body.reasoning
    if not fields:
        return {"success": True, "followup": row}
    result = db.update_followup(fid, history_actor="dashboard", **fields)
    if not result or "error" in result:
        return JSONResponse({"error": result.get("error", f"Followup {fid} not found")}, status_code=400)
    return {"success": True, "followup": result}


@app.delete("/api/followups/{fid}")
async def api_followups_delete(fid: str, read_token: str = Query("", description="Read token from GET /api/followups/{fid}")):
    """Soft-delete a followup."""
    db = _db()
    row = db.get_followup(fid)
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    read_error = _require_dashboard_item_read("followup", fid, read_token)
    if read_error:
        return read_error
    db.add_followup_note(fid, "Soft-deleted from dashboard.", actor="dashboard")
    db.delete_followup(fid)
    return {"success": True, "deleted_id": fid}


# ---------------------------------------------------------------------------
# Ops: Move and Execute
# ---------------------------------------------------------------------------

@app.post("/api/ops/move")
async def api_ops_move(body: MoveRequest):
    """Move an item between reminders and followups."""
    db = _db()
    conn = db.get_db()

    if body.direction == "to_followup":
        item = db.get_reminder(body.id)
        if not item:
            return JSONResponse({"error": f"Reminder {body.id} not found"}, status_code=404)
        read_error = _require_dashboard_item_read("reminder", body.id, body.read_token)
        if read_error:
            return read_error
        fid = _next_followup_id(conn)
        created = db.create_followup(
            fid,
            item["description"],
            date=item.get("date"),
            reasoning=f"Moved from reminder {body.id} via dashboard.",
        )
        if not created or "error" in created:
            return JSONResponse({"error": created.get("error", "Failed to create followup")}, status_code=400)
        db.add_followup_note(fid, f"Created from reminder {body.id} via dashboard move.", actor="dashboard")
        db.add_reminder_note(body.id, f"Moved to followup {fid} via dashboard.", actor="dashboard")
        db.delete_reminder(body.id)
        return {"success": True, "new_id": fid, "direction": "to_followup"}

    elif body.direction == "to_reminder":
        item = db.get_followup(body.id)
        if not item:
            return JSONResponse({"error": f"Followup {body.id} not found"}, status_code=404)
        read_error = _require_dashboard_item_read("followup", body.id, body.read_token)
        if read_error:
            return read_error
        rid = _next_reminder_id(conn)
        created = db.create_reminder(
            rid,
            item["description"],
            date=item.get("date"),
            category="general",
        )
        if not created or "error" in created:
            return JSONResponse({"error": created.get("error", "Failed to create reminder")}, status_code=400)
        migration_note = f"Created from followup {body.id} via dashboard move."
        extra = []
        if item.get("verification"):
            extra.append(f"Previous verification: {item['verification']}")
        if item.get("reasoning"):
            extra.append(f"Previous reasoning: {item['reasoning']}")
        if extra:
            migration_note += " " + " ".join(extra)
        db.add_reminder_note(rid, migration_note, actor="dashboard")
        db.add_followup_note(body.id, f"Moved to reminder {rid} via dashboard.", actor="dashboard")
        db.delete_followup(body.id)
        return {"success": True, "new_id": rid, "direction": "to_reminder"}

    else:
        return JSONResponse(
            {"error": f"Invalid direction: {body.direction}. Use 'to_followup' or 'to_reminder'"},
            status_code=400,
        )


@app.post("/api/ops/execute/{fid}")
async def api_ops_execute(fid: str):
    """Execute a followup by opening Terminal with the configured NEXO client."""
    db = _db()
    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM followups WHERE id = ? AND status != 'DELETED'",
        (fid,),
    ).fetchone()
    if not row:
        return JSONResponse({"error": f"Followup {fid} not found"}, status_code=404)
    item = dict(row)
    if platform.system() != "Darwin":
        return JSONResponse(
            {"error": "This operation requires macOS (uses osascript to open Terminal)"},
            status_code=501,
        )
    # Security: avoid interpolating user-controlled data into shell commands.
    # Write the followup ID to a temp file and pass a safe, fixed command to osascript.
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="nexo-followup-", delete=False)
    tmp.write(fid)
    tmp.close()
    # The selected terminal client reads the followup ID from the temp file — no shell interpolation of description
    try:
        _, shell_cmd = build_followup_terminal_shell_command(tmp.name)
    except AgentRunnerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Terminal" to do script "{escaped}"'
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
        "SELECT *, 'reminder' as item_type FROM reminders WHERE date LIKE ? AND status != 'DELETED' ORDER BY date ASC",
        (month_prefix,),
    ).fetchall()

    followup_rows = conn.execute(
        "SELECT *, 'followup' as item_type FROM followups WHERE date LIKE ? AND status != 'DELETED' ORDER BY date ASC",
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


# ===========================================================================
# NEW API ENDPOINTS — Dashboard v3.0 modules
# ===========================================================================

# ---------------------------------------------------------------------------
# Activity Feed
# ---------------------------------------------------------------------------

@app.get("/api/feed")
async def api_feed(limit: int = Query(50, ge=1, le=200)):
    """Unified activity stream from multiple sources."""
    db = _db()
    conn = db.get_db()
    events = []

    for row in conn.execute(
        "SELECT 'diary' as type, created_at, summary as content, domain, mental_state "
        "FROM session_diary ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall():
        d = dict(row); d["icon"] = "book"; events.append(d)

    for row in conn.execute(
        "SELECT 'change' as type, created_at, what_changed as content, files, why "
        "FROM change_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall():
        d = dict(row); d["icon"] = "code"; events.append(d)

    for row in conn.execute(
        "SELECT 'cortex' as type, created_at, goal as content, task_type, mode "
        "FROM cortex_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall():
        d = dict(row); d["icon"] = "eye"; events.append(d)

    for row in conn.execute(
        "SELECT 'cron' as type, started_at as created_at, cron_id as content, "
        "exit_code, duration_secs, summary "
        "FROM cron_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall():
        d = dict(row); d["icon"] = "clock"; events.append(d)

    for row in conn.execute(
        "SELECT 'decision' as type, created_at, decision as content, domain, confidence "
        "FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall():
        d = dict(row); d["icon"] = "scale"; events.append(d)

    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return {"count": len(events[:limit]), "events": events[:limit]}


# ---------------------------------------------------------------------------
# Crons Control Center
# ---------------------------------------------------------------------------

@app.get("/api/crons")
async def api_crons(hours: int = Query(24, ge=1, le=168)):
    db = _db()
    conn = db.get_db()
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM cron_runs WHERE started_at >= ? ORDER BY started_at DESC", (cutoff,)
    ).fetchall()
    runs = [dict(r) for r in rows]

    cron_summary = {}
    for r in runs:
        cid = r["cron_id"]
        if cid not in cron_summary:
            cron_summary[cid] = {"total": 0, "success": 0, "fail": 0, "last_run": r["started_at"], "durations": []}
        cron_summary[cid]["total"] += 1
        if r.get("exit_code") == 0:
            cron_summary[cid]["success"] += 1
        else:
            cron_summary[cid]["fail"] += 1
        if r.get("duration_secs"):
            cron_summary[cid]["durations"].append(r["duration_secs"])

    for cid, s in cron_summary.items():
        s["avg_duration"] = round(sum(s["durations"]) / len(s["durations"]), 2) if s["durations"] else 0
        del s["durations"]

    return {"hours": hours, "total_runs": len(runs), "runs": runs[:100], "summary": cron_summary}

@app.get("/api/crons/timeline")
async def api_crons_timeline():
    db = _db()
    conn = db.get_db()
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT cron_id, started_at, exit_code, duration_secs "
        "FROM cron_runs WHERE started_at >= ? ORDER BY started_at", (cutoff,)
    ).fetchall()
    return {"timeline": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# NEXO Chat
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def api_chat(body: ChatMessage):
    msg = body.message.lower().strip()
    db = _db()
    conn = db.get_db()

    if any(w in msg for w in ["anoche", "noche", "last night", "overnight"]):
        rows = conn.execute(
            "SELECT * FROM session_diary WHERE domain LIKE '%sleep%' OR domain LIKE '%night%' "
            "ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
        if not rows:
            rows = conn.execute("SELECT * FROM session_diary ORDER BY created_at DESC LIMIT 3").fetchall()
        return {"answer": "Recent overnight activity:", "data": [dict(r) for r in rows], "query_type": "diary"}

    elif any(w in msg for w in ["watchdog", "salud", "health", "status"]):
        nexo_home = os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))
        wp = Path(nexo_home) / "operations" / "watchdog-status.json"
        if wp.exists():
            return {"answer": "Watchdog status:", "data": json.loads(wp.read_text()), "query_type": "watchdog"}
        return {"answer": "Watchdog not available.", "data": [], "query_type": "watchdog"}

    elif any(w in msg for w in ["skill", "habilidad"]):
        rows = conn.execute(
            "SELECT id, name, level, trust_score, use_count FROM skills ORDER BY trust_score DESC LIMIT 20"
        ).fetchall()
        return {"answer": f"{len(rows)} skills:", "data": [dict(r) for r in rows], "query_type": "skills"}

    elif any(w in msg for w in ["cron", "ejecut", "cycle", "recent cron"]):
        rows = conn.execute(
            "SELECT cron_id, started_at, exit_code, duration_secs, summary "
            "FROM cron_runs ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        return {"answer": "Recent cron runs:", "data": [dict(r) for r in rows], "query_type": "crons"}

    elif any(w in msg for w in ["trust", "confianza"]):
        cog = _cognitive()
        return {"answer": f"Trust: {cog.get_trust_score()}", "data": cog.get_trust_history(days=7), "query_type": "trust"}

    elif any(w in msg for w in ["decision", "decidi"]):
        rows = conn.execute(
            "SELECT domain, decision, confidence, created_at FROM decisions ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return {"answer": "Recent decisions:", "data": [dict(r) for r in rows], "query_type": "decisions"}

    elif any(w in msg for w in ["followup", "pendiente", "overdue", "pending"]):
        rows = conn.execute(
            "SELECT id, description, date, status FROM followups "
            "WHERE status NOT IN ('completed','archived','deleted') ORDER BY date ASC LIMIT 20"
        ).fetchall()
        return {"answer": f"{len(rows)} pending followups:", "data": [dict(r) for r in rows], "query_type": "followups"}

    elif any(w in msg for w in ["learn", "aprend", "error"]):
        rows = conn.execute(
            "SELECT id, category, title, priority, created_at FROM learnings WHERE status='active' ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
        return {"answer": f"{len(rows)} learnings:", "data": [dict(r) for r in rows], "query_type": "learnings"}

    elif any(w in msg for w in ["plugin"]):
        rows = conn.execute("SELECT filename, tools_count, tool_names FROM plugins").fetchall()
        return {"answer": f"{len(rows)} plugins:", "data": [dict(r) for r in rows], "query_type": "plugins"}

    elif any(w in msg for w in ["memoria", "memory", "stm", "ltm"]):
        cog = _cognitive()
        return {"answer": "Cognitive memory:", "data": cog.get_stats(), "query_type": "memory"}

    elif any(w in msg for w in ["resumen", "summary", "semana", "week"]):
        rows = conn.execute(
            "SELECT domain, summary, mental_state, created_at FROM session_diary ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return {"answer": "Recent sessions:", "data": [dict(r) for r in rows], "query_type": "summary"}

    elif any(w in msg for w in ["guard", "riesgo", "risk"]):
        rows = conn.execute(
            "SELECT area, files, learnings_returned, blocking_rules_returned, created_at "
            "FROM guard_checks ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
        return {"answer": "Guard checks:", "data": [dict(r) for r in rows], "query_type": "guard"}

    else:
        rows = conn.execute(
            "SELECT 'learning' as source, title as content, category, created_at FROM learnings "
            "WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT 5",
            (f"%{msg[:50]}%", f"%{msg[:50]}%")
        ).fetchall()
        results = [dict(r) for r in rows]
        diary = conn.execute(
            "SELECT 'diary' as source, summary as content, domain, created_at FROM session_diary "
            "WHERE summary LIKE ? ORDER BY created_at DESC LIMIT 5", (f"%{msg[:50]}%",)
        ).fetchall()
        results.extend([dict(r) for r in diary])
        if results:
            return {"answer": f"{len(results)} results:", "data": results, "query_type": "search"}
        return {"answer": "Try: crons, trust, skills, memory, decisions, last night, followups, learnings, guard, plugins, summary.", "data": [], "query_type": "help"}


# ---------------------------------------------------------------------------
# Memory Flow
# ---------------------------------------------------------------------------

@app.get("/api/memory/flow")
async def api_memory_flow():
    cog_conn = _cognitive_db()
    stm = cog_conn.execute("SELECT COUNT(*) FROM stm_memories").fetchone()[0]
    ltm = cog_conn.execute("SELECT COUNT(*) FROM ltm_memories").fetchone()[0]
    quar = cog_conn.execute("SELECT COUNT(*) FROM quarantine WHERE status='pending'").fetchone()[0]
    promoted = cog_conn.execute("SELECT COUNT(*) FROM stm_memories WHERE promoted_to_ltm=1").fetchone()[0]
    dreamed = cog_conn.execute("SELECT COUNT(*) FROM dreamed_pairs").fetchone()[0]

    stm_recent = [dict(r) for r in cog_conn.execute(
        "SELECT id, content, source_type, domain, strength, created_at, promoted_to_ltm "
        "FROM stm_memories ORDER BY created_at DESC LIMIT 20"
    ).fetchall()]
    ltm_recent = [dict(r) for r in cog_conn.execute(
        "SELECT id, content, source_type, domain, strength, access_count, created_at "
        "FROM ltm_memories ORDER BY created_at DESC LIMIT 20"
    ).fetchall()]
    quarantine = [dict(r) for r in cog_conn.execute(
        "SELECT id, content, source_type, confidence, status, created_at FROM quarantine ORDER BY created_at DESC LIMIT 15"
    ).fetchall()]
    cog_conn.close()

    return {"counts": {"stm": stm, "ltm": ltm, "quarantine": quar, "promoted": promoted, "dreamed": dreamed},
            "stm_recent": stm_recent, "ltm_recent": ltm_recent, "quarantine": quarantine}


@app.get("/api/recent-context")
async def api_recent_context(
    query: str = Query("", description="Optional search query for hot context"),
    hours: int = Query(24, ge=1, le=168, description="How many recent hours to inspect"),
    limit: int = Query(8, ge=1, le=25, description="Max contexts/events to return"),
):
    """Expose recent hot context and event timeline for the last N hours."""
    db = _db()
    bundle = db.build_pre_action_context(query=query, hours=hours, limit=limit)
    return {
        "query": bundle.get("query") or "",
        "hours": bundle.get("hours") or hours,
        "has_matches": bool(bundle.get("has_matches")),
        "counts": {
            "contexts": len(bundle.get("contexts") or []),
            "events": len(bundle.get("events") or []),
            "reminders": len(bundle.get("reminders") or []),
            "followups": len(bundle.get("followups") or []),
        },
        "contexts": bundle.get("contexts") or [],
        "events": bundle.get("events") or [],
        "reminders": bundle.get("reminders") or [],
        "followups": bundle.get("followups") or [],
        "excerpt": db.format_pre_action_context_bundle(bundle, compact=True) if bundle.get("has_matches") else "No recent context.",
    }


# ---------------------------------------------------------------------------
# Dream Journal
# ---------------------------------------------------------------------------

@app.get("/api/dreams")
async def api_dreams(limit: int = Query(20, ge=1, le=100)):
    cog_conn = _cognitive_db()
    pairs = cog_conn.execute(
        "SELECT dp.id, dp.memory_a_id, dp.memory_b_id, dp.insight_id, dp.created_at, "
        "ltm.content as insight_content "
        "FROM dreamed_pairs dp LEFT JOIN ltm_memories ltm ON dp.insight_id = ltm.id "
        "ORDER BY dp.created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    cog_conn.close()

    db = _db()
    conn = db.get_db()
    sleep = [dict(r) for r in conn.execute(
        "SELECT summary, domain, mental_state, created_at FROM session_diary "
        "WHERE domain LIKE '%sleep%' OR domain LIKE '%dream%' OR domain LIKE '%night%' "
        "ORDER BY created_at DESC LIMIT 10"
    ).fetchall()]

    return {"dreams": [dict(r) for r in pairs], "sleep_entries": sleep}


# ---------------------------------------------------------------------------
# Skills Lab
# ---------------------------------------------------------------------------

@app.get("/api/skills")
async def api_skills():
    db = _db()
    conn = db.get_db()
    skills = [dict(r) for r in conn.execute("SELECT * FROM skills ORDER BY trust_score DESC").fetchall()]
    for s in skills:
        for f in ("tags", "trigger_patterns", "source_sessions", "linked_learnings", "steps", "gotchas"):
            if s.get(f) and isinstance(s[f], str):
                try: s[f] = json.loads(s[f])
                except: pass

    usage = [dict(r) for r in conn.execute(
        "SELECT skill_id, success, context, created_at FROM skill_usage ORDER BY created_at DESC LIMIT 30"
    ).fetchall()]

    levels = {}
    for s in skills:
        lvl = s.get("level", "unknown")
        levels[lvl] = levels.get(lvl, 0) + 1

    return {"skills": skills, "usage": usage, "levels": levels, "total": len(skills)}


# ---------------------------------------------------------------------------
# Trust Events
# ---------------------------------------------------------------------------

@app.get("/api/trust/events")
async def api_trust_events(limit: int = Query(50, ge=1, le=200)):
    cog_conn = _cognitive_db()
    rows = cog_conn.execute("SELECT * FROM trust_score ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    cog_conn.close()
    return {"events": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Protocol Explainability
# ---------------------------------------------------------------------------

@app.get("/api/protocol")
async def api_protocol(limit: int = Query(20, ge=5, le=100)):
    return _protocol_explainability_snapshot(limit=limit)


# ---------------------------------------------------------------------------
# Guard Heatmap
# ---------------------------------------------------------------------------

@app.get("/api/guard")
async def api_guard(limit: int = Query(100, ge=1, le=500)):
    db = _db()
    conn = db.get_db()
    checks = [dict(r) for r in conn.execute("SELECT * FROM guard_checks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]

    heatmap = {}
    for c in checks:
        area = c.get("area") or "unknown"
        if area not in heatmap:
            heatmap[area] = {"count": 0, "blocking": 0, "learnings": 0}
        heatmap[area]["count"] += 1
        heatmap[area]["blocking"] += c.get("blocking_rules_returned") or 0
        heatmap[area]["learnings"] += c.get("learnings_returned") or 0

    cog_conn = _cognitive_db()
    markers = [dict(r) for r in cog_conn.execute(
        "SELECT target, target_type, risk_score, incident_count, last_incident "
        "FROM somatic_markers WHERE risk_score > 0 ORDER BY risk_score DESC LIMIT 30"
    ).fetchall()]
    cog_conn.close()

    return {"checks": checks[:50], "heatmap": heatmap, "somatic_markers": markers}


# ---------------------------------------------------------------------------
# Cortex Monitor
# ---------------------------------------------------------------------------

@app.get("/api/cortex")
async def api_cortex(limit: int = Query(50, ge=1, le=200)):
    db = _db()
    conn = db.get_db()
    logs = [dict(r) for r in conn.execute("SELECT * FROM cortex_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]
    decisions = [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]
    return {"cortex_logs": logs, "decisions": decisions}


# ---------------------------------------------------------------------------
# Core Rules
# ---------------------------------------------------------------------------

@app.get("/api/rules")
async def api_rules():
    db = _db()
    conn = db.get_db()
    rules = [dict(r) for r in conn.execute("SELECT * FROM core_rules ORDER BY importance DESC, category").fetchall()]
    categories = {}
    for r in rules:
        cat = r.get("category", "uncategorized")
        categories.setdefault(cat, []).append(r)
    return {"rules": rules, "categories": categories, "total": len(rules), "active": sum(1 for r in rules if r.get("is_active"))}


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

@app.get("/api/plugins")
async def api_plugins():
    db = _db()
    conn = db.get_db()
    plugins = [dict(r) for r in conn.execute("SELECT * FROM plugins").fetchall()]
    return {"plugins": plugins, "total": len(plugins), "total_tools": sum(p.get("tools_count", 0) for p in plugins)}


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------

@app.get("/api/evolution")
async def api_evolution():
    db = _db()
    conn = db.get_db()
    logs = [dict(r) for r in conn.execute("SELECT * FROM evolution_log ORDER BY created_at DESC LIMIT 50").fetchall()]
    metrics = [dict(r) for r in conn.execute("SELECT * FROM evolution_metrics ORDER BY measured_at DESC LIMIT 100").fetchall()]
    dimensions = {}
    for m in metrics:
        dim = m["dimension"]
        if dim not in dimensions:
            dimensions[dim] = {"score": m["score"], "delta": m.get("delta", 0), "measured_at": m["measured_at"]}
    return {"logs": logs, "metrics": metrics, "dimensions": dimensions}


# ---------------------------------------------------------------------------
# Claims Network
# ---------------------------------------------------------------------------

@app.get("/api/claims")
async def api_claims(limit: int = Query(100, ge=1, le=500)):
    cog_conn = _cognitive_db()
    claims = [dict(r) for r in cog_conn.execute(
        "SELECT id, text, source_type, source_id, confidence, verification_status, domain, created_at "
        "FROM claims ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()]
    links = [dict(r) for r in cog_conn.execute(
        "SELECT source_claim_id, target_claim_id, relation, confidence FROM claim_links LIMIT ?", (limit * 2,)
    ).fetchall()]
    cog_conn.close()
    status_counts = {}
    for c in claims:
        s = c.get("verification_status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
    return {"claims": claims, "links": links, "status_counts": status_counts, "total": len(claims)}


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

@app.get("/api/sentiment")
async def api_sentiment(days: int = Query(30, ge=1, le=90)):
    cog_conn = _cognitive_db()
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    logs = [dict(r) for r in cog_conn.execute(
        "SELECT * FROM sentiment_log WHERE created_at >= ? ORDER BY created_at", (cutoff,)
    ).fetchall()]
    cog_conn.close()

    daily = {}
    for l in logs:
        day = l["created_at"][:10] if l.get("created_at") else "unknown"
        if day not in daily:
            daily[day] = {"positive": 0, "negative": 0, "neutral": 0, "urgent": 0, "count": 0, "total_intensity": 0}
        s = l.get("sentiment", "neutral")
        if s in daily[day]: daily[day][s] += 1
        daily[day]["count"] += 1
        daily[day]["total_intensity"] += l.get("intensity", 0.5)
    for d in daily.values():
        d["avg_intensity"] = round(d["total_intensity"] / d["count"], 2) if d["count"] else 0
        del d["total_intensity"]

    return {"logs": logs, "daily": daily}


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

@app.get("/api/triggers")
async def api_triggers():
    cog_conn = _cognitive_db()
    triggers = [dict(r) for r in cog_conn.execute("SELECT * FROM prospective_triggers ORDER BY created_at DESC").fetchall()]
    cog_conn.close()
    return {"triggers": triggers, "total": len(triggers),
            "armed": sum(1 for t in triggers if t.get("status") == "armed"),
            "fired": sum(1 for t in triggers if t.get("status") == "fired")}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

@app.get("/api/artifacts")
async def api_artifacts():
    db = _db()
    conn = db.get_db()
    artifacts = [dict(r) for r in conn.execute("SELECT * FROM artifact_registry ORDER BY last_touched_at DESC").fetchall()]
    for a in artifacts:
        for f in ("aliases", "ports", "paths"):
            if a.get(f) and isinstance(a[f], str):
                try: a[f] = json.loads(a[f])
                except: pass
        if a.get("metadata") and isinstance(a["metadata"], str):
            try: a["metadata"] = json.loads(a["metadata"])
            except: pass
    kinds = {}
    for a in artifacts:
        k = a.get("kind", "unknown")
        kinds[k] = kinds.get(k, 0) + 1
    return {"artifacts": artifacts, "total": len(artifacts), "kinds": kinds}


# ---------------------------------------------------------------------------
# Email Monitor
# ---------------------------------------------------------------------------

@app.get("/api/email")
async def api_email_stats():
    conn = _email_db()
    if not conn:
        return {"error": "Email DB not found", "stats": {}}
    total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    processed = conn.execute("SELECT COUNT(*) FROM emails WHERE status='processed'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM emails WHERE status='pending'").fetchone()[0]
    recent = [dict(r) for r in conn.execute(
        "SELECT message_id, from_addr, from_name, subject, received_at, status FROM emails ORDER BY received_at DESC LIMIT 20"
    ).fetchall()]
    threads = [dict(r) for r in conn.execute(
        "SELECT thread_id, COUNT(*) as count, MAX(received_at) as last_email "
        "FROM emails GROUP BY thread_id ORDER BY last_email DESC LIMIT 15"
    ).fetchall()]
    conn.close()
    return {"stats": {"total": total, "processed": processed, "pending": pending}, "recent": recent, "threads": threads}


# ---------------------------------------------------------------------------
# Credentials (names only)
# ---------------------------------------------------------------------------

@app.get("/api/credentials")
async def api_credentials():
    db = _db()
    conn = db.get_db()
    rows = conn.execute("SELECT service, key, notes, created_at, updated_at FROM credentials ORDER BY service, key").fetchall()
    creds = [dict(r) for r in rows]
    services = {}
    for c in creds:
        services.setdefault(c["service"], []).append({"key": c["key"], "notes": c.get("notes", "")})
    return {"credentials": creds, "services": services, "total": len(creds)}


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

@app.get("/api/backups")
async def api_backups():
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    backup_dir = nexo_home / "backups"
    data_dir = nexo_home / "data"
    backups = []
    if backup_dir.exists():
        for item in sorted(backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            stat = item.stat()
            backups.append({"name": item.name, "size_mb": round(stat.st_size / 1048576, 2) if item.is_file() else None,
                            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(), "is_dir": item.is_dir()})
    db_sizes = {}
    for dbfile in ["nexo.db", "cognitive.db"]:
        p = data_dir / dbfile
        if p.exists():
            db_sizes[dbfile] = round(p.stat().st_size / 1048576, 2)
    return {"backups": backups, "db_sizes": db_sizes}


# ---------------------------------------------------------------------------
# Followup Health
# ---------------------------------------------------------------------------

@app.get("/api/followup-health")
async def api_followup_health():
    db = _db()
    conn = db.get_db()
    all_f = [dict(r) for r in conn.execute("SELECT * FROM followups").fetchall()]
    today = datetime.date.today().isoformat()
    pending = [f for f in all_f if f.get("status") not in ("completed", "archived", "deleted")]
    completed = [f for f in all_f if f.get("status") == "completed"]
    overdue = [f for f in pending if f.get("date") and f["date"] < today]
    rate = round(len(completed) / max(len(all_f), 1) * 100, 1)
    age_buckets = {"0-3d": 0, "4-7d": 0, "8-14d": 0, "15-30d": 0, "30d+": 0}
    for f in overdue:
        if f.get("date"):
            try:
                age = (datetime.date.today() - datetime.date.fromisoformat(f["date"])).days
            except: continue
            if age <= 3: age_buckets["0-3d"] += 1
            elif age <= 7: age_buckets["4-7d"] += 1
            elif age <= 14: age_buckets["8-14d"] += 1
            elif age <= 30: age_buckets["15-30d"] += 1
            else: age_buckets["30d+"] += 1
    return {"total": len(all_f), "pending": len(pending), "completed": len(completed),
            "overdue": len(overdue), "completion_rate": rate, "age_buckets": age_buckets, "overdue_items": overdue[:20]}


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
