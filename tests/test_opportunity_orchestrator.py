import json
import sqlite3
import time

import db
import opportunity_orchestrator as oo
from db._schema import _m80_opportunity_orchestrator, run_migrations


FORBIDDEN = {
    "anxious",
    "depressed",
    "vulnerable",
    "unstable",
    "burnout",
    "manipulable",
    "compliance_likely",
    "frustrated",
    "mood",
    "tension",
}


def _insert_open_protocol_task(conn, task_id: str, title: str, *, status: str = "open") -> None:
    conn.execute(
        """
        INSERT INTO protocol_tasks (
            task_id, session_id, goal, task_type, status, opened_at, verification_step
        ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        (task_id, "SID-test", title, "edit", status, "pytest evidence"),
    )


def test_opportunity_migration_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    _m80_opportunity_orchestrator(conn)
    _m80_opportunity_orchestrator(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "nexo_signals",
        "nexo_opportunities",
        "nexo_opportunity_evidence",
        "nexo_preparations",
        "nexo_proposals",
        "nexo_proposal_events",
        "nexo_suppression_rules",
        "nexo_action_authorizations",
    } <= tables


def test_opportunity_m80_upgrades_existing_install():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    for version in range(1, 80):
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, f"already_applied_{version}"),
        )
    conn.commit()

    run_migrations(conn)

    assert conn.execute("SELECT version FROM schema_migrations WHERE version = 80").fetchone()
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nexo_opportunities'").fetchone()


def test_refresh_from_closure_persists_evidence_and_preparations():
    conn = db.get_db()
    now = time.time()
    _insert_open_protocol_task(conn, "PT-opportunity", "Ship release with evidence")
    conn.execute(
        """
        INSERT INTO followups (id, date, description, verification, status, created_at, updated_at)
        VALUES (?, date('now'), ?, ?, ?, ?, ?)
        """,
        ("FUP-opportunity", "Check release smoke", "smoke evidence", "PENDING", now, now),
    )
    conn.execute(
        """
        INSERT INTO outcomes (
            action_type, action_id, session_id, description, expected_result, status, deadline
        ) VALUES (?, ?, ?, ?, ?, ?, date('now'))
        """,
        ("release", "REL-opportunity", "SID-test", "Release outcome", "tests pass", "pending"),
    )

    result = oo.refresh_opportunities(conn, dry_run=False)
    queue = oo.opportunity_queue(conn, surface="home", limit=10)

    assert result["ok"] is True
    assert result["persisted"]["opportunities"] >= 3
    assert result["persisted"]["evidence"] >= 3
    assert len(queue["proposals"]) <= 3
    assert queue["proposals"]
    for proposal in queue["proposals"]:
        assert proposal["confidence"] > 0
        assert proposal["evidence_refs"]
        assert proposal["opportunity"]["why_now"]
        assert proposal["opportunity"]["preparations"]


def test_zero_proposals_is_valid_for_empty_or_weak_queue():
    conn = db.get_db()
    weak = {
        "id": "OPP-weak",
        "title": "Weak candidate",
        "hypothesis": "",
        "domain": "general",
        "opportunity_type": "closure",
        "dedupe_key": "weak:1",
        "impact": 0.1,
        "urgency": 0.1,
        "confidence": 0.1,
        "risk": 0.4,
        "effort": 0.5,
        "readiness": 0.1,
        "user_burden_reduction": 0.1,
        "interruption_cost": 0.5,
        "strategic_alignment": 0.1,
        "repetition_penalty": 0.0,
        "score": 0.0,
        "state": "candidate",
        "owner": "nero",
        "why_now": "not enough evidence",
        "next_action": "watch",
        "action_class": "read_only",
        "authorization_status": "not_required",
        "created_at": "2026-06-07T00:00:00Z",
        "updated_at": "2026-06-07T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "last_proposed_at": "",
        "source_payload_json": "{}",
    }
    oo._upsert_opportunity(conn, weak)
    conn.commit()

    queue = oo.opportunity_queue(conn, surface="home", limit=3)

    assert queue["ok"] is True
    assert queue["proposals"] == []
    assert queue["zero_proposals_ok"] is True


def test_queue_caps_normal_output_at_three_proposals():
    conn = db.get_db()
    for index in range(10):
        _insert_open_protocol_task(conn, f"PT-many-{index}", f"High value release item {index}")

    oo.refresh_opportunities(conn, dry_run=False)
    queue = oo.opportunity_queue(conn, surface="morning_briefing", limit=10)

    assert len(queue["proposals"]) == 3
    assert queue["proposal_limit"] == 3


def test_feedback_records_event_and_suppresses_repeated_noise():
    conn = db.get_db()
    _insert_open_protocol_task(conn, "PT-feedback", "Noisy release item")
    oo.refresh_opportunities(conn, dry_run=False)
    row = conn.execute(
        """
        SELECT *
        FROM nexo_opportunities
        WHERE title LIKE '%Noisy release item%'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    opportunity = dict(row)
    evidence = oo._opportunity_evidence(conn, opportunity["id"])
    proposal = oo._create_or_update_proposal(conn, opportunity, "home", evidence)
    proposal_id = proposal["id"]

    feedback = oo.opportunity_feedback(proposal_id, "false_positive", note="not useful", conn=conn)
    next_queue = oo.opportunity_queue(conn, surface="home", limit=3)
    next_opportunity_ids = {
        proposal["opportunity"]["id"]
        for proposal in next_queue["proposals"]
        if proposal.get("opportunity")
    }

    assert feedback["ok"] is True
    assert feedback["suppression"]["ok"] is True
    assert opportunity["id"] not in next_opportunity_ids
    assert conn.execute(
        "SELECT COUNT(*) FROM nexo_proposal_events WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()[0] == 1


def test_visible_payload_redacts_clinical_or_profile_language():
    conn = db.get_db()
    _insert_open_protocol_task(
        conn,
        "PT-sensitive",
        "User seems anxious, depressed, vulnerable, unstable, frustrated, mood TENSION",
    )
    oo.refresh_opportunities(conn, dry_run=False)
    queue = oo.opportunity_queue(conn, surface="home", limit=3)
    rendered = json.dumps(queue, ensure_ascii=False).lower()

    for term in FORBIDDEN:
        assert term not in rendered


def test_external_action_classes_require_permission():
    assert oo._authorization_status("read_only") == "not_required"
    assert oo._authorization_status("prepare_artifact") == "not_required"
    for action_class in ("send", "deploy", "buy", "delete", "production_change", "payment", "public_publish"):
        assert oo._authorization_status(action_class) == "needs_permission"


def test_server_exports_opportunity_tools():
    import asyncio
    import server

    async def get_tool(name):
        return await server.mcp.get_tool(name)

    for tool_name in (
        "nexo_opportunity_refresh",
        "nexo_opportunity_queue",
        "nexo_opportunity_get",
        "nexo_opportunity_feedback",
        "nexo_opportunity_suppress",
    ):
        tool = asyncio.run(get_tool(tool_name))
        assert tool is not None
        assert getattr(tool, "output_schema", "missing") is None
