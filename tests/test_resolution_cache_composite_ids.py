"""Fail-closed invariant for composite-id evidence refs in the resolution cache.

The pre-answer router's ``evidence_ledger`` adapter renders each evidence entry
with a COMPOSITE ``evidence_id`` (``task:<id>`` / ``workflow:<id>`` /
``diary:<id>`` / ``lifecycle:<id>`` / ``continuity:<id>`` / ``evidence_record:
<event_uid>`` / …), so ``_rows_result`` emits refs shaped
``evidence_ledger:task:PT-E2E``. The ``runtime_db`` adapter reads
``lifecycle_events`` (PK ``event_id``, NO ``id`` column) and emits
``runtime_db:<positional|event_id>``.

Earlier rounds mapped ``evidence_ledger`` → ``memory_events.event_uid`` and
``runtime_db`` → ``lifecycle_events.event_id`` and called it done. But the
composite/positional id never matches that id-column, so ``ref_version``
returned the CONSTANT ``__missing__`` sentinel (a non-None handle that the write
gate happily accepted) → the snapshot froze on a constant → the answer was
served STALE after the backing row changed. This suite proves the structural
INVARIANT: a composite/unresolvable ref either resolves to a REAL row (and a
content change is a MISS) or is refused at write (``untrackable_source``) — it
can NEVER be cached on a constant sentinel.

Isolated /tmp DB via conftest's autouse ``isolated_db`` fixture.
"""

from __future__ import annotations

import db


def _result(refs, rendered="X"):
    return {
        "ok": True,
        "should_inject": True,
        "rendered": rendered,
        "evidence_refs": list(refs),
        "intent": "memory_question",
    }


# ── REPRODUCER 1: evidence_ledger composite task ref must not freeze on sentinel ─

def test_evidence_ledger_composite_task_edit_is_miss():
    """Cache an answer on ``evidence_ledger:task:PT-E2E`` while the protocol task
    is open; close it by a plain UPDATE (no change_log); the cached answer must
    MISS. Against 3c7fb62a this FAILS: the composite id never matched
    memory_events.event_uid, so ref_version froze on ``__missing__`` and the
    open-status answer was served stale."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) "
        "VALUES ('PT-E2E', 'S1', 'do the thing', 'open', strftime('%s','now'))"
    )
    conn.commit()

    ref = "evidence_ledger:task:PT-E2E"
    out = rc.set("rk_el_task", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"composite task ref should resolve to the real row: {out}"
    assert rc.get("rk_el_task") is not None, "fresh hit expected before the close"

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE protocol_tasks SET status='done', closed_at=strftime('%s','now') WHERE task_id='PT-E2E'")
    conn.commit()
    assert db.get_change_watermark() == wm_before, "plain UPDATE must not touch change_log"
    assert rc.get("rk_el_task") is None, "STALE: status=open answer served after the task was closed"


def test_evidence_ledger_composite_workflow_edit_is_miss():
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO workflow_runs (run_id, goal, status, opened_at, updated_at) "
        "VALUES ('WF-E2E', 'ship it', 'running', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()

    ref = "evidence_ledger:workflow:WF-E2E"
    out = rc.set("rk_el_wf", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"composite workflow ref should resolve: {out}"
    assert rc.get("rk_el_wf") is not None

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE workflow_runs SET status='completed', updated_at=strftime('%s','now')+1 WHERE run_id='WF-E2E'")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_el_wf") is None, "STALE: workflow answer served after it advanced"


def test_evidence_ledger_composite_evidence_record_edit_is_miss():
    """``evidence_record:<event_uid>`` is the ONE composite whose backing row IS
    memory_events. It must still resolve to that real row and MISS on change."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO memory_events (event_uid, created_at, source_type, event_type, output_digest) "
        "VALUES ('EV-ABC', strftime('%s','now'), 'evidence_ledger', 'evidence_recorded', 'digest_v1')"
    )
    conn.commit()

    ref = "evidence_ledger:evidence_record:EV-ABC"
    out = rc.set("rk_el_ev", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"evidence_record composite should resolve to memory_events: {out}"
    assert rc.get("rk_el_ev") is not None

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE memory_events SET output_digest='digest_v2' WHERE event_uid='EV-ABC'")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_el_ev") is None, "STALE: evidence_record answer served after the event changed"


def test_evidence_ledger_composite_deletion_is_miss():
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) "
        "VALUES ('PT-DEL', 'S1', 'x', 'open', strftime('%s','now'))"
    )
    conn.commit()
    ref = "evidence_ledger:task:PT-DEL"
    out = rc.set("rk_el_del", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True
    assert rc.get("rk_el_del") is not None
    conn.execute("DELETE FROM protocol_tasks WHERE task_id='PT-DEL'")
    conn.commit()
    assert rc.get("rk_el_del") is None, "STALE: served after the backing task row was deleted"


def test_evidence_ledger_unknown_composite_prefix_is_refused():
    """A composite prefix we do not know how to resolve to a table → None →
    refused. No silent ``__missing__`` constant."""
    import resolution_cache as rc

    ref = "evidence_ledger:made_up_kind:XYZ"
    assert rc.ref_version(ref) is None, "unknown composite kind must be untrackable (None)"
    out = rc.set("rk_el_unknown", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is False
    assert out["reason"] == "untrackable_source"


# ── REPRODUCER 2: runtime_db positional/event_id ref ─────────────────────────

def test_runtime_db_lifecycle_edit_is_miss():
    """``runtime_db`` reads lifecycle_events (PK event_id). Cache an answer on a
    real lifecycle event, then mutate its delivery_status by UPDATE → MISS.
    Against 3c7fb62a the runtime_db ref (positional / wrong id-column) froze on
    ``__missing__`` so the answer was served stale."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO lifecycle_events (event_id, action, conversation_id, delivery_status, retry_count, created_at) "
        "VALUES ('LC-E2E', 'session_end', 'conv-1', 'accepted', 0, strftime('%s','now'))"
    )
    conn.commit()

    ref = "runtime_db:LC-E2E"
    out = rc.set("rk_rt", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"runtime_db ref should resolve to the lifecycle row: {out}"
    assert rc.get("rk_rt") is not None

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE lifecycle_events SET delivery_status='processed', retry_count=1 WHERE event_id='LC-E2E'")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_rt") is None, "STALE: runtime_db answer served after the lifecycle event changed"


def test_runtime_db_positional_ref_is_refused():
    """A positional ``runtime_db:<n>`` (the router falls back to enumerate idx
    because lifecycle_events has no ``id`` column) identifies no row → refused,
    never frozen on a sentinel."""
    import resolution_cache as rc

    ref = "runtime_db:1"
    # No lifecycle row has event_id '1' → unresolvable → None.
    assert rc.ref_version(ref) is None
    out = rc.set("rk_rt_pos", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is False
    assert out["reason"] == "untrackable_source"


# ── THE INVARIANT: every CANONICAL_ROUTER_SOURCES either MISSes on change or is
#    refused (untrackable). None may serve stale. ──────────────────────────────

# For each per-row source: how the router emits its ref, plus an (insert, mutate)
# pair against a real row. ``ref`` is the EXACT shape ``_rows_result`` / the inline
# branches produce for that source. ``insert`` creates a real row; ``mutate``
# changes content WITHOUT writing change_log (so only the per-row snapshot, not
# the watermark, can catch it). After ``mutate`` the cached answer MUST MISS.
_PER_ROW_SOURCES = {
    "protocol_tasks": dict(
        ref="protocol_tasks:PT-INV",
        insert="INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) VALUES ('PT-INV','S','g','open',strftime('%s','now'))",
        mutate="UPDATE protocol_tasks SET status='done' WHERE task_id='PT-INV'",
    ),
    "workflows": dict(
        ref="workflows:WF-INV",
        insert="INSERT INTO workflow_runs (run_id, goal, status, opened_at, updated_at) VALUES ('WF-INV','g','running',strftime('%s','now'),strftime('%s','now'))",
        mutate="UPDATE workflow_runs SET status='completed', updated_at=strftime('%s','now')+1 WHERE run_id='WF-INV'",
    ),
    "change_log": dict(
        ref="change_log:5000",
        insert="INSERT INTO change_log (id, session_id, files, what_changed, why) VALUES (5000,'S','f','orig','w')",
        mutate="UPDATE change_log SET what_changed='EDITED' WHERE id=5000",
    ),
    "diary": dict(
        # The router's _source_diary adapter emits ``diary:<session_diary.id>`` (the
        # numeric PK), NOT ``diary:<session_id>``. The ref+versioner must agree on
        # ``id``. (Earlier this case used ``diary:SESS-INV``/session_id, which only
        # ever matched because the OLD versioner also read session_id — masking the
        # real adapter's id-based ref. See test_diary_row_collision_* below.)
        ref="diary:7777",
        insert="INSERT INTO session_diary (id, session_id, summary, decisions, created_at) VALUES (7777,'SESS-INV','orig','none',strftime('%s','now'))",
        mutate="UPDATE session_diary SET summary='EDITED' WHERE id=7777",
    ),
    "reminders": dict(
        ref="reminders:RM-INV",
        insert="INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) VALUES ('RM-INV','2026-06-15','x','PENDING','general',strftime('%s','now'),strftime('%s','now'))",
        mutate="UPDATE reminders SET status='DONE', updated_at=strftime('%s','now')+1 WHERE id='RM-INV'",
    ),
    "followups": dict(
        ref="followups:NF-INV",
        insert="INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) VALUES ('NF-INV','2026-06-15','x','x','open',datetime('now'),datetime('now'))",
        mutate="UPDATE followups SET status='completed', updated_at=datetime('now','+1 second') WHERE id='NF-INV'",
    ),
    "commitments": dict(
        ref="commitments:CM-INV",
        insert="INSERT INTO commitments (id, created_at, updated_at, statement, status) VALUES ('CM-INV',strftime('%s','now'),strftime('%s','now'),'ship','active')",
        mutate="UPDATE commitments SET status='done', updated_at=strftime('%s','now')+1 WHERE id='CM-INV'",
    ),
    "continuity": dict(
        ref="continuity:9001",
        insert="INSERT INTO continuity_snapshots (id, session_id, conversation_id, client, event_type, updated_at) VALUES (9001,'S','conv','c','open',strftime('%s','now'))",
        mutate="UPDATE continuity_snapshots SET event_type='closed', updated_at=strftime('%s','now')+1 WHERE id=9001",
    ),
    "runtime_db": dict(
        ref="runtime_db:LC-INV",
        insert="INSERT INTO lifecycle_events (event_id, action, conversation_id, delivery_status, retry_count, created_at) VALUES ('LC-INV','session_end','conv',  'accepted', 0, strftime('%s','now'))",
        mutate="UPDATE lifecycle_events SET delivery_status='processed', retry_count=1 WHERE event_id='LC-INV'",
    ),
    "evidence_ledger": dict(
        # The evidence_ledger adapter emits a COMPOSITE inner kind; task→protocol_tasks.
        ref="evidence_ledger:task:PT-EL-INV",
        insert="INSERT INTO protocol_tasks (task_id, session_id, goal, status, opened_at) VALUES ('PT-EL-INV','S','g','open',strftime('%s','now'))",
        mutate="UPDATE protocol_tasks SET status='done' WHERE task_id='PT-EL-INV'",
    ),
}

# Watermark-tracked sources: no per-row snapshot; a change_log write MUST MISS.
_WATERMARK_SOURCES = {
    "filesystem": "filesystem:inline",
    "recent_context": "recent_context:inline",
    "causal_graph": "causal_graph:somefile.py",
    "guard_context": "guard_context:verified_clean",
    "cognitive": "cognitive:inline",
}

# Sources the router emits that have NO freshness handle → set() must REFUSE.
_UNTRACKABLE_SOURCES = {
    "project_atlas": "project_atlas:recambios",
    "system_catalog": "system_catalog:runtime",
    "runtime_docs": "runtime_docs:1",
    "source_grep": "source_grep:1",
    # semantic_layers emits canonical SUB-refs, not 'semantic_layers:<id>'; a
    # literal positional one is unresolvable → refused.
    "semantic_layers": "semantic_layers:1",
    # local_context emits 'local_context:<id>' for a flat query log we don't
    # version per-row at the top level → refused (better MISS than stale).
    "local_context": "local_context:1",
    # memory positional (no nested source:id) is unresolvable → refused.
    "memory": "memory:7",
    "transcripts": "transcripts:somefile",
}


def test_invariant_every_canonical_source_cannot_serve_stale():
    """For EVERY source_name in CANONICAL_ROUTER_SOURCES, prove it can never
    serve stale: either a change to its backing row is a MISS, or the answer is
    refused at write (untrackable). This is the structural guarantee — not a
    case-by-case patch."""
    import resolution_cache as rc
    from pre_answer_runtime import CANONICAL_ROUTER_SOURCES

    conn = db.get_db()
    classified = (
        set(_PER_ROW_SOURCES) | set(_WATERMARK_SOURCES) | set(_UNTRACKABLE_SOURCES)
    )
    # Every canonical source must be accounted for here (drift guard).
    missing = sorted(CANONICAL_ROUTER_SOURCES - classified)
    assert missing == [], f"CANONICAL_ROUTER_SOURCES not classified by the invariant test: {missing}"
    extra = sorted(classified - CANONICAL_ROUTER_SOURCES)
    assert extra == [], f"invariant test classifies non-canonical sources: {extra}"

    # (1) per-row sources: a content change → MISS.
    for name, spec in _PER_ROW_SOURCES.items():
        conn.execute(spec["insert"])
        conn.commit()
        ref = spec["ref"]
        key = f"rk_inv_{name}"
        out = rc.set(key, _result([ref]), ttl_seconds=6 * 3600,
                     intent="memory_question", source_refs=[ref])
        assert out["ok"] is True, f"[{name}] real-row ref must be cacheable: {out}"
        assert rc.get(key) is not None, f"[{name}] fresh hit expected before the edit"
        wm_before = db.get_change_watermark()
        conn.execute(spec["mutate"])
        conn.commit()
        assert db.get_change_watermark() == wm_before, f"[{name}] mutate must not touch change_log"
        assert rc.get(key) is None, f"[{name}] STALE: served after its backing row changed"

    # (2) watermark sources: trackable, and a change_log write → MISS.
    for name, ref in _WATERMARK_SOURCES.items():
        key = f"rk_wm_{name}"
        out = rc.set(key, _result([ref]), ttl_seconds=6 * 3600,
                     intent="memory_question", source_refs=[ref])
        assert out["ok"] is True, f"[{name}] watermark-tracked ref must be cacheable: {out}"
        assert rc.get(key) is not None, f"[{name}] fresh hit expected"
        db.log_change(f"S-{name}", "src/x.py", "edited", "watermark move")
        assert rc.get(key) is None, f"[{name}] STALE: served after a change_log write advanced the watermark"

    # (3) untrackable sources: set() refuses (no stale possible — nothing cached).
    for name, ref in _UNTRACKABLE_SOURCES.items():
        assert rc.ref_version(ref) is None, f"[{name}] must be untrackable (None), got {rc.ref_version(ref)!r}"
        key = f"rk_un_{name}"
        out = rc.set(key, _result([ref]), ttl_seconds=6 * 3600,
                     intent="memory_question", source_refs=[ref])
        assert out["ok"] is False, f"[{name}] untrackable source must be refused at write: {out}"
        assert out["reason"] == "untrackable_source"
        assert rc.get(key) is None


def test_self_check_fail_closed_holds():
    """The module's structural self-check: every versioner names a real
    table+id-column in the live schema, and the write gate refuses every
    sentinel/constant/unresolved version. Catches map drift before it ships."""
    import resolution_cache as rc

    report = rc.self_check_fail_closed()
    assert report["ok"] is True, f"fail-closed self-check failed: {report['problems']}"


def test_ref_version_never_returns_a_constant_sentinel():
    """The root-cause guarantee: ref_version returns a real-row version, a
    watermark token, or None — NEVER a constant ``__missing__`` sentinel. This is
    what kills the whole stale class (a frozen-constant snapshot)."""
    import resolution_cache as rc

    probes = [
        "evidence_ledger:task:GONE", "evidence_ledger:workflow:GONE",
        "evidence_ledger:diary:GONE", "evidence_ledger:lifecycle:GONE",
        "evidence_ledger:continuity:GONE", "evidence_ledger:evidence_record:GONE",
        "evidence_ledger:made_up:GONE", "runtime_db:GONE", "runtime_db:1",
        "protocol_tasks:GONE", "followups:GONE", "change_log:99999",
        "memory:learning:GONE", "diary:GONE", "continuity:99999",
    ]
    for ref in probes:
        v = rc.ref_version(ref)
        assert v is None or rc._MISSING not in str(v), f"{ref} leaked a constant sentinel: {v!r}"
