"""Adversarial re-verification of the resolution cache after the anti-stale fix.

Single obsession: can ANYTHING stale be served? Each test tries to break the
cache from a different angle and asserts a MISS (or refusal-to-cache).
"""

from __future__ import annotations

import db
import pytest


@pytest.fixture(autouse=True)
def _seed_change_log_row():
    """Seed a REAL ``change_log`` row id=1 so the ``change_log:1`` sample refs
    used as stand-in trackable handles resolve to an actual row.

    Under the fail-closed invariant a non-existent ``change_log:1`` is correctly
    refused (untrackable_source); these adversarial tests only use it as a
    generic trackable handle while attacking TTL/watermark/atomicity, so a stable
    seeded row keeps their intent without relying on the old constant-sentinel
    behavior. AUTOINCREMENT starts the next id at 2 so ``log_change`` still bumps
    the global watermark 1→2."""
    conn = db.get_db()
    conn.execute(
        "INSERT INTO change_log (id, session_id, files, what_changed, why) "
        "VALUES (1, 'S-seed', 'src/seed.py', 'seed change', 'fixture for change_log:1 ref')"
    )
    conn.commit()
    yield


def _result(refs, rendered="X"):
    return {
        "ok": True,
        "should_inject": True,
        "rendered": rendered,
        "evidence_refs": list(refs),
        "intent": "prior_work",
    }


# ── (A) cross-session END-TO-END through run_pre_answer_route ────────────────

def _counting_adapters(calls, rendered_tag="route"):
    def make(name):
        def _inner(request):
            calls.append(name)
            return {
                "source": name,
                "rendered": f"{rendered_tag}:{name}",
                # Watermark-tracked ref: legitimately cacheable under the
                # fail-closed invariant (a synthetic change_log:<n> at a row that
                # does not exist is now correctly refused). These tests attack the
                # cross-session watermark invalidation, not per-row freshness.
                "evidence_refs": ["filesystem:inline"],
                "result_count": 1,
            }
        return _inner
    return {
        name: make(name)
        for name in (
            "commitments", "reminders", "followups", "recent_context",
            "project_atlas", "filesystem", "change_log", "evidence_ledger",
            "protocol_tasks", "workflows", "causal_graph", "memory",
            "transcripts", "semantic_layers",
        )
    }


def test_A_cross_session_change_invalidates_via_full_route():
    """Scenario A-cachea / B-cambia / A-pregunta, end to end through the
    public run_pre_answer_route entrypoint (NOT just the module API).

    Session A asks an identical query (same key, since the key has no sid),
    caches. Session B logs a change in another session. Session A re-asks the
    EXACT same query: it must re-run the adapters (MISS), never serve stale.
    """
    from pre_answer_runtime import run_pre_answer_route

    payload = {
        "query": "que sabes del proyecto recambios end to end?",
        "intent": "modify_existing",  # non-session-scoped: key unscoped, cacheable
        "area": "general",
        "source": "pytest",
        "sid": "SID-A",
    }

    calls_a1 = []
    a1 = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls_a1))
    assert a1.get("cache_hit") is False
    assert a1.get("resolution_cache", {}).get("stored") is True
    assert len(calls_a1) > 0

    # A re-asks immediately: cache hit, no adapters.
    calls_a2 = []
    a2 = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls_a2))
    assert a2.get("cache_hit") is True
    assert calls_a2 == []

    # Session B (different sid) logs a change ANYWHERE.
    db.log_change("SID-B", "src/something_else.py", "B shipped", "cross-session mutation")

    # A re-asks the IDENTICAL query → must MISS and re-run (global watermark moved).
    calls_a3 = []
    a3 = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls_a3))
    assert a3.get("cache_hit") is False, "STALE SERVED across sessions via full route"
    assert len(calls_a3) > 0


def test_A2_session_scoped_cross_session_via_full_route():
    """Same as A but with a session-SCOPED intent (prior_work). Session B's
    change must still invalidate session A's sid-bound entry."""
    from pre_answer_runtime import run_pre_answer_route

    base = {
        "query": "que resolvi yo de recambios?",
        "intent": "prior_work",
        "area": "shopify",
        "source": "pytest",
    }
    pa = dict(base, sid="SID-A")

    calls1 = []
    run_pre_answer_route(pa, source_adapters=_counting_adapters(calls1))
    calls2 = []
    hit = run_pre_answer_route(pa, source_adapters=_counting_adapters(calls2))
    assert hit.get("cache_hit") is True
    assert calls2 == []

    db.log_change("SID-B", "src/x.py", "B changed prior work area", "mutation")

    calls3 = []
    after = run_pre_answer_route(pa, source_adapters=_counting_adapters(calls3))
    assert after.get("cache_hit") is False, "STALE session-scoped serve after cross-session change"
    assert len(calls3) > 0


# ── (B) session-scoped key COLLISION (key has no sid) ────────────────────────

def test_B_session_scoped_key_collision_no_cross_leak():
    """The cache_key does not embed the sid, so two sessions asking the same
    session-scoped question produce the SAME key. B must never read A's row,
    and B's set must not let A read B's private snapshot."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-COLL', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    ref = "followup:NF-COLL"

    # A caches under the shared key with sid SID-A.
    rc.set("rk_coll", _result([ref], "A-private"), ttl_seconds=3600,
           intent="prior_work", sid="SID-A", source_refs=[ref])

    # B (different sid) asking the same key must MISS (no cross-session leak).
    assert rc.get("rk_coll", expected_sid="SID-B") is None
    # Unscoped caller must also MISS a sid-bound row.
    assert rc.get("rk_coll", expected_sid="") is None
    # A still reads its own.
    assert rc.get("rk_coll", expected_sid="SID-A") is not None

    # Now B overwrites the same key with its own snapshot.
    rc.set("rk_coll", _result([ref], "B-private"), ttl_seconds=3600,
           intent="prior_work", sid="SID-B", source_refs=[ref])
    # A must NOT receive B's snapshot (sid changed on the row → A is now a MISS).
    assert rc.get("rk_coll", expected_sid="SID-A") is None
    # B reads its own.
    b = rc.get("rk_coll", expected_sid="SID-B")
    assert b is not None and b["result"]["rendered"] == "B-private"


# ── (C) untrackable kinds: project_atlas / doc / local_asset(missing) ────────

def test_C_untrackable_kinds_refused():
    """Kinds with no freshness handle must be refused at write (never cached)."""
    import resolution_cache as rc

    for ref in ("project_atlas:recambios", "doc:agent-playbook", "commit:abc123",
                "spec:panel", "audit:wave1", "release:0.41.0"):
        out = rc.set(f"rk_u_{ref}", _result([ref]), ttl_seconds=3600,
                     intent="modify_existing", source_refs=[ref])
        assert out["ok"] is False, f"{ref} unexpectedly cached"
        assert out["reason"] == "untrackable_source", f"{ref} wrong refusal reason: {out}"
        assert rc.get(f"rk_u_{ref}") is None


def test_C2_mixed_trackable_and_untrackable_refused():
    """One trackable + one untrackable ref → still refused (the untrackable one
    has no handle, so the whole answer could go stale)."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-MIX', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    refs = ["followup:NF-MIX", "project_atlas:recambios"]
    out = rc.set("rk_mix", _result(refs), ttl_seconds=3600,
                 intent="modify_existing", source_refs=refs)
    assert out["ok"] is False
    assert out["reason"] == "untrackable_source"
    assert "project_atlas:recambios" in out["untrackable_refs"]
    assert rc.get("rk_mix") is None


def test_C3_local_asset_content_change_is_miss():
    """local_asset is now versioned from its row: a re-index (fingerprint/mtime
    change) with NO change_log write must MISS."""
    import resolution_cache as rc

    conn = db.get_db()
    # Minimal local_assets row honoring NOT NULL columns (path/display_path/...).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(local_assets)").fetchall()}
    assert "asset_id" in cols
    conn.execute(
        "INSERT INTO local_assets "
        "(asset_id, root_id, path, display_path, parent_path, created_at_fs, modified_at_fs, deleted_at, "
        " quick_fingerprint, size_bytes, status, first_seen_at, last_seen_at, updated_at) "
        "VALUES ('A1', 'R1', '/x/a1', '/x/a1', '/x', strftime('%s','now'), strftime('%s','now'), 0, "
        "        'fp-v1', 100, 'indexed', strftime('%s','now'), strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()

    out = rc.set("rk_la", _result(["local_asset:A1"]), ttl_seconds=3600,
                 intent="modify_existing", source_refs=["local_asset:A1"])
    assert out["ok"] is True, f"local_asset should be trackable now: {out}"
    assert rc.get("rk_la") is not None

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE local_assets SET quick_fingerprint='fp-v2', size_bytes=200 WHERE asset_id='A1'")
    conn.commit()
    assert db.get_change_watermark() == wm_before  # no change_log write
    assert rc.get("rk_la") is None, "STALE local_asset served after re-index"


def test_C4_preference_content_change_is_miss():
    """preference is now versioned from its row."""
    import resolution_cache as rc

    conn = db.get_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(preferences)").fetchall()}
    assert "key" in cols and "value" in cols
    conn.execute(
        "INSERT INTO preferences (key, value, category, updated_at) "
        "VALUES ('tone', 'concise', 'comms', strftime('%s','now'))"
    )
    conn.commit()

    out = rc.set("rk_pref", _result(["preference:tone"]), ttl_seconds=3600,
                 intent="modify_existing", source_refs=["preference:tone"])
    assert out["ok"] is True
    assert rc.get("rk_pref") is not None

    wm_before = db.get_change_watermark()
    conn.execute("UPDATE preferences SET value='detailed', updated_at=strftime('%s','now')+1 WHERE key='tone'")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_pref") is None, "STALE preference served after edit"


def test_C5_learning_deletion_is_miss():
    """A versioned, db-backed ref that is DELETED must MISS (missing-marker)."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO learnings (id, category, title, content, created_at, updated_at) "
        "VALUES (9001, 'feedback', 't', 'c', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()
    out = rc.set("rk_del", _result(["learning:9001"]), ttl_seconds=3600,
                 intent="modify_existing", source_refs=["learning:9001"])
    assert out["ok"] is True
    assert rc.get("rk_del") is not None

    wm_before = db.get_change_watermark()
    conn.execute("DELETE FROM learnings WHERE id=9001")
    conn.commit()
    assert db.get_change_watermark() == wm_before
    assert rc.get("rk_del") is None, "STALE served after source learning deleted"


# ── (D) TTL edge ─────────────────────────────────────────────────────────────

def test_D_ttl_boundary_is_miss():
    """now >= expires_at is a MISS (>= not >)."""
    import resolution_cache as rc

    out = rc.set("rk_edge", _result(["change_log:1"]), ttl_seconds=600,
                 intent="modify_existing", source_refs=["change_log:1"])
    assert out["ok"] is True
    conn = db.get_db()
    # Set expires_at exactly to now.
    conn.execute("UPDATE resolution_cache SET expires_at=? WHERE cache_key='rk_edge'", (rc._now(),))
    conn.commit()
    assert rc.get("rk_edge") is None


# ── (E) set "a medias" / atomicity ──────────────────────────────────────────

def test_E_non_serializable_result_does_not_persist():
    """A result that cannot be JSON-serialized must NOT leave a half-written row
    that a later get could serve."""
    import resolution_cache as rc

    bad = {"ok": True, "evidence_refs": ["change_log:1"], "blob": object()}
    out = rc.set("rk_bad", bad, ttl_seconds=600, intent="modify_existing",
                 source_refs=["change_log:1"])
    assert out["ok"] is False
    assert out["reason"] == "result_not_serializable"
    conn = db.get_db()
    n = conn.execute("SELECT COUNT(*) FROM resolution_cache WHERE cache_key='rk_bad'").fetchone()[0]
    assert n == 0
    assert rc.get("rk_bad") is None


def test_E2_re_set_resets_fingerprint_and_watermark():
    """An overwrite (ON CONFLICT) must refresh fingerprint+watermark+hit_count so
    a stale fingerprint/watermark from the prior version can't linger."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-RS', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    rc.set("rk_rs", _result(["followup:NF-RS"]), ttl_seconds=3600,
           intent="modify_existing", source_refs=["followup:NF-RS"])
    # advance watermark, then overwrite: the new row must store the NEW watermark.
    db.log_change("S1", "f", "c", "w")
    out2 = rc.set("rk_rs", _result(["followup:NF-RS"], "v2"), ttl_seconds=3600,
                  intent="modify_existing", source_refs=["followup:NF-RS"])
    assert out2["ok"] is True
    assert out2["change_watermark"] == db.get_change_watermark()
    hit = rc.get("rk_rs")
    assert hit is not None and hit["result"]["rendered"] == "v2"
    assert hit["hit_count"] == 1  # reset to 0 on conflict, +1 by this get


# ── (F) prune wiring / unbounded growth ──────────────────────────────────────

def test_F_maybe_prune_fires_from_set():
    """_maybe_prune must actually fire from set() at the real throttle and
    collect expired rows, so the table cannot accumulate dead entries forever.

    We seed many EXPIRED rows, then do _PRUNE_EVERY writes of fresh rows; the
    throttled prune triggered by set() must delete the expired ones."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-FLOOD', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()

    # Seed expired rows directly (these would never be collected without prune).
    for i in range(20):
        conn.execute(
            "INSERT INTO resolution_cache (cache_key, kind, result_json, source_fingerprint, "
            "source_refs_json, change_watermark, status, resolved_at, expires_at) "
            "VALUES (?, 'route', '{}', 'fp', '[]', 0, 'fresh', ?, ?)",
            (f"rk_old_{i}", rc._now() - 100, rc._now() - 50),
        )
    conn.commit()
    expired_before = conn.execute(
        "SELECT COUNT(*) FROM resolution_cache WHERE expires_at <= ?", (rc._now(),)
    ).fetchone()[0]
    assert expired_before == 20

    rc._writes_since_prune = 0
    for i in range(rc._PRUNE_EVERY):  # exactly enough writes to trip the throttle once
        rc.set(f"rk_fresh_{i}", _result(["followup:NF-FLOOD"]), ttl_seconds=3600,
               intent="modify_existing", source_refs=["followup:NF-FLOOD"])

    expired_after = conn.execute(
        "SELECT COUNT(*) FROM resolution_cache WHERE expires_at <= ?", (rc._now(),)
    ).fetchone()[0]
    assert expired_after == 0, f"throttled prune never fired: {expired_after} expired rows remain"


def test_F2_hard_cap_trims_even_fresh_rows():
    """The hard row cap (prune's max_rows parameter path) trims the table even
    when every row is fresh/within-TTL — the true unbounded-growth backstop."""
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-CAP', '2026-06-15', 'x', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    for i in range(30):
        rc.set(f"rk_cap_{i}", _result(["followup:NF-CAP"]), ttl_seconds=3600,
               intent="modify_existing", source_refs=["followup:NF-CAP"])
    assert conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0] == 30
    rc.prune(max_rows=5)
    n = conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0]
    assert n <= 5, f"hard cap did not trim: {n} rows"


# ── (G) router synthetic inline markers ride the global watermark ────────────

def test_G_inline_markers_invalidate_on_watermark():
    """filesystem:inline / recent_context:inline are 'unsupported' namespaces:
    NOT flagged untrackable (so common answers cache), but they carry no
    fingerprint handle, so their ONLY guard is the global watermark. Prove a
    change invalidates an answer backed solely by an inline marker."""
    import resolution_cache as rc

    ref = "filesystem:inline"
    # untrackable gate must NOT flag it (else common fs answers never cache)
    assert rc.untrackable_refs([ref]) == []
    out = rc.set("rk_inline", _result([ref]), ttl_seconds=3600,
                 intent="modify_existing", source_refs=[ref])
    assert out["ok"] is True
    assert rc.get("rk_inline") is not None

    db.log_change("S-fs", "src/app.py", "edited file", "fs change")
    assert rc.get("rk_inline") is None, "STALE inline-marker answer after fs change"


# ── (H) THE REAL STALENESS GAP: router source-name refs ride only the watermark
#       and MCP state mutations (followup_complete, etc.) do NOT move it ────────

def test_H_followup_completion_serves_stale_via_source_name_ref():
    """REPRODUCER. The router's followups adapter emits 'followups:<id>' (the
    SOURCE NAME), not the canonical 'followup:<id>'. 'followups:' resolves to an
    UNSUPPORTED namespace → constant fingerprint, NOT flagged untrackable → it
    caches and relies SOLELY on the global change_log watermark.

    But completing a followup (nexo_followup_complete) updates the followups
    table WITHOUT writing change_log, so the watermark does NOT advance. Result:
    a 'pending' answer is served stale after the followup is done.
    """
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-STALE', '2026-06-15', 'verify recambios', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()

    # This is EXACTLY what _rows_result(source='followups', ...) produces.
    router_ref = "followups:NF-STALE"
    # The gate does NOT refuse it (proving it gets cached):
    assert rc.untrackable_refs([router_ref]) == [], "if this is now flagged, the gap is closed"

    out = rc.set("rk_fu_stale", _result([router_ref], "NF-STALE is PENDING"),
                 ttl_seconds=6 * 3600, intent="memory_question", source_refs=[router_ref])
    assert out["ok"] is True  # cached
    assert rc.get("rk_fu_stale") is not None  # fresh

    # Complete the followup the way nexo_followup_complete does: update the row,
    # NO change_log write.
    wm_before = db.get_change_watermark()
    conn.execute("UPDATE followups SET status='completed', updated_at=datetime('now') WHERE id='NF-STALE'")
    conn.commit()
    assert db.get_change_watermark() == wm_before  # watermark did NOT move

    # The bug: the stale 'PENDING' answer is STILL served.
    hit = rc.get("rk_fu_stale")
    if hit is not None:
        raise AssertionError(
            "STALE SERVE: cached 'NF-STALE is PENDING' answer served after the "
            "followup was completed (source-name ref 'followups:' is unsupported, "
            "fingerprint constant, watermark unmoved). This is the same bug class "
            "fix #2 targeted, but only the canonical 'followup:' prefix was patched "
            "— the router emits 'followups:'."
        )


def test_H2_followup_stale_end_to_end_via_run_pre_answer_route():
    """End-to-end proof through the SHIPPING entrypoint. A followups-backed
    answer is cached; the followup is completed (no change_log write); the same
    query re-asked is STILL served from cache (stale)."""
    from pre_answer_runtime import run_pre_answer_route

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-E2E', '2026-06-15', 'verify recambios deploy', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()

    # An adapter that mirrors the REAL followups source: emits 'followups:<id>'
    # and reflects the live DB status in its rendered text + evidence.
    def followups_adapter(request):
        row = conn.execute("SELECT id, status FROM followups WHERE id='NF-E2E'").fetchone()
        return {
            "source": "followups",
            "rendered": f"NF-E2E status={row['status']}",
            "evidence_refs": [f"followups:{row['id']}"],
            "result_count": 1,
        }

    adapters = {"followups": followups_adapter}

    payload = {
        "query": "que followups tengo pendientes de recambios?",
        "intent": "memory_question",
        "area": "general",
        "source": "pytest",
        "sid": "SID-A",
    }

    first = run_pre_answer_route(payload, source_adapters=adapters)
    # Only assert the gap if it actually cached a followups-backed answer.
    stored = first.get("resolution_cache", {}).get("stored")
    rendered_first = first.get("rendered") or ""
    if not (stored and "status=open" in rendered_first):
        import pytest
        pytest.skip(f"route did not cache a followups-open answer (stored={stored}, rendered={rendered_first!r})")

    # Complete the followup the nexo_followup_complete way (no change_log write).
    wm_before = db.get_change_watermark()
    conn.execute("UPDATE followups SET status='completed', updated_at=datetime('now') WHERE id='NF-E2E'")
    conn.commit()
    assert db.get_change_watermark() == wm_before

    second = run_pre_answer_route(payload, source_adapters=adapters)
    if second.get("cache_hit") is True:
        raise AssertionError(
            "STALE E2E: run_pre_answer_route served the cached 'status=open' answer "
            f"after the followup was completed. rendered={second.get('rendered')!r}"
        )
