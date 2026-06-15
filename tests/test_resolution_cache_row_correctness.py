"""PER-SOURCE ROW-CORRECTNESS for the resolution cache.

The fail-closed invariant (``test_resolution_cache_composite_ids.py``) proved a
cached ref either MISSes on change or is refused. This suite proves the STRONGER
property the brief demands: the ref the router actually emits resolves to the
EXACT row it encodes — not a real-but-WRONG row.

The earlier invariant test hand-crafted each ref to match the versioner's
id-column. That hid a class of bug: when ``pre_answer_router._rows_result``
builds the ref from a DIFFERENT column than the versioner reads, ``ref_version``
resolves to another row. With a value collision (one row's free-text id column
== another row's numeric id) it resolves to a REAL but WRONG row, so editing the
row the ref encodes does not move the snapshot → STALE HIT, while editing the
unrelated colliding row wrongly invalidates → over-resolution.

So these tests build refs through the REAL adapter ref-builder (``_rows_result``,
the same code path the live router uses) and, for each cacheable source, assert:

  (1) seed >=2 rows,
  (2) cache an answer whose ref points at row X,
  (3) edit ONLY row X  -> the cached answer MISSes, and
  (4) edit ONLY row Y  -> the cached answer is NOT invalidated (no over-resolve).

The diary-collision test is the proven reproducer: it FAILS against 194cdca3
(versioner read session_id while the adapter emitted id) and PASSES after the
fix (both read id).

Isolated /tmp DB via conftest's autouse ``isolated_db`` fixture.
"""

from __future__ import annotations

import db
import pytest


def _result(refs, rendered="X"):
    return {
        "ok": True,
        "should_inject": True,
        "rendered": rendered,
        "evidence_refs": list(refs),
        "intent": "memory_question",
    }


def _adapter_ref(source, row, fields):
    """The EXACT ref the live router emits for ``row`` — built by the real
    ``_rows_result`` (and its ``_ROUTER_REF_ID_FIELD`` pinning), not hand-crafted."""
    from pre_answer_router import _rows_result

    res = _rows_result(source, [row], fields, 4000)
    assert res.evidence_refs, f"[{source}] adapter produced no ref for row {row!r}"
    return res.evidence_refs[0]


def _fetch(conn, table, id_column, id_value):
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {id_column}=? LIMIT 1", (id_value,)
    ).fetchone()
    assert row is not None, f"seed row {table}.{id_column}={id_value!r} not found"
    return dict(row)


# Per cacheable source: how to seed two distinct real rows X and Y, the adapter
# render-fields, and how to mutate each. ``x_id``/``y_id`` identify the seeded
# rows so we can fetch the real row dict and feed it to the real adapter.
_ROW_CORRECTNESS = {
    "protocol_tasks": dict(
        table="protocol_tasks", id_column="task_id",
        fields=("task_id", "goal", "description", "status"),
        seed_x="INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) VALUES ('PT-X','S','gx','open',strftime('%s','now'))",
        seed_y="INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) VALUES ('PT-Y','S','gy','open',strftime('%s','now'))",
        x_id="PT-X", y_id="PT-Y",
        mutate_x="UPDATE protocol_tasks SET status='done' WHERE task_id='PT-X'",
        mutate_y="UPDATE protocol_tasks SET status='done' WHERE task_id='PT-Y'",
    ),
    "workflows": dict(
        table="workflow_runs", id_column="run_id",
        fields=("run_id", "goal", "next_action", "status"),
        seed_x="INSERT INTO workflow_runs (run_id, goal, status, opened_at, updated_at) VALUES ('WF-X','gx','running',strftime('%s','now'),strftime('%s','now'))",
        seed_y="INSERT INTO workflow_runs (run_id, goal, status, opened_at, updated_at) VALUES ('WF-Y','gy','running',strftime('%s','now'),strftime('%s','now'))",
        x_id="WF-X", y_id="WF-Y",
        mutate_x="UPDATE workflow_runs SET status='completed', updated_at=strftime('%s','now')+1 WHERE run_id='WF-X'",
        mutate_y="UPDATE workflow_runs SET status='completed', updated_at=strftime('%s','now')+1 WHERE run_id='WF-Y'",
    ),
    "change_log": dict(
        table="change_log", id_column="id",
        fields=("id", "files", "what_changed", "why", "created_at"),
        seed_x="INSERT INTO change_log (id, session_id, files, what_changed, why) VALUES (6001,'S','fx','origx','w')",
        seed_y="INSERT INTO change_log (id, session_id, files, what_changed, why) VALUES (6002,'S','fy','origy','w')",
        x_id=6001, y_id=6002,
        mutate_x="UPDATE change_log SET what_changed='EDITEDX' WHERE id=6001",
        mutate_y="UPDATE change_log SET what_changed='EDITEDY' WHERE id=6002",
    ),
    "diary": dict(
        table="session_diary", id_column="id",
        fields=("session_id", "summary", "pending", "created_at"),
        seed_x="INSERT INTO session_diary (id, session_id, summary, decisions, created_at) VALUES (8001,'SESS-X','sx','none',strftime('%s','now'))",
        seed_y="INSERT INTO session_diary (id, session_id, summary, decisions, created_at) VALUES (8002,'SESS-Y','sy','none',strftime('%s','now'))",
        x_id=8001, y_id=8002,
        mutate_x="UPDATE session_diary SET summary='EDITEDX' WHERE id=8001",
        mutate_y="UPDATE session_diary SET summary='EDITEDY' WHERE id=8002",
    ),
    "reminders": dict(
        table="reminders", id_column="id",
        fields=("id", "date", "description", "status"),
        seed_x="INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) VALUES ('RM-X','2026-06-15','x','PENDING','general',strftime('%s','now'),strftime('%s','now'))",
        seed_y="INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) VALUES ('RM-Y','2026-06-15','y','PENDING','general',strftime('%s','now'),strftime('%s','now'))",
        x_id="RM-X", y_id="RM-Y",
        mutate_x="UPDATE reminders SET status='DONE', updated_at=strftime('%s','now')+1 WHERE id='RM-X'",
        mutate_y="UPDATE reminders SET status='DONE', updated_at=strftime('%s','now')+1 WHERE id='RM-Y'",
    ),
    "followups": dict(
        table="followups", id_column="id",
        fields=("id", "date", "description", "status"),
        seed_x="INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) VALUES ('NF-X','2026-06-15','x','x','open',datetime('now'),datetime('now'))",
        seed_y="INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) VALUES ('NF-Y','2026-06-15','y','y','open',datetime('now'),datetime('now'))",
        x_id="NF-X", y_id="NF-Y",
        mutate_x="UPDATE followups SET status='completed', updated_at=datetime('now','+1 second') WHERE id='NF-X'",
        mutate_y="UPDATE followups SET status='completed', updated_at=datetime('now','+1 second') WHERE id='NF-Y'",
    ),
    "commitments": dict(
        table="commitments", id_column="id",
        fields=("id", "status", "deadline", "owner", "statement", "action_ref_type", "action_ref_id", "evidence_ref"),
        seed_x="INSERT INTO commitments (id, created_at, updated_at, statement, status) VALUES ('CM-X',strftime('%s','now'),strftime('%s','now'),'shipx','active')",
        seed_y="INSERT INTO commitments (id, created_at, updated_at, statement, status) VALUES ('CM-Y',strftime('%s','now'),strftime('%s','now'),'shipy','active')",
        x_id="CM-X", y_id="CM-Y",
        mutate_x="UPDATE commitments SET status='done', updated_at=strftime('%s','now')+1 WHERE id='CM-X'",
        mutate_y="UPDATE commitments SET status='done', updated_at=strftime('%s','now')+1 WHERE id='CM-Y'",
    ),
    "continuity": dict(
        table="continuity_snapshots", id_column="id",
        fields=("conversation_id", "session_id", "event_type", "latest_user_text"),
        seed_x="INSERT INTO continuity_snapshots (id, session_id, conversation_id, client, event_type, idempotency_key, updated_at) VALUES (9101,'S','convx','c','open','kx',strftime('%s','now'))",
        seed_y="INSERT INTO continuity_snapshots (id, session_id, conversation_id, client, event_type, idempotency_key, updated_at) VALUES (9102,'S','convy','c','open','ky',strftime('%s','now'))",
        x_id=9101, y_id=9102,
        mutate_x="UPDATE continuity_snapshots SET event_type='closed', updated_at=strftime('%s','now')+1 WHERE id=9101",
        mutate_y="UPDATE continuity_snapshots SET event_type='closed', updated_at=strftime('%s','now')+1 WHERE id=9102",
    ),
    "runtime_db": dict(
        table="lifecycle_events", id_column="event_id",
        fields=("id", "event_type", "client", "created_at"),
        seed_x="INSERT INTO lifecycle_events (event_id, action, conversation_id, delivery_status, retry_count, created_at) VALUES ('LC-X','session_end','convx','accepted',0,strftime('%s','now'))",
        seed_y="INSERT INTO lifecycle_events (event_id, action, conversation_id, delivery_status, retry_count, created_at) VALUES ('LC-Y','session_end','convy','accepted',0,strftime('%s','now'))",
        x_id="LC-X", y_id="LC-Y",
        mutate_x="UPDATE lifecycle_events SET delivery_status='processed', retry_count=1 WHERE event_id='LC-X'",
        mutate_y="UPDATE lifecycle_events SET delivery_status='processed', retry_count=1 WHERE event_id='LC-Y'",
    ),
}


@pytest.mark.parametrize("source", sorted(_ROW_CORRECTNESS))
def test_per_source_ref_resolves_to_the_exact_row(source):
    """Build the ref through the REAL adapter, then prove it tracks row X and
    ONLY row X: editing X is a MISS; editing the unrelated row Y is NOT."""
    import resolution_cache as rc

    spec = _ROW_CORRECTNESS[source]
    conn = db.get_db()
    conn.execute(spec["seed_x"])
    conn.execute(spec["seed_y"])
    conn.commit()

    row_x = _fetch(conn, spec["table"], spec["id_column"], spec["x_id"])
    ref = _adapter_ref(source, row_x, spec["fields"])

    key = f"rk_rowcorrect_{source}"
    out = rc.set(key, _result([ref]), ttl_seconds=900,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"[{source}] adapter ref must resolve to the real row X: {out} (ref={ref})"
    assert rc.get(key) is not None, f"[{source}] fresh hit expected before any edit (ref={ref})"

    # (4 first, so a true over-resolution is caught before X is touched) editing
    # the UNRELATED row Y must NOT invalidate the answer about row X.
    wm = db.get_change_watermark()
    conn.execute(spec["mutate_y"])
    conn.commit()
    assert db.get_change_watermark() == wm, f"[{source}] mutate_y must not touch change_log"
    assert rc.get(key) is not None, (
        f"[{source}] OVER-RESOLUTION: editing the unrelated row Y invalidated the "
        f"cached answer about row X (ref={ref}) — the versioner resolved the ref to "
        f"the wrong row"
    )

    # (3) editing row X — the row the ref encodes — MUST MISS.
    conn.execute(spec["mutate_x"])
    conn.commit()
    assert db.get_change_watermark() == wm, f"[{source}] mutate_x must not touch change_log"
    assert rc.get(key) is None, (
        f"[{source}] STALE: editing the real row X (ref={ref}) did not invalidate the "
        f"cached answer — the versioner is reading a different column/row than the "
        f"adapter emitted"
    )


def test_diary_row_collision_resolves_to_the_real_row():
    """THE reproducer for the row-correctness fix.

    Seed two diary rows so row Y's free-text ``session_id`` EQUALS row X's numeric
    ``id``. The adapter emits ``diary:<X.id>``. Against 194cdca3 the versioner read
    ``session_id`` and resolved ``diary:<X.id>`` to row Y → editing the real row X
    did NOT invalidate (STALE) while editing row Y DID (over-resolve). After the fix
    (versioner reads ``id``) the ref resolves to row X exactly.
    """
    import resolution_cache as rc

    conn = db.get_db()
    # Row X with a known numeric id.
    conn.execute(
        "INSERT INTO session_diary (id, session_id, summary, decisions, created_at) "
        "VALUES (4242,'SESS-REAL-X','summary of X','none',strftime('%s','now'))"
    )
    # Row Y whose session_id COLLIDES with row X's numeric id.
    conn.execute(
        "INSERT INTO session_diary (id, session_id, summary, decisions, created_at) "
        "VALUES (5555,'4242','summary of Y','none',strftime('%s','now'))"
    )
    conn.commit()

    row_x = _fetch(conn, "session_diary", "id", 4242)
    ref = _adapter_ref("diary", row_x, ("session_id", "summary", "pending", "created_at"))
    assert ref == "diary:4242", f"adapter must emit the numeric-id ref, got {ref!r}"

    key = "rk_diary_collision"
    out = rc.set(key, _result([ref]), ttl_seconds=900,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"diary ref must resolve to the real row X: {out}"
    assert rc.get(key) is not None

    # Editing the COLLIDING row Y must NOT invalidate (would prove over-resolution
    # to session_id='4242' = row Y, the 194cdca3 bug).
    wm = db.get_change_watermark()
    conn.execute("UPDATE session_diary SET summary='EDITED Y' WHERE id=5555")
    conn.commit()
    assert db.get_change_watermark() == wm
    assert rc.get(key) is not None, (
        "OVER-RESOLUTION: editing diary row Y (session_id collides with X's id) "
        "invalidated the answer about row X — versioner resolved by session_id"
    )

    # Editing the REAL row X (the one the ref encodes) MUST MISS.
    conn.execute("UPDATE session_diary SET summary='EDITED X' WHERE id=4242")
    conn.commit()
    assert db.get_change_watermark() == wm
    assert rc.get(key) is None, (
        "STALE: editing the real diary row X did not invalidate — versioner read "
        "session_id ('SESS-REAL-X') instead of id (4242), so diary:4242 resolved to "
        "the colliding row Y"
    )


def test_self_check_catches_router_versioner_column_drift():
    """The structural guard: ``self_check_fail_closed`` flags any source whose
    router-pinned ref column != the versioner id-column (guarantee C). This is the
    drift sentinel that makes the row-correctness fix systematic, not case-by-case:
    if anyone re-points the diary/runtime_db versioner (or adds a pinned source)
    to a mismatched column, this fails before it ships."""
    import resolution_cache as rc

    # Healthy: the live map is aligned.
    report = rc.self_check_fail_closed()
    assert report["ok"] is True, f"unexpected drift: {report['problems']}"

    # Inject a deliberate mismatch (diary versioner back to session_id) and prove
    # the self-check catches it.
    original = rc._SOURCE_VERSIONERS["diary"]
    try:
        table, _wrong, cols = original
        rc._SOURCE_VERSIONERS["diary"] = (table, "session_id", cols)
        broken = rc.self_check_fail_closed()
        assert broken["ok"] is False
        assert any("diary" in p and "WRONG row" in p for p in broken["problems"]), broken["problems"]
    finally:
        rc._SOURCE_VERSIONERS["diary"] = original
