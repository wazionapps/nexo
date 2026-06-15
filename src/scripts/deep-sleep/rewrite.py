#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 5: REWRITE (memory hygiene, deterministic, no-LLM).

MVP SCOPE (intentionally tiny): dedup near-identical *learnings* via a
REVERSIBLE supersede. Nothing else. No decay, no re-link, no observations, no
hard delete. The bias is NOOP/soft: it is always better not to merge than to
merge wrong or lose data.

ABSOLUTE PRINCIPLE — never lose data:
  - The ONLY mutation this phase performs is ``supersede_learning`` (soft:
    sets status='superseded', the row is preserved and restorable).
  - It NEVER calls delete_learning, gc_*, merge_nodes, or any drop-row path.
  - Every applied decision writes an append-only, reversible item_history event
    (before_state, after_state, primitive, dedupe_key) so it can be restored.

DECISION per candidate pair — explicit ADD / UPDATE / DELETE(soft) / NOOP:
  - NOOP   (default): any doubt, conflict, high authority, recent evidence,
                      pinned, or already-idempotent -> no mutation.
  - DELETE (soft):    the older near-duplicate is superseded by the canonical
                      (newer) one. "Delete" == invalidate, never drop a row.
  (ADD / UPDATE are reserved for later phases; the MVP only emits DELETE/NOOP.)

ULTRA-SAFE THRESHOLD (all must hold, else NOOP):
  - similarity >= 0.95 (resolver math, candidate_similarity)
  - one normalized content/title is a substring of the other (exact-ish dup)
  - identical applies_to scope
  - no discriminant sibling (a third active learning that is also similar but
    NOT a substring -> ambiguous cluster -> NOOP the whole cluster)
  - both rows are dedup-safe (not high authority, not recently verified by
    code/test evidence, not pinned)

SAFETY RAILS:
  - REWRITE_MAX_CHANGES_PER_NIGHT (default 5): beyond the cap, the remaining
    pairs go to draft_for_morning (human review), nothing is applied.
  - Idempotent: reuses dedupe_key checked against the last 7 days of applied
    manifests; re-running the same night does not re-supersede.
  - dry_run mode emits the manifest only, with zero mutations (recommended
    operating default for the first week).
  - calibration.json is NEVER touched by this phase.

Environment variables:
  NEXO_HOME                       -- root of the NEXO installation (~/.nexo)
  REWRITE_MAX_CHANGES_PER_NIGHT   -- daily cap (default 5)
  REWRITE_DRY_RUN                 -- "1" to emit manifest only, no mutations
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import db as nexo_db
from learning_resolver import (
    AUTHORITY_RANKS,
    candidate_similarity,
)

# Reuse the same path resolution and the (now fail-closed) backup primitive
# from the apply phase. No parallel infra.
import apply_findings  # noqa: E402  (sys.path set above)
from apply_findings import (  # noqa: E402
    NEXO_DB,
    DEEP_SLEEP_DIR,
    BackupError,
    backup_db,
    generate_run_id,
    load_recent_dedupe_keys,
    _normalize_text,
)

# ── Tunables ────────────────────────────────────────────────────────
DEFAULT_MAX_CHANGES = 5
SIMILARITY_FLOOR = 0.95            # ultra-safe; below this -> never a candidate
SIBLING_SIMILARITY_FLOOR = 0.85    # a third row this similar makes the cluster ambiguous
RECENT_EVIDENCE_WINDOW_DAYS = 7    # do not rewrite learnings touched in this window

# Authority floor: anything at or above code_test_evidence is off-limits for
# automatic supersede by Deep Sleep (mirrors learning_resolver's contract).
PROTECTED_AUTHORITY_RANK = AUTHORITY_RANKS["code_test_evidence"]  # 60


def _max_changes() -> int:
    raw = str(os.environ.get("REWRITE_MAX_CHANGES_PER_NIGHT", "")).strip()
    if raw.isdigit():
        return max(0, int(raw))
    return DEFAULT_MAX_CHANGES


def _is_dry_run(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return str(os.environ.get("REWRITE_DRY_RUN", "")).strip() in ("1", "true", "yes", "on")


def _now() -> float:
    return time.time()


def _content_hash(row: dict) -> str:
    body = f"{row.get('title','')}|{row.get('content','')}|{row.get('applies_to','')}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _row_authority_high(row: dict) -> bool:
    """True if a row is protected by authority and must never be auto-superseded.

    Mirrors learning_resolver._row_authority_rank heuristics: any francisco /
    correction marker, or critical priority, is treated as high authority.
    """
    text = " ".join(
        str(row.get(k) or "")
        for k in ("title", "content", "reasoning", "prevention")
    ).lower()
    if "francisco" in text or "correction" in text or "correccion" in text:
        return True
    if "explicit_instruction" in text or "explicit instruction" in text:
        return True
    priority = str(row.get("priority") or "").strip().lower()
    if priority == "critical":
        return True
    # An explicit authority column, if present.
    authority = str(row.get("authority") or row.get("source_authority") or "").strip().lower()
    if authority in ("francisco_correction", "explicit_instruction", "code_test_evidence"):
        return True
    return False


def _row_recently_verified(row: dict, *, window_days: int = RECENT_EVIDENCE_WINDOW_DAYS) -> bool:
    """True if the row carries recent code/test evidence (do not rewrite)."""
    text = " ".join(
        str(row.get(k) or "")
        for k in ("reasoning", "prevention", "content")
    ).lower()
    has_evidence = any(
        marker in text
        for marker in ("code_test_evidence", "test evidence", "verified", "passed", "smoke", "commit ")
    )
    if not has_evidence:
        return False
    updated = row.get("updated_at") or row.get("created_at")
    try:
        updated_ts = float(updated)
    except (TypeError, ValueError):
        return True  # has evidence but unparseable timestamp -> be conservative
    return (_now() - updated_ts) <= window_days * 86400


def _row_pinned(row: dict) -> bool:
    state = str(row.get("lifecycle_state") or row.get("status") or "").strip().lower()
    if state == "pinned":
        return True
    pinned = row.get("pinned")
    return bool(pinned) and str(pinned).strip() not in ("0", "", "false", "none")


def _row_dedup_safe(row: dict) -> tuple[bool, str]:
    """Return (safe, reason). A row is dedup-safe only if nothing protects it."""
    if _row_pinned(row):
        return False, "pinned"
    if _row_authority_high(row):
        return False, "high_authority"
    if _row_recently_verified(row):
        return False, "recent_code_test_evidence"
    return True, ""


def _fetch_active_learnings(conn: sqlite3.Connection) -> list[dict]:
    columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(learnings)").fetchall()}
    if not columns:
        return []
    status_filter = " WHERE COALESCE(status, 'active') = 'active'" if "status" in columns else ""
    order_by = "updated_at DESC, id DESC" if "updated_at" in columns else "id DESC"
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM learnings{status_filter} ORDER BY {order_by} LIMIT 2000"
    ).fetchall()
    return [dict(r) for r in rows]


def _substring_dup(a: dict, b: dict) -> bool:
    """True if one normalized title/content is a substring of the other.

    Requires a meaningful (non-trivial) shared body so two tiny rows do not
    accidentally collide.
    """
    a_title, a_content = _normalize_text(a.get("title", "")), _normalize_text(a.get("content", ""))
    b_title, b_content = _normalize_text(b.get("title", "")), _normalize_text(b.get("content", ""))
    if a_title and a_title == b_title:
        return True
    for x, y in ((a_content, b_content), (a_content, b_title), (a_title, b_content)):
        if not x or not y:
            continue
        if len(x) < 12 and len(y) < 12:
            continue  # too short to assert duplication safely
        if x in y or y in x:
            return True
    return False


def _scope_tokens(scope: str) -> frozenset:
    """Normalized, order-independent set of comma-separated applies_to tokens."""
    return frozenset(
        tok for tok in (_normalize_text(part) for part in scope.split(",")) if tok
    )


def _same_scope(a: dict, b: dict) -> bool:
    """IDENTICAL applies_to scope, order-independent (token-SET equality).

    Empty-vs-empty counts as same; empty-vs-set does NOT. Crucially, mere
    overlap is NOT enough: a partial overlap ('recambios,wazion' vs
    'recambios,grwellness') or a parent/child scope ('recambios' vs
    'recambios/theme') is a DIFFERENT scope and must be NOOP — never merge a
    learning whose scope is genuinely narrower or only partially shared.
    """
    a_scope = str(a.get("applies_to") or "").strip()
    b_scope = str(b.get("applies_to") or "").strip()
    if not a_scope and not b_scope:
        return True
    if not a_scope or not b_scope:
        return False
    return _scope_tokens(a_scope) == _scope_tokens(b_scope)


def _dedupe_key(canonical: dict, victim: dict) -> str:
    """Stable, order-independent key for a (canonical, victim) supersede pair."""
    pair = sorted([str(canonical.get("id")), str(victim.get("id"))])
    return f"rewrite-dedup:{pair[0]}:{pair[1]}"


def _pair_similarity(a: dict, b: dict) -> float:
    """Ultra-safe duplicate similarity between two learnings.

    We take the MAX of (title+content) similarity and content-only similarity.
    Two true duplicates with slightly different titles share identical (or
    one-substring-of-the-other) content, so content-only similarity is the
    honest signal for "same fact"; the combined score guards against
    same-content/different-meaning by still requiring the substring + scope +
    no-sibling gates downstream. Exact-title duplicates score 1.0 directly.
    """
    a_title = _normalize_text(a.get("title", ""))
    b_title = _normalize_text(b.get("title", ""))
    if a_title and a_title == b_title:
        return 1.0
    combined = candidate_similarity(
        f"{a.get('title','')} {a.get('content','')}",
        f"{b.get('title','')} {b.get('content','')}",
    )
    content = candidate_similarity(
        str(a.get("content", "")),
        str(b.get("content", "")),
    )
    return max(combined, content)


def find_dedup_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Find ultra-safe duplicate learning pairs. Pure analysis, no mutation.

    Returns a list of decision dicts. Each entry that survives every gate is a
    DELETE(soft) decision (supersede the older row by the newer canonical one).
    Ambiguous or protected pairs are recorded as NOOP with a reason.
    """
    rows = _fetch_active_learnings(conn)
    decisions: list[dict] = []
    consumed: set[int] = set()  # rows already assigned to a pair this run

    # Group by category first (resolver semantics: dedup is within a category).
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        by_category.setdefault(str(row.get("category") or ""), []).append(row)

    for category, group in by_category.items():
        n = len(group)
        for i in range(n):
            a = group[i]
            if int(a.get("id")) in consumed:
                continue
            for j in range(i + 1, n):
                b = group[j]
                if int(b.get("id")) in consumed:
                    continue

                sim = _pair_similarity(a, b)
                if sim < SIMILARITY_FLOOR:
                    continue
                if not _substring_dup(a, b):
                    continue
                if not _same_scope(a, b):
                    decisions.append(_noop(a, b, sim, "different_applies_to"))
                    continue

                # Discriminant-sibling check: any OTHER active row in the
                # category that is also highly similar to either side but is
                # NOT a substring dup -> ambiguous cluster -> NOOP.
                if _has_discriminant_sibling(a, b, group):
                    decisions.append(_noop(a, b, sim, "discriminant_sibling"))
                    continue

                a_safe, a_reason = _row_dedup_safe(a)
                b_safe, b_reason = _row_dedup_safe(b)
                if not a_safe or not b_safe:
                    decisions.append(
                        _noop(a, b, sim, f"protected:{a_reason or b_reason}")
                    )
                    continue

                # Choose canonical = newer (higher id / updated_at); victim = older.
                canonical, victim = _pick_canonical(a, b)
                decisions.append(_delete_soft(canonical, victim, sim))
                consumed.add(int(canonical.get("id")))
                consumed.add(int(victim.get("id")))

    return decisions


def _has_discriminant_sibling(a: dict, b: dict, group: list[dict]) -> bool:
    """A third active row this similar to a/b, but NOT a substring dup, makes the
    cluster ambiguous -> the whole cluster must be NOOP. Uses the same
    content-aware similarity as candidate detection so a near-duplicate with a
    flipped meaning (negation) is caught as a discriminant, not silently merged.
    """
    a_id, b_id = int(a.get("id")), int(b.get("id"))
    for other in group:
        oid = int(other.get("id"))
        if oid in (a_id, b_id):
            continue
        sim = max(_pair_similarity(a, other), _pair_similarity(b, other))
        if sim >= SIBLING_SIMILARITY_FLOOR and not (_substring_dup(a, other) or _substring_dup(b, other)):
            return True
    return False


def _pick_canonical(a: dict, b: dict) -> tuple[dict, dict]:
    def key(r: dict) -> tuple[float, int]:
        try:
            ts = float(r.get("updated_at") or r.get("created_at") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        return (ts, int(r.get("id") or 0))
    if key(a) >= key(b):
        return a, b
    return b, a


def _noop(a: dict, b: dict, sim: float, reason: str) -> dict:
    return {
        "decision": "NOOP",
        "reason": reason,
        "similarity": round(float(sim), 4),
        "left_id": int(a.get("id")),
        "right_id": int(b.get("id")),
        "category": str(a.get("category") or ""),
    }


def _delete_soft(canonical: dict, victim: dict, sim: float) -> dict:
    return {
        "decision": "DELETE",          # soft: supersede, never drop
        "primitive": "supersede_learning",
        "reason": "ultra_safe_duplicate",
        "similarity": round(float(sim), 4),
        "canonical_id": int(canonical.get("id")),
        "canonical_title": str(canonical.get("title") or ""),
        "victim_id": int(victim.get("id")),
        "victim_title": str(victim.get("title") or ""),
        "category": str(canonical.get("category") or ""),
        "before_state": {
            "id": int(victim.get("id")),
            "status": str(victim.get("status") or "active"),
            "content_hash": _content_hash(victim),
        },
        "dedupe_key": _dedupe_key(canonical, victim),
    }


def apply_decision(decision: dict, run_id: str) -> dict:
    """Apply one DELETE(soft) decision via supersede_learning + reversible audit."""
    canonical_id = int(decision["canonical_id"])
    victim_id = int(decision["victim_id"])
    dedupe_key = decision["dedupe_key"]
    note = (
        f"Deep Sleep REWRITE: superseded duplicate #{victim_id} by canonical "
        f"#{canonical_id} (sim={decision['similarity']}, run={run_id})."
    )
    result = nexo_db.supersede_learning(victim_id, canonical_id, note)
    if isinstance(result, dict) and result.get("error"):
        return {"status": "error", "details": result, "dedupe_key": dedupe_key}

    after_state = {
        "id": victim_id,
        "status": str(result.get("status") or "superseded"),
        "supersedes_by": canonical_id,
        "content_hash": decision["before_state"]["content_hash"],
    }
    # Append-only, reversible audit. Restore = set victim status back to active.
    try:
        nexo_db.add_item_history(
            "learning",
            str(victim_id),
            "deep_sleep_rewrite_supersede",
            note=note,
            actor="deep_sleep_rewrite",
            metadata={
                "before": decision["before_state"],
                "after": after_state,
                "primitive": "supersede_learning",
                "dedupe_key": dedupe_key,
                "canonical_id": canonical_id,
                "reversible": True,
                "restore_hint": "UPDATE learnings SET status='active' WHERE id=victim_id",
            },
        )
    except Exception as e:
        # The supersede is soft and fully reversible even without the audit row;
        # never fail the operation on audit failure, but record it.
        print(f"  [rewrite] Warning: audit log failed for #{victim_id}: {e}", file=sys.stderr)

    return {
        "status": "applied",
        "decision": "DELETE",
        "canonical_id": canonical_id,
        "victim_id": victim_id,
        "dedupe_key": dedupe_key,
        "reversible": True,
    }


def _count_active_learnings(conn: sqlite3.Connection) -> int:
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(learnings)").fetchall()}
        where = " WHERE COALESCE(status,'active')='active'" if "status" in cols else ""
        return int(conn.execute(f"SELECT COUNT(*) FROM learnings{where}").fetchone()[0])
    except Exception:
        return 0


def run_rewrite(target_date: str, *, dry_run: bool | None = None) -> dict:
    """Phase 5 entrypoint. Returns the manifest dict (also written to disk)."""
    dry = _is_dry_run(dry_run)
    cap = _max_changes()
    run_id = generate_run_id(target_date)
    db_path = Path(NEXO_DB)

    manifest: dict = {
        "phase": "rewrite",
        "date": target_date,
        "run_id": run_id,
        "dry_run": dry,
        "cap": cap,
        "started_at": datetime.now().isoformat(),
        "decisions": [],
        "applied": [],
        "draft_for_morning": [],
        "stats": {
            "candidates": 0,
            "noop": 0,
            "delete_soft_eligible": 0,
            "applied": 0,
            "deferred_cap": 0,
            "skipped_dedupe": 0,
            "errors": 0,
        },
        "metrics": {},
        "backup": None,
    }

    if not db_path.exists():
        manifest["note"] = "nexo.db not found; nothing to do"
        _write_manifest(target_date, manifest)
        return manifest

    conn = sqlite3.connect(str(db_path))
    try:
        active_before = _count_active_learnings(conn)
        decisions = find_dedup_candidates(conn)
    finally:
        conn.close()

    manifest["decisions"] = decisions
    manifest["stats"]["candidates"] = len(decisions)
    noop = [d for d in decisions if d["decision"] == "NOOP"]
    deletes = [d for d in decisions if d["decision"] == "DELETE"]
    manifest["stats"]["noop"] = len(noop)
    manifest["stats"]["delete_soft_eligible"] = len(deletes)

    # Idempotency: skip pairs already applied in the last 7 days.
    existing_keys = load_recent_dedupe_keys(target_date)
    existing_keys |= _recent_rewrite_keys(target_date)
    fresh_deletes = []
    for d in deletes:
        if d["dedupe_key"] in existing_keys:
            manifest["stats"]["skipped_dedupe"] += 1
        else:
            fresh_deletes.append(d)

    # Cap: apply up to `cap`, defer the rest to morning review.
    to_apply = fresh_deletes[:cap]
    deferred = fresh_deletes[cap:]
    manifest["draft_for_morning"] = deferred
    manifest["stats"]["deferred_cap"] = len(deferred)

    if dry:
        manifest["note"] = "dry_run: manifest only, zero mutations"
        manifest["finished_at"] = datetime.now().isoformat()
        manifest["metrics"] = {
            "active_learnings_before": active_before,
            "active_learnings_after": active_before,
            "duplicates_pending": len(deletes),
            "physical_rows_deleted": 0,
        }
        _write_manifest(target_date, manifest)
        return manifest

    # Real apply path: fail-closed backup before ANY mutation.
    if to_apply:
        try:
            backup_path = backup_db(db_path, run_id, fail_closed=True)
            manifest["backup"] = str(backup_path) if backup_path else None
        except BackupError as e:
            manifest["aborted"] = f"backup_failed: {e}"
            manifest["finished_at"] = datetime.now().isoformat()
            _write_manifest(target_date, manifest)
            print(f"[rewrite] ABORT: {e}", file=sys.stderr)
            return manifest
        # Fail-closed independent of backup_db internals: if there are changes
        # to apply but no verified backup path was produced, abort rather than
        # mutate unprotected.
        if not backup_path:
            manifest["aborted"] = "backup_missing: no verified snapshot before mutation"
            manifest["finished_at"] = datetime.now().isoformat()
            _write_manifest(target_date, manifest)
            print("[rewrite] ABORT: no verified backup snapshot", file=sys.stderr)
            return manifest

    for d in to_apply:
        outcome = apply_decision(d, run_id)
        manifest["applied"].append(outcome)
        if outcome["status"] == "applied":
            manifest["stats"]["applied"] += 1
        else:
            manifest["stats"]["errors"] += 1

    conn = sqlite3.connect(str(db_path))
    try:
        active_after = _count_active_learnings(conn)
        total_rows = int(conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0])
    finally:
        conn.close()

    manifest["metrics"] = {
        "active_learnings_before": active_before,
        "active_learnings_after": active_after,
        "duplicates_collapsed": manifest["stats"]["applied"],
        "duplicates_pending": len(deferred),
        "total_rows": total_rows,
        # Anti-loss invariant: this phase never drops a row.
        "physical_rows_deleted": 0,
    }
    manifest["finished_at"] = datetime.now().isoformat()
    _write_manifest(target_date, manifest)
    return manifest


def _recent_rewrite_keys(target_date: str, days: int = 7) -> set[str]:
    """Load dedupe_keys from prior rewrite manifests (idempotency across nights)."""
    keys: set[str] = set()
    try:
        base = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return keys
    for i in range(days):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        path = Path(DEEP_SLEEP_DIR) / f"{d}-rewrite-manifest.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in data.get("applied", []):
            dk = entry.get("dedupe_key")
            if dk and entry.get("status") == "applied":
                keys.add(dk)
    return keys


def _write_manifest(target_date: str, manifest: dict) -> Path:
    out_dir = Path(DEEP_SLEEP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{target_date}-rewrite-manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    dry = "--dry-run" in sys.argv[2:] or None
    manifest = run_rewrite(target_date, dry_run=dry)
    s = manifest["stats"]
    print(f"[rewrite] Phase 5 REWRITE for {target_date} (dry_run={manifest['dry_run']})")
    print(f"  Candidates: {s['candidates']} | NOOP: {s['noop']} | "
          f"eligible: {s['delete_soft_eligible']}")
    print(f"  Applied (soft supersede): {s['applied']} | deferred (cap): {s['deferred_cap']} | "
          f"dedupe-skipped: {s['skipped_dedupe']} | errors: {s['errors']}")
    m = manifest.get("metrics", {})
    if m:
        print(f"  Active learnings: {m.get('active_learnings_before')} -> "
              f"{m.get('active_learnings_after')} | physical rows deleted: "
              f"{m.get('physical_rows_deleted')}")
    print(f"[rewrite] Manifest: {Path(DEEP_SLEEP_DIR) / f'{target_date}-rewrite-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
