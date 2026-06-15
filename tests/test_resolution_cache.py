"""Tests for the working-memory / resolution cache (Ola 2).

Covers the anti-stale rule of gold (Francisco's brief): a HIT is valid only
when ALL hold — (1) now() < expires_at, (2) status=='fresh', (3) source
fingerprint unchanged, (4) global change_watermark unchanged. Any failure is a
MISS that falls through to the normal route (which rewrites and re-caches).

The DB is the isolated temp cognitive/nexo DB from conftest.py (autouse
``isolated_db`` fixture) — never ~/.nexo prod.
"""

from __future__ import annotations

import time

import db
import pytest


@pytest.fixture(autouse=True)
def _seed_change_log_row():
    """Seed a REAL ``change_log`` row id=1 so the ``change_log:1`` sample ref the
    mechanics tests use resolves to an actual row.

    The fail-closed cache only caches refs that resolve to a real row (or a
    watermark token); a non-existent ``change_log:1`` is correctly refused now.
    These TTL/round-trip/invalidate/session-scope tests use ``change_log:1`` only
    as a stand-in trackable handle, not to exercise freshness, so a stable
    seeded row keeps their intent intact without depending on the old
    constant-sentinel bug. AUTOINCREMENT starts the next id at 2, so watermark
    advance tests that ``log_change(...)`` still bump MAX(id) 1→2.
    """
    conn = db.get_db()
    conn.execute(
        "INSERT INTO change_log (id, session_id, files, what_changed, why) "
        "VALUES (1, 'S-seed', 'src/seed.py', 'seed change', 'fixture for change_log:1 ref')"
    )
    conn.commit()
    yield


# ── direct module unit tests ─────────────────────────────────────────────────

def _sample_result(refs=None):
    return {
        "ok": True,
        "should_inject": True,
        "rendered": "Project X status: shipped",
        "evidence_refs": list(refs or ["change_log:1"]),
        "intent": "prior_work",
    }


def test_valid_hit_round_trips():
    import resolution_cache as rc

    res = _sample_result(["change_log:1"])
    out = rc.set("rk1", res, ttl_seconds=600, intent="modify_existing", source_refs=["change_log:1"])
    assert out["ok"] is True

    hit = rc.get("rk1")
    assert hit is not None
    assert hit["valid"] is True
    assert hit["result"]["rendered"] == "Project X status: shipped"
    assert hit["hit_count"] == 1
    # second get bumps the hit counter again
    assert rc.get("rk1")["hit_count"] == 2


def test_expired_by_ttl_is_miss():
    import resolution_cache as rc

    out = rc.set("rk_ttl", _sample_result(), ttl_seconds=1, intent="modify_existing", source_refs=["change_log:1"])
    assert out["ok"] is True
    # Force the entry past its TTL without sleeping: rewind expires_at into the past.
    conn = db.get_db()
    conn.execute("UPDATE resolution_cache SET expires_at = ? WHERE cache_key = ?", (rc._now() - 5, "rk_ttl"))
    conn.commit()

    assert rc.get("rk_ttl") is None
    # And the entry was demoted to 'stale' on the read path.
    status = conn.execute("SELECT status FROM resolution_cache WHERE cache_key=?", ("rk_ttl",)).fetchone()[0]
    assert status == "stale"


def test_source_fingerprint_change_is_miss_even_before_ttl():
    import resolution_cache as rc

    # A real, db-backed, versioned source: a followup row.
    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        ("NF-TEST-1", "2026-06-15", "verify project X", "url live", "open"),
    )
    conn.commit()

    out = rc.set(
        "rk_fp",
        _sample_result(["followup:NF-TEST-1"]),
        ttl_seconds=3600,  # long TTL so only the fingerprint can invalidate
        intent="modify_existing",
        source_refs=["followup:NF-TEST-1"],
    )
    assert out["ok"] is True
    assert rc.get("rk_fp") is not None  # fresh hit while nothing changed

    # Mutate the source DIRECTLY (no change_log write → watermark stays put),
    # so we isolate condition (3) from condition (4).
    watermark_before = db.get_change_watermark()
    conn.execute("UPDATE followups SET status='completed' WHERE id=?", ("NF-TEST-1",))
    conn.commit()
    assert db.get_change_watermark() == watermark_before  # watermark genuinely unchanged

    assert rc.get("rk_fp") is None  # MISS purely due to fingerprint change


def test_watermark_advance_is_miss():
    import resolution_cache as rc

    # Use a trackable, db-backed ref (a followup row) so the trackable-refs gate
    # accepts the entry and we isolate condition (4) — the GLOBAL watermark.
    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-WM', '2026-06-15', 'wm test', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()

    out = rc.set("rk_wm", _sample_result(["followup:NF-WM"]), ttl_seconds=3600, intent="memory_question",
                 source_refs=["followup:NF-WM"])
    assert out["ok"] is True
    assert rc.get("rk_wm") is not None  # fresh

    # Any mutation lands in the change_log ledger → GLOBAL watermark advances.
    # We log it under a DIFFERENT session to prove the watermark is global, not
    # scoped to the entry's session: a change anywhere invalidates everywhere.
    db.log_change("S-other", "src/x.py", "edited x", "fix bug")
    assert db.get_change_watermark() > 0

    assert rc.get("rk_wm") is None  # MISS due to global watermark advance


def test_instant_tier_never_caches():
    import resolution_cache as rc

    out = rc.set("rk_instant", _sample_result(), ttl_seconds=0, intent="general")
    assert out["ok"] is False
    assert out["reason"] == "ttl_zero_never_cache"
    assert rc.get("rk_instant") is None


def test_session_scoped_intent_requires_sid_and_does_not_leak():
    import resolution_cache as rc

    # Refuse to cache a session-scoped intent without a sid (would leak globally).
    refused = rc.set("rk_noleak", _sample_result(), ttl_seconds=600, intent="prior_work", sid="")
    assert refused["ok"] is False
    assert refused["reason"] == "session_scoped_without_sid"

    # With a sid it caches, but only the owning session can read it.
    ok = rc.set("rk_sid", _sample_result(), ttl_seconds=600, intent="prior_work", sid="SID-A",
                source_refs=["change_log:1"])
    assert ok["ok"] is True
    assert rc.get("rk_sid", expected_sid="SID-A") is not None
    assert rc.get("rk_sid", expected_sid="SID-B") is None  # cross-session MISS
    assert rc.get("rk_sid", expected_sid="") is None       # unscoped caller MISS


def test_no_evidence_answer_is_not_cached_by_set_guard():
    # set() itself caches anything with a key+ttl; the guard that refuses to
    # cache an empty answer lives in the fast-path. Here we assert set() does
    # persist (it's the caller's job to gate), proving the two layers are
    # distinct and the gate is testable separately.
    import resolution_cache as rc

    out = rc.set("rk_empty", {"ok": True, "should_inject": False, "evidence_refs": []},
                 ttl_seconds=600, intent="memory_question", source_refs=[])
    assert out["ok"] is True


def test_repo_map_invalidates_on_git_hash_change(monkeypatch):
    import resolution_cache as rc

    fake_head = {"value": "aaaa111"}
    monkeypatch.setattr(rc, "_git_head", lambda repo_dir: fake_head["value"])

    stored = rc.set_repo_map(
        "nexo-desktop",
        {"tree": ["src/", "app/"], "key_files": ["main.js"], "gotchas": ["dual manifests"]},
        repo_dir="/fake/nexo-desktop",
        ttl_seconds=86400,
    )
    assert stored["ok"] is True

    # Same HEAD → served from cache, no rebuild needed.
    hit = rc.get_repo_map("nexo-desktop", repo_dir="/fake/nexo-desktop")
    assert hit is not None
    assert hit["result"]["git_head"] == "aaaa111"
    assert "dual manifests" in hit["result"]["gotchas"]

    # Repo moved (new commit) → MISS, must rebuild.
    fake_head["value"] = "bbbb222"
    assert rc.get_repo_map("nexo-desktop", repo_dir="/fake/nexo-desktop") is None


def test_invalidate_and_prune():
    import resolution_cache as rc

    rc.set("rk_a", _sample_result(), ttl_seconds=600, intent="modify_existing", source_refs=["change_log:1"])
    rc.set("rk_b", _sample_result(), ttl_seconds=600, intent="modify_existing", source_refs=["change_log:1"])

    assert rc.invalidate("rk_a") == 1
    assert rc.get("rk_a") is None  # stale → miss
    assert rc.get("rk_b") is not None

    # Prune deletes by elapsed TTL.
    conn = db.get_db()
    conn.execute("UPDATE resolution_cache SET expires_at = ? WHERE cache_key='rk_b'", (rc._now() - 1,))
    conn.commit()
    assert rc.prune() >= 1
    assert conn.execute("SELECT COUNT(*) FROM resolution_cache WHERE cache_key='rk_b'").fetchone()[0] == 0


def test_cross_session_change_invalidates_session_scoped_cache():
    """A change in ANOTHER session must invalidate THIS session's cache.

    This is bug #1 (the root cross-session staleness). Session A caches a
    session-scoped ``prior_work`` answer (sid-bound key). Session B then logs a
    change — which advances the GLOBAL change watermark but NOT session A's
    sid-scoped watermark. Under the NEXO identity model ("if another terminal
    did X, I did X") session A re-asking MUST be a MISS. Before the fix
    ``is_valid`` compared against the entry's sid-scoped watermark and served
    the stale snapshot; now it compares against the global watermark.

    Uses a trackable, db-backed ref that B's change does NOT touch, so the only
    thing that can invalidate is condition (4) — the global watermark — proving
    the watermark (not the fingerprint) is what catches the cross-session change.
    """
    import resolution_cache as rc

    conn = db.get_db()
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-XSESSION', '2026-06-15', 'A snapshot source', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    ref = "followup:NF-XSESSION"

    # Session A caches under its own sid.
    out = rc.set(
        "rk_xsession",
        _sample_result([ref]),
        ttl_seconds=6 * 3600,  # working-memory window; only a change can invalidate
        intent="prior_work",
        sid="SID-A",
        source_refs=[ref],
    )
    assert out["ok"] is True
    # A reads its own entry → fresh hit while nothing changed anywhere.
    assert rc.get("rk_xsession", expected_sid="SID-A") is not None

    # Session B logs a change. Global watermark advances; SID-A's does not.
    wm_global_before = db.get_change_watermark()
    wm_sid_a_before = db.get_change_watermark("SID-A")
    db.log_change("SID-B", "src/other_project.py", "B shipped a change", "cross-session mutation")
    wm_global_after = db.get_change_watermark()
    wm_sid_a_after = db.get_change_watermark("SID-A")

    # Numeric proof of the asymmetry the bug exploited:
    assert wm_global_after > wm_global_before          # global advanced
    assert wm_sid_a_after == wm_sid_a_before            # sid-A watermark did NOT

    # The followup A cached on was NOT touched, so the fingerprint is stable;
    # the ONLY thing that can invalidate is the global watermark.
    assert rc.source_fingerprint([ref], conn=conn) == out["source_fingerprint"]

    # Therefore: session A re-asking MUST be a MISS (no stale cross-session serve).
    assert rc.get("rk_xsession", expected_sid="SID-A") is None


def test_non_db_backed_kind_change_is_not_served_stale():
    """Bug #2: a content change in a kind the fingerprint cannot version, and
    whose store does not write change_log, must never be served stale.

    Two halves:
      * ``project_atlas`` resolves to a CONSTANT validator digest and its JSON
        store is not in change_log → the cache REFUSES to store it
        (``untrackable_refs``), so it can never go stale.
      * ``learning`` is now versioned from its DB row, so a superseded learning
        changes the fingerprint → a stored entry MISSes after the change even
        though no change_log row was written (watermark untouched).
    """
    import resolution_cache as rc

    # Half 1: project_atlas → un-cacheable (no freshness handle at all).
    refused = rc.set(
        "rk_atlas",
        _sample_result(["project_atlas:recambios"]),
        ttl_seconds=3600,
        intent="modify_existing",
        source_refs=["project_atlas:recambios"],
    )
    assert refused["ok"] is False
    assert refused["reason"] == "untrackable_source"
    assert "project_atlas:recambios" in refused["untrackable_refs"]
    assert rc.get("rk_atlas") is None  # nothing was cached

    # Half 2: learning is now trackable; a content change invalidates via the
    # fingerprint WITHOUT any change_log write (watermark stays put).
    conn = db.get_db()
    conn.execute(
        "INSERT INTO learnings (id, category, title, content, created_at, updated_at) "
        "VALUES (4242, 'feedback', 'orig title', 'orig content', strftime('%s','now'), strftime('%s','now'))"
    )
    conn.commit()

    out = rc.set(
        "rk_learning",
        _sample_result(["learning:4242"]),
        ttl_seconds=3600,
        intent="modify_existing",
        source_refs=["learning:4242"],
    )
    assert out["ok"] is True
    assert rc.get("rk_learning") is not None  # fresh while unchanged

    wm_before = db.get_change_watermark()
    # Mutate the learning content directly (a supersede/edit). No change_log write.
    conn.execute(
        "UPDATE learnings SET content='SUPERSEDED content', updated_at=strftime('%s','now')+1 WHERE id=4242"
    )
    conn.commit()
    assert db.get_change_watermark() == wm_before  # watermark genuinely unchanged

    # Fingerprint changed → MISS purely from condition (3), proving the learning
    # kind is no longer a blind spot.
    assert rc.get("rk_learning") is None


def test_prune_reduces_expired_rows_and_caps_table():
    """prune() collects expired rows and enforces a hard row cap."""
    import resolution_cache as rc

    conn = db.get_db()
    # Seed several entries with a trackable ref.
    conn.execute(
        "INSERT INTO followups (id, date, description, verification, status, created_at, updated_at) "
        "VALUES ('NF-PRUNE', '2026-06-15', 'p', 'x', 'open', datetime('now'), datetime('now'))"
    )
    conn.commit()
    for i in range(6):
        rc.set(f"rk_p{i}", _sample_result(["followup:NF-PRUNE"]), ttl_seconds=600,
               intent="modify_existing", source_refs=["followup:NF-PRUNE"])

    # Expire half of them in the past.
    conn.execute("UPDATE resolution_cache SET expires_at = ? WHERE cache_key IN ('rk_p0','rk_p1','rk_p2')",
                 (rc._now() - 10,))
    conn.commit()
    total_before = conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0]
    deleted = rc.prune()
    total_after = conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0]
    assert deleted >= 3
    assert total_after == total_before - 3
    assert conn.execute(
        "SELECT COUNT(*) FROM resolution_cache WHERE cache_key IN ('rk_p0','rk_p1','rk_p2')"
    ).fetchone()[0] == 0

    # Hard cap backstop: with a tiny max_rows the table is trimmed even though
    # the remaining rows are still fresh (within TTL).
    remaining = conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0]
    assert remaining >= 2
    rc.prune(max_rows=1)
    assert conn.execute("SELECT COUNT(*) FROM resolution_cache").fetchone()[0] <= 1


# ── integration: fast-path inside run_pre_answer_route ────────────────────────

def _counting_adapters(calls):
    def make(name):
        def _inner(request):
            calls.append(name)
            # Emit a WATERMARK-TRACKED ref (``filesystem:inline``) so the answer is
            # legitimately cacheable under the fail-closed invariant: these tests
            # exercise the fast-path HIT and the watermark-advance MISS, not
            # per-row freshness. A synthetic ``change_log:<n>`` pointing at a row
            # that does not exist is now correctly refused (untrackable_source),
            # so it cannot be used as a stand-in handle anymore.
            return {"source": name, "rendered": name, "evidence_refs": ["filesystem:inline"], "result_count": 1}
        return _inner
    # cover the standard-tier source set so the route produces evidence
    return {
        name: make(name)
        for name in (
            "commitments", "reminders", "followups", "recent_context",
            "project_atlas", "filesystem", "change_log", "evidence_ledger",
            "protocol_tasks", "workflows", "causal_graph", "memory",
            "transcripts", "semantic_layers",
        )
    }


def test_fast_path_hit_skips_route_execution():
    from pre_answer_runtime import run_pre_answer_route

    payload = {
        "query": "que sabes del proyecto recambios?",
        "intent": "modify_existing",
        "area": "shopify",
        "source": "pytest",
    }

    calls_first = []
    first = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls_first))
    assert first.get("cache_hit") is False
    assert first.get("resolution_cache", {}).get("stored") is True
    assert len(calls_first) > 0  # the route actually ran adapters

    calls_second = []
    second = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls_second))
    assert second.get("cache_hit") is True
    assert second.get("resolution_cache", {}).get("hit") is True
    assert calls_second == []  # NO adapters ran — served from working memory
    assert second.get("rendered") == first.get("rendered")


def test_fast_path_instant_intent_is_never_cached():
    from pre_answer_runtime import run_pre_answer_route

    payload = {"query": "hola", "intent": "general", "source": "pytest"}
    calls = []
    result = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls))
    # instant tier: cache_ttl=0 → no resolution_cache stored block
    assert result.get("cache_hit") is False
    assert result.get("resolution_cache") is None


def test_fast_path_invalidates_after_a_change_lands():
    from pre_answer_runtime import run_pre_answer_route

    payload = {
        "query": "estado del deploy de wazion?",
        "intent": "modify_existing",
        "area": "server",
        "source": "pytest",
    }
    # area='server' → critical tier (ttl 30s) still caches; use a neutral area
    payload["area"] = "general"

    calls1 = []
    run_pre_answer_route(payload, source_adapters=_counting_adapters(calls1))

    calls2 = []
    second = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls2))
    assert second.get("cache_hit") is True
    assert calls2 == []

    # A mutation lands → watermark advances → next ask is a MISS, route reruns.
    db.log_change("S-test", "src/wazion.py", "deployed", "release")
    calls3 = []
    third = run_pre_answer_route(payload, source_adapters=_counting_adapters(calls3))
    assert third.get("cache_hit") is False
    assert len(calls3) > 0


def test_working_memory_ttl_is_15_minutes(monkeypatch):
    """Defense in depth: the working-memory TTL is 15 minutes (900s), NOT 6h.

    The per-row snapshot is the primary anti-stale guard; the short TTL bounds
    the worst-case obsolescence to minutes for anything that ever slips past
    resolution, without losing the same-conversation repeat-question win.
    """
    from pre_answer_runtime import select_budget_policy, _DEFAULT_WORKING_MEMORY_TTL

    assert _DEFAULT_WORKING_MEMORY_TTL == 900, "working-memory default must be 15 minutes"

    # Default: prior_work extends to the 900s working-memory window (not 6h).
    monkeypatch.delenv("NEXO_RESOLUTION_CACHE_PRIOR_WORK_TTL", raising=False)
    pol = select_budget_policy(query="que resolvi de recambios?", intent="prior_work", area="shopify")
    assert pol.cache_ttl_seconds == 900, "prior_work must cap at 15 minutes, not 6h"
    assert pol.cache_ttl_seconds != 6 * 3600
    assert "working_memory_ttl_extended" in pol.reason_codes

    # live_state_claim now also rides the working-memory window.
    pol_live = select_budget_policy(query="esta el server arriba?", intent="live_state_claim", area="shopify")
    assert pol_live.budget_tier == "deep"
    assert pol_live.cache_ttl_seconds == 900
    assert "working_memory_ttl_extended" in pol_live.reason_codes

    # Configurable via env.
    monkeypatch.setenv("NEXO_RESOLUTION_CACHE_PRIOR_WORK_TTL", "1800")  # 30 min
    pol30 = select_budget_policy(query="que resolvi de recambios?", intent="prior_work", area="shopify")
    assert pol30.cache_ttl_seconds == 1800

    # Env=0 disables the override → falls back to the tier's short base TTL.
    monkeypatch.setenv("NEXO_RESOLUTION_CACHE_PRIOR_WORK_TTL", "0")
    pol0 = select_budget_policy(query="que resolvi de recambios?", intent="prior_work", area="shopify")
    assert pol0.cache_ttl_seconds < 900  # tier base (deep=90s), no extension
    assert "working_memory_ttl_extended" not in pol0.reason_codes
    monkeypatch.delenv("NEXO_RESOLUTION_CACHE_PRIOR_WORK_TTL", raising=False)

    # NOT applied to critical-boundary areas (release/server/billing/legal).
    pol_crit = select_budget_policy(query="liberamos?", intent="prior_work", area="release")
    assert pol_crit.budget_tier == "critical"
    assert pol_crit.cache_ttl_seconds == 30  # short window preserved

    # NOT applied to instant (general) — never caches.
    pol_instant = select_budget_policy(query="hola", intent="general")
    assert pol_instant.cache_ttl_seconds == 0


def test_fast_path_bypass_cache_flag():
    from pre_answer_runtime import run_pre_answer_route

    payload = {"query": "algo cacheable", "intent": "modify_existing", "area": "x", "source": "pytest"}
    run_pre_answer_route(payload, source_adapters=_counting_adapters([]))

    calls = []
    bypass = dict(payload)
    bypass["no_cache"] = True
    result = run_pre_answer_route(bypass, source_adapters=_counting_adapters(calls))
    # bypass forces a real run even though a fresh entry exists
    assert result.get("cache_hit") is False
    assert len(calls) > 0
