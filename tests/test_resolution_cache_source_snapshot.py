"""Per-source-name content-snapshot anti-stale tests for the resolution cache.

The router emits ``{source_name}:{id}`` evidence refs (followups, reminders,
learning, memory, commitments, …). Earlier the cache versioned freshness through
PROXY signals (canonical-namespace classification + change_log watermark) that
did NOT cover these source names, so a row mutated by a plain UPDATE (no
change_log write) was served stale. The fix snapshots the REAL rows by id and
re-reads them on get(); this suite proves a content change in EACH source-name
family is a MISS, and that an out-of-map source name is refused at write.

Isolated /tmp DB via conftest's autouse ``isolated_db`` fixture — never ~/.nexo.
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


def _assert_edit_is_miss(rc, key, ref, mutate_sql, *, params=()):
    """Cache an answer on ``ref``; mutate the row WITHOUT change_log; assert MISS."""
    conn = db.get_db()
    out = rc.set(key, _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"{ref} should be cacheable (trackable): {out}"
    assert rc.get(key) is not None, f"{ref} fresh hit expected before the edit"
    wm_before = db.get_change_watermark()
    conn.execute(mutate_sql, params)
    conn.commit()
    assert db.get_change_watermark() == wm_before, "edit must not touch change_log"
    assert rc.get(key) is None, f"STALE: {ref} served after its row changed"


# ── one test per real source-name family ─────────────────────────────────────

def test_followups_source_name_edit_is_miss():
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-FAM', '2026-06-15', 'verify x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    _assert_edit_is_miss(
        rc, "rk_fam_fu", "followups:NF-FAM",
        "UPDATE followups SET status='completed', updated_at=datetime('now') WHERE id='NF-FAM'",
    )


def test_reminders_source_name_edit_is_miss():
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO reminders (id, date, description, status, category, created_at, updated_at) "
        "VALUES ('RM-FAM', '2026-06-15', 'call X', 'PENDING', 'general', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()
    _assert_edit_is_miss(
        rc, "rk_fam_rm", "reminders:RM-FAM",
        "UPDATE reminders SET status='DONE', updated_at=strftime('%s','now')+1 WHERE id='RM-FAM'",
    )


def test_learning_source_name_edit_is_miss():
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO learnings (id, category, title, content, created_at, updated_at) "
        "VALUES (7777, 'feedback', 't', 'orig', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()
    _assert_edit_is_miss(
        rc, "rk_fam_l", "learning:7777",
        "UPDATE learnings SET content='SUPERSEDED', updated_at=strftime('%s','now')+1 WHERE id=7777",
    )


def test_commitments_source_name_edit_is_miss():
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO commitments (id, created_at, updated_at, statement, status) "
        "VALUES ('CM-FAM', strftime('%s','now'), strftime('%s','now'), 'ship the thing', 'active')"
    )
    conn.commit()
    _assert_edit_is_miss(
        rc, "rk_fam_c", "commitments:CM-FAM",
        "UPDATE commitments SET status='done', updated_at=strftime('%s','now')+1 WHERE id='CM-FAM'",
    )


def test_workflows_source_name_edit_is_miss():
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO workflow_runs (run_id, goal, status, opened_at, updated_at) "
        "VALUES ('WF-FAM', 'do thing', 'running', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()
    _assert_edit_is_miss(
        rc, "rk_fam_wf", "workflows:WF-FAM",
        "UPDATE workflow_runs SET status='done', next_action='', updated_at=strftime('%s','now')+1 WHERE run_id='WF-FAM'",
    )


def test_memory_source_name_edit_is_miss():
    """memory:<source>:<source_id> versions via the unified_search FTS snapshot.

    The router now emits a resolvable ``memory:<source>:<source_id>`` ref (not a
    positional ``memory:<n>``), so editing the backing unified_search row
    invalidates the cached answer."""
    import resolution_cache as rc
    conn = db.get_db()
    # unified_search is the FTS table backing recall(); insert via its writer.
    from db._fts import fts_upsert
    fts_upsert("learning", "9100", "mem title", "mem body v1", category="feedback")
    ref = "memory:learning:9100"
    out = rc.set("rk_fam_mem", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is True, f"memory ref should be trackable: {out}"
    assert rc.get("rk_fam_mem") is not None

    wm_before = db.get_change_watermark()
    fts_upsert("learning", "9100", "mem title", "mem body v2 EDITED", category="feedback")
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_fam_mem") is None, "STALE: memory answer served after unified_search edit"


def test_source_name_row_deletion_is_miss():
    """A versioned source-name row that DISAPPEARS must MISS (missing-marker)."""
    import resolution_cache as rc
    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-DEL', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    out = rc.set("rk_fam_del", _result(["followups:NF-DEL"]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=["followups:NF-DEL"])
    assert out["ok"] is True
    assert rc.get("rk_fam_del") is not None
    wm_before = db.get_change_watermark()
    conn.execute("DELETE FROM followups WHERE id='NF-DEL'")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_fam_del") is None, "STALE: served after the followup row was deleted"


# ── out-of-map source name → refused at write (untrackable_source) ───────────

def test_out_of_map_source_name_is_refused():
    """A source_name neither in _SOURCE_VERSIONERS, nor watermark-tracked, nor a
    resolvable canonical ref → no freshness handle → set() refuses to cache it
    with reason='untrackable_source'. Better an extra MISS than a stale HIT."""
    import resolution_cache as rc

    ref = "totally_unknown_source:42"
    assert rc.ref_version(ref) is None
    assert rc.untrackable_refs([ref]) == [ref]
    out = rc.set("rk_unknown", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is False
    assert out["reason"] == "untrackable_source"
    assert ref in out["untrackable_refs"]
    assert rc.get("rk_unknown") is None


def test_positional_memory_ref_is_refused():
    """A positional ``memory:<n>`` (no nested source:id) identifies no row → it
    must be refused, never cached as if fresh forever."""
    import resolution_cache as rc

    ref = "memory:3"
    assert rc.ref_version(ref) is None
    out = rc.set("rk_mem_pos", _result([ref]), ttl_seconds=6 * 3600,
                 intent="memory_question", source_refs=[ref])
    assert out["ok"] is False
    assert out["reason"] == "untrackable_source"


def test_map_covers_every_router_emitted_ref_prefix():
    """Drift guard under the FAIL-CLOSED invariant.

    The old version asserted a NON-existent id must be "trackable" (non-None) —
    which is exactly how the stale class survived: a missing row returned a
    CONSTANT ``__missing__`` that looked like a handle. The correct invariant is:

      * a per-row source with a NON-existent id resolves to None (untrackable) —
        there is no real row to derive freshness from. ``ref_version`` NEVER
        returns a constant sentinel anymore.
      * a watermark-tracked source resolves to a ``__wm__`` token WITHOUT a row
        (its guard is the global change_log watermark).
      * a flat-file / grep / catalog / atlas source resolves to None.

    Per-row freshness (resolves-when-present, edit→MISS) is proven by the
    per-family tests above and the composite-id suite. Here we lock the
    classification so it cannot silently drift back to constant sentinels.
    """
    import resolution_cache as rc

    # Watermark-tracked prefixes: trackable as a __wm__ token even with NO row.
    watermark = {
        "filesystem": "filesystem:inline",
        "recent_context": "recent_context:inline",
        "kg": "kg:node:1:2",
        "causal_graph": "causal_graph:x",
        "guard_context": "guard_context:verified_clean",
    }
    for prefix, ref in watermark.items():
        v = rc.ref_version(ref)
        assert v is not None and v.startswith("__wm__:"), f"{prefix} must be watermark-tracked, got {v!r}"

    # Per-row prefixes: a NON-existent id is untrackable (None) — never a constant.
    per_row_missing = [
        "protocol_tasks:NOPE", "workflows:NOPE", "change_log:0", "diary:NOPE",
        "memory:learning:NOPE", "reminders:NOPE", "followups:NOPE",
        "runtime_db:NOPE", "commitments:NOPE", "continuity:0",
        "evidence_ledger:task:NOPE", "evidence_ledger:workflow:NOPE",
        "transcript_index:0", "hot_context:NOPE", "memory_event:NOPE",
        "local_asset:NOPE", "learning:NOPE",
    ]
    leaked = [r for r in per_row_missing if rc.ref_version(r) is not None]
    assert leaked == [], f"per-row prefixes returned a non-None handle for a missing row (stale risk): {leaked}"

    # Flat-file / grep / catalog / atlas prefixes: untrackable (None).
    must_refuse = ["project_atlas:x", "system_catalog:runtime", "runtime_docs:1", "source_grep:1"]
    wrongly_trackable = [r for r in must_refuse if rc.ref_version(r) is not None]
    assert wrongly_trackable == [], f"prefixes that should be refused but are trackable: {wrongly_trackable}"

    # No ref_version output may EVER be a constant missing sentinel.
    all_refs = list(watermark.values()) + per_row_missing + must_refuse
    for ref in all_refs:
        v = rc.ref_version(ref)
        assert v is None or rc._MISSING not in v, f"{ref} leaked a constant sentinel: {v!r}"
