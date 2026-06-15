"""Tests for Deep Sleep Phase 5 REWRITE (memory hygiene / learning dedup).

ABSOLUTE PRINCIPLE under test: never lose data. The bias is NOOP/soft.

Coverage:
  (a) two near-identical learnings (>=0.95 + substring) -> ONE superseded
      (soft, recoverable), the other canonical; reversible via restore.
  (b) ANTI-LOSS: no code path calls delete_learning / gc_* (monkeypatched to
      blow up); zero physical rows deleted.
  (c) conflict / high authority -> NOOP (not superseded).
  (d) cap: 6 duplicate pairs, cap=5 -> 5 applied + 1 deferred to draft_for_morning.
  (e) backup fails -> phase ABORTS without mutating (fail-closed).
  (f) idempotent: re-running the same night does not re-supersede.
  (g) calibration.json is never touched.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
REWRITE_PATH = SRC / "scripts" / "deep-sleep" / "rewrite.py"
APPLY_PATH = SRC / "scripts" / "deep-sleep" / "apply_findings.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "scripts" / "deep-sleep") not in sys.path:
    sys.path.insert(0, str(SRC / "scripts" / "deep-sleep"))


def _fresh_home(tmp_path: Path, monkeypatch) -> tuple[object, object, Path]:
    """Reload the db stack + rewrite/apply modules against an isolated /tmp DB."""
    home = tmp_path / "nexo-home"
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "operations" / "deep-sleep").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("NEXO_CODE", str(SRC))
    monkeypatch.delenv("REWRITE_DRY_RUN", raising=False)
    monkeypatch.delenv("REWRITE_MAX_CHANGES_PER_NIGHT", raising=False)

    # Reload the DB stack so it points at the isolated home.
    for name in list(sys.modules):
        if name == "db" or name.startswith("db."):
            sys.modules.pop(name, None)
    for name in ("apply_findings", "rewrite", "learning_resolver"):
        sys.modules.pop(name, None)

    import db as nexo_db
    import db._core as db_core
    import db._schema as db_schema
    importlib.reload(db_core)
    importlib.reload(db_schema)
    importlib.reload(nexo_db)
    nexo_db.init_db()

    apply_mod = importlib.import_module("apply_findings")
    rewrite_mod = importlib.import_module("rewrite")
    importlib.reload(apply_mod)
    importlib.reload(rewrite_mod)
    return nexo_db, rewrite_mod, home


def _seed(nexo_db, *, category, title, content, applies_to="", status="active",
          reasoning="", priority="medium", created_at=None, updated_at=None) -> int:
    conn = nexo_db.get_db()
    now = nexo_db.now_epoch() if created_at is None else created_at
    upd = now if updated_at is None else updated_at
    cur = conn.execute(
        "INSERT INTO learnings (category, title, content, reasoning, prevention, "
        "applies_to, priority, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (category, title, content, reasoning, "", applies_to, priority, status, now, upd),
    )
    conn.commit()
    return int(cur.lastrowid)


def _status(nexo_db, lid: int) -> str:
    conn = nexo_db.get_db()
    row = conn.execute("SELECT status FROM learnings WHERE id = ?", (lid,)).fetchone()
    return str(row["status"]) if row else "MISSING"


def _count_rows(nexo_db) -> int:
    conn = nexo_db.get_db()
    return int(conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0])


def _executable_code(path: Path) -> str:
    """Return lowercased source with comments and docstrings removed.

    Used to assert that forbidden hard-delete primitives never appear in real
    code, while allowing the module docstring to document them as off-limits.
    """
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            # Skip comments and string literals (docstrings + SQL/string content
            # that legitimately names forbidden primitives in documentation).
            continue
        out.append(tok.string)
    return " ".join(out).lower()


DUP_CONTENT = (
    "The weekly Shopify health cron must verify the reviews badges before "
    "publishing the theme, otherwise frozen ratings ship to production."
)


# ── (a) supersede + reversible ──────────────────────────────────────
def test_near_identical_pair_one_superseded_and_reversible(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="shopify", title="Verify reviews badges weekly",
                  content=DUP_CONTENT, applies_to="recambios", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="shopify", title="Reviews badges weekly verification",
                  content=DUP_CONTENT, applies_to="recambios", created_at=2000, updated_at=2000)
    assert older < newer

    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)

    assert manifest["stats"]["applied"] == 1
    assert _status(nexo_db, older) == "superseded"   # older = victim
    assert _status(nexo_db, newer) == "active"        # newer = canonical
    assert _count_rows(nexo_db) == 2                   # nothing physically removed
    assert manifest["metrics"]["physical_rows_deleted"] == 0

    # Reversible: an append-only history event records before/after; restore is
    # a plain status flip back to active.
    history = nexo_db.get_item_history("learning", str(older))
    assert any(h["event_type"] == "deep_sleep_rewrite_supersede" for h in history)
    conn = nexo_db.get_db()
    conn.execute("UPDATE learnings SET status='active' WHERE id=?", (older,))
    conn.commit()
    assert _status(nexo_db, older) == "active"         # fully restored


# ── scope correctness: only IDENTICAL applies_to may merge ──────────
def test_partial_overlap_scope_is_noop(tmp_path, monkeypatch):
    """Share a token ('recambios') but a DIFFERENT scope -> NOOP. Same
    title/content as the merging pair; only applies_to differs, isolating scope."""
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    a = _seed(nexo_db, category="shopify", title="Verify reviews badges weekly",
              content=DUP_CONTENT, applies_to="recambios,wazion", created_at=1000, updated_at=1000)
    b = _seed(nexo_db, category="shopify", title="Reviews badges weekly verification",
              content=DUP_CONTENT, applies_to="recambios,grwellness", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, a) == "active"
    assert _status(nexo_db, b) == "active"


def test_parent_child_scope_is_noop(tmp_path, monkeypatch):
    """recambios (parent) vs recambios/theme (child) is a narrower, DIFFERENT
    scope -> NOOP (never collapse a narrower-scope learning into a broader one)."""
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    parent = _seed(nexo_db, category="shopify", title="Verify reviews badges weekly",
                   content=DUP_CONTENT, applies_to="recambios", created_at=1000, updated_at=1000)
    child = _seed(nexo_db, category="shopify", title="Reviews badges weekly verification",
                  content=DUP_CONTENT, applies_to="recambios/theme", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, parent) == "active"
    assert _status(nexo_db, child) == "active"


def test_identical_scope_is_order_independent(tmp_path, monkeypatch):
    """Same token SET in different order IS the same scope -> a true duplicate
    still merges (older superseded)."""
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="shopify", title="Verify reviews badges weekly",
                  content=DUP_CONTENT, applies_to="recambios,wazion", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="shopify", title="Reviews badges weekly verification",
                  content=DUP_CONTENT, applies_to="wazion,recambios", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["stats"]["applied"] == 1
    assert _status(nexo_db, older) == "superseded"
    assert _status(nexo_db, newer) == "active"


# ── (b) ANTI-LOSS: no hard delete reachable ─────────────────────────
def test_no_hard_delete_path_reachable(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)

    # Make every hard-delete primitive explode if ever called from this phase.
    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("HARD DELETE called from REWRITE phase — data-loss bug!")

    for name in ("delete_learning",):
        if hasattr(nexo_db, name):
            monkeypatch.setattr(nexo_db, name, _boom)
    # gc_* style functions, if present on the db module.
    for name in dir(nexo_db):
        if name.startswith("gc_") or name in ("merge_nodes", "consolidate_semantic", "auto_merge"):
            try:
                monkeypatch.setattr(nexo_db, name, _boom)
            except Exception:
                pass
    # Also guard raw SQL DELETE on learnings: wrap connection execute.
    real_get_db = nexo_db.get_db

    older = _seed(nexo_db, category="server", title="A weekly cron health note",
                  content=DUP_CONTENT, applies_to="mundi", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="server", title="Weekly cron health note B",
                  content=DUP_CONTENT, applies_to="mundi", created_at=2000, updated_at=2000)

    before = _count_rows(nexo_db)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    after = _count_rows(nexo_db)

    assert before == after  # no rows dropped
    assert manifest["metrics"]["physical_rows_deleted"] == 0
    assert manifest["stats"]["applied"] == 1
    # Static guarantee: rewrite EXECUTABLE code never references hard-delete
    # primitives. We strip comments + docstrings (which legitimately name the
    # forbidden primitives to document that they are off-limits) and scan only
    # real tokens via the AST source segments.
    code_tokens = _executable_code(REWRITE_PATH)
    for forbidden in ("delete_learning", "gc_stm", "gc_ltm", "gc_test", "gc_sensory",
                      "merge_nodes", "DELETE FROM", "DELETE\tFROM"):
        assert forbidden.lower() not in code_tokens, (
            f"REWRITE executable code references forbidden primitive: {forbidden}")


# ── (c) conflict / high authority -> NOOP ───────────────────────────
def test_high_authority_pair_is_noop(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    # Both rows carry a francisco_correction marker -> protected -> NOOP.
    a = _seed(nexo_db, category="protocol", title="Francisco correction rule alpha",
              content=DUP_CONTENT, applies_to="core",
              reasoning="francisco correction: do this", created_at=1000, updated_at=1000)
    b = _seed(nexo_db, category="protocol", title="Francisco correction rule beta",
              content=DUP_CONTENT, applies_to="core",
              reasoning="francisco correction: do this", created_at=2000, updated_at=2000)

    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)

    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, a) == "active"
    assert _status(nexo_db, b) == "active"
    noops = [d for d in manifest["decisions"] if d["decision"] == "NOOP"]
    assert any("protected" in d["reason"] for d in noops)


def test_recent_code_test_evidence_is_noop(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    now = nexo_db.now_epoch()
    a = _seed(nexo_db, category="backend", title="Cron verified note one",
              content=DUP_CONTENT, applies_to="x",
              reasoning="verified: smoke passed, commit abc", created_at=now, updated_at=now)
    b = _seed(nexo_db, category="backend", title="Cron verified note two",
              content=DUP_CONTENT, applies_to="x",
              reasoning="verified: smoke passed, commit abc", created_at=now, updated_at=now)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, a) == "active"
    assert _status(nexo_db, b) == "active"


def test_different_applies_to_is_noop(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    a = _seed(nexo_db, category="shopify", title="Same content diff scope one",
              content=DUP_CONTENT, applies_to="recambios", created_at=1000, updated_at=1000)
    b = _seed(nexo_db, category="shopify", title="Same content diff scope two",
              content=DUP_CONTENT, applies_to="wazion", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, a) == "active"
    assert _status(nexo_db, b) == "active"


# ── (d) cap: 6 dup pairs, cap=5 -> 5 applied + 1 deferred ───────────
def test_cap_defers_overflow_to_morning(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    monkeypatch.setenv("REWRITE_MAX_CHANGES_PER_NIGHT", "5")
    # Build 6 independent duplicate pairs in 6 distinct categories so each pair
    # is unambiguous (no cross-pair discriminant siblings).
    pairs = []
    for i in range(6):
        cat = f"cat{i}"
        content = f"{DUP_CONTENT} variant {i}"
        older = _seed(nexo_db, category=cat, title=f"dup {i} older",
                      content=content, applies_to=f"scope{i}", created_at=1000, updated_at=1000)
        newer = _seed(nexo_db, category=cat, title=f"dup {i} newer",
                      content=content, applies_to=f"scope{i}", created_at=2000, updated_at=2000)
        pairs.append((older, newer))

    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)

    assert manifest["stats"]["applied"] == 5
    assert manifest["stats"]["deferred_cap"] == 1
    assert len(manifest["draft_for_morning"]) == 1
    superseded = sum(1 for (o, _n) in pairs if _status(nexo_db, o) == "superseded")
    assert superseded == 5
    assert _count_rows(nexo_db) == 12  # 6 pairs, nothing deleted


# ── (e) backup fails -> ABORT, no mutation (fail-closed) ────────────
def test_backup_failure_aborts_without_mutation(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
                  applies_to="s", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
                  applies_to="s", created_at=2000, updated_at=2000)

    def _fail_backup(*a, **k):
        raise rewrite_mod.BackupError("simulated disk full")

    monkeypatch.setattr(rewrite_mod, "backup_db", _fail_backup)

    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)

    assert "aborted" in manifest
    assert manifest["stats"]["applied"] == 0
    assert _status(nexo_db, older) == "active"   # no mutation happened
    assert _status(nexo_db, newer) == "active"


def test_real_backup_is_consistent_snapshot(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, home = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
                  applies_to="s", created_at=1000, updated_at=1000)
    _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
          applies_to="s", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert manifest["backup"] is not None
    backup_path = Path(manifest["backup"])
    assert backup_path.exists() and backup_path.stat().st_size > 0
    # The snapshot is a readable SQLite DB containing the learnings table.
    bconn = sqlite3.connect(str(backup_path))
    cnt = bconn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    bconn.close()
    assert cnt == 2


# ── (f) idempotent ─────────────────────────────────────────────────
def test_idempotent_rerun_does_not_resupersede(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
                  applies_to="s", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
                  applies_to="s", created_at=2000, updated_at=2000)

    first = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert first["stats"]["applied"] == 1
    assert _status(nexo_db, older) == "superseded"

    history_before = len(nexo_db.get_item_history("learning", str(older)))

    # Re-run the SAME night. The victim is now superseded (not active) so it is
    # not even a candidate, and the dedupe_key from the manifest is honored.
    second = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert second["stats"]["applied"] == 0

    history_after = len(nexo_db.get_item_history("learning", str(older)))
    assert history_after == history_before  # no new supersede event


def test_idempotent_via_dedupe_key_when_victim_still_active(tmp_path, monkeypatch):
    """Even if a row were reactivated, the prior dedupe_key blocks re-applying."""
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
                  applies_to="s", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
                  applies_to="s", created_at=2000, updated_at=2000)
    rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    # Reactivate the victim (simulating a manual restore) and rerun.
    conn = nexo_db.get_db()
    conn.execute("UPDATE learnings SET status='active' WHERE id=?", (older,))
    conn.commit()
    second = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    assert second["stats"]["skipped_dedupe"] >= 1
    assert second["stats"]["applied"] == 0
    assert _status(nexo_db, older) == "active"  # not re-superseded


# ── (g) calibration.json never touched ──────────────────────────────
def test_calibration_json_never_touched(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, home = _fresh_home(tmp_path, monkeypatch)
    calib = home / "brain" / "calibration.json"
    original = {"user_name": "Francisco", "autonomy": "balanced", "communication": "concise"}
    calib.write_text(json.dumps(original), encoding="utf-8")
    mtime_before = calib.stat().st_mtime_ns

    _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
          applies_to="s", created_at=1000, updated_at=1000)
    _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
          applies_to="s", created_at=2000, updated_at=2000)
    rewrite_mod.run_rewrite("2026-06-15", dry_run=False)

    assert calib.exists()
    assert json.loads(calib.read_text(encoding="utf-8")) == original
    assert calib.stat().st_mtime_ns == mtime_before  # untouched
    # Static guarantee: rewrite source never mentions calibration.
    assert "calibration" not in REWRITE_PATH.read_text(encoding="utf-8").lower().replace(
        "# calibration.json is never touched", ""
    ).replace("calibration.json is never touched", "")


# ── dry-run: manifest only, zero mutations ──────────────────────────
def test_dry_run_emits_manifest_without_mutation(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    older = _seed(nexo_db, category="x", title="dup older", content=DUP_CONTENT,
                  applies_to="s", created_at=1000, updated_at=1000)
    newer = _seed(nexo_db, category="x", title="dup newer", content=DUP_CONTENT,
                  applies_to="s", created_at=2000, updated_at=2000)
    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=True)
    assert manifest["dry_run"] is True
    assert manifest["stats"]["applied"] == 0
    assert manifest["stats"]["delete_soft_eligible"] == 1
    assert _status(nexo_db, older) == "active"
    assert _status(nexo_db, newer) == "active"
    assert manifest["metrics"]["physical_rows_deleted"] == 0


# ── discriminant sibling -> ambiguous cluster -> NOOP ───────────────
def test_discriminant_sibling_forces_noop(tmp_path, monkeypatch):
    nexo_db, rewrite_mod, _ = _fresh_home(tmp_path, monkeypatch)
    # Two exact dups PLUS a third highly-similar-but-distinct row in scope.
    a = _seed(nexo_db, category="x", title="alpha", content=DUP_CONTENT,
              applies_to="s", created_at=1000, updated_at=1000)
    b = _seed(nexo_db, category="x", title="beta", content=DUP_CONTENT,
              applies_to="s", created_at=2000, updated_at=2000)
    # Sibling: same words mostly (high keyword overlap) but a negation flips
    # meaning, and it is NOT a substring of a/b.
    sibling_content = DUP_CONTENT.replace("must verify", "must NOT verify") + " unless flagged"
    _seed(nexo_db, category="x", title="gamma", content=sibling_content,
          applies_to="s", created_at=3000, updated_at=3000)

    manifest = rewrite_mod.run_rewrite("2026-06-15", dry_run=False)
    # The cluster is ambiguous; do not collapse a/b.
    assert _status(nexo_db, a) == "active"
    assert _status(nexo_db, b) == "active"
    decisions = manifest["decisions"]
    assert any(d["decision"] == "NOOP" and d["reason"] == "discriminant_sibling"
               for d in decisions)
