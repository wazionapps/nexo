from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_stack():
    import db
    import learning_resolver
    import consolidation_prep

    importlib.reload(db)
    importlib.reload(learning_resolver)
    importlib.reload(consolidation_prep)
    db.init_db()
    return db, learning_resolver, consolidation_prep


def _seed_corpus(db, n: int, *, prefix: str = "topic"):
    """Seed n synthetic active learnings across distinct topics."""
    for i in range(n):
        db.create_learning(
            category="nexo-ops",
            title=f"{prefix} rule {i} about subsystem-{i}",
            content=(
                f"When working on subsystem-{i} always verify config-{i} and "
                f"validate output-{i} before shipping change-{i}."
            ),
            reasoning=f"Reasoning for subsystem-{i}.",
        )


def _diary(summary: str, critique: str, *, domain: str = ""):
    return {
        "id": int(hashlib.md5(summary.encode()).hexdigest()[:6], 16),
        "session_id": "s-" + hashlib.md5(summary.encode()).hexdigest()[:8],
        "summary": summary,
        "self_critique": critique,
        "domain": domain,
    }


def test_brief_size_bounded_with_500_learnings(isolated_db):
    db, _resolver, prep = _reload_stack()
    _seed_corpus(db, 500)

    diaries = [
        _diary(
            "Worked on subsystem-3 deployment",
            "I should always verify config-3 and validate output-3 before shipping change-3.",
        ),
        _diary(
            "Novel area never seen before",
            "I forgot to instrument the brand-new telemetry pipeline X-9000.",
        ),
    ]

    brief = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)

    assert brief["corpus_size"] == 500
    assert len(brief["shortlist"]) <= 25
    assert len(brief["contradiction_pairs"]) <= 15
    assert len(json.dumps(brief, ensure_ascii=False)) <= 6000
    if brief["truncated"]:
        assert brief["truncated"] is True


def test_token_budget_independent_of_corpus_growth(isolated_db):
    db, _resolver, prep = _reload_stack()

    # Seed short learnings that ALL clearly match the diary topic, so the
    # shortlist saturates its cap (25) at both corpus sizes without truncation —
    # proving the brief size is governed by the caps, not the corpus size.
    diary_topic = "Verify deploy config and validate output before shipping."

    def _seed_matching(n):
        for _ in range(n):
            db.create_learning(
                "nexo-ops",
                "Verify deploy config and validate output before shipping",
                "Verify deploy config and validate output before shipping.",
                reasoning="Short rule.",
            )

    diaries = [_diary("Reviewed deploy", diary_topic)]

    _seed_matching(50)
    brief_small = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)
    size_small = len(json.dumps(brief_small, ensure_ascii=False))

    _seed_matching(450)  # corpus now ~500
    brief_big = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)
    size_big = len(json.dumps(brief_big, ensure_ascii=False))

    assert brief_big["corpus_size"] >= 500
    # The shortlist is hard-capped, so a 10x bigger corpus does not grow it.
    assert len(brief_small["shortlist"]) <= 25
    assert len(brief_big["shortlist"]) <= 25
    # The brief must NOT scale with corpus size (the core anti-timeout guarantee):
    # both stay under the hard byte budget regardless of 50 vs 500 learnings.
    assert size_small <= 6000
    assert size_big <= 6000
    # 10x more learnings does not materially grow the brief (caps + truncation).
    assert size_big <= max(1, size_small) * 1.2 + 200


def test_mechanical_dedup_still_runs(isolated_db):
    db, _resolver, prep = _reload_stack()

    # Two learnings with colliding normalized_key (same title diff casing).
    db.create_learning("nexo-ops", "Guard Before Edit", "Run guard before editing files.")
    db.create_learning("nexo-ops", "guard before edit", "Always run guard before editing.")

    # A weak learning (low weight).
    weak = db.create_learning("nexo-ops", "Weak rare rule about flaky cron", "Some flaky cron note.")
    conn = db.get_db()
    conn.execute("UPDATE learnings SET weight = 0.5 WHERE id = ?", (weak["id"],))
    conn.commit()

    # An active learning that contradicts a today-topic.
    contra = db.create_learning(
        "nexo-ops",
        "Always validate releases before pushing",
        "Always validate releases before pushing to production.",
        reasoning="Critical.",
    )
    conn.execute("UPDATE learnings SET weight = 3.0 WHERE id = ?", (contra["id"],))
    conn.commit()

    diaries = [
        _diary(
            "Release hotfix incident",
            "We learned to skip release validation on urgent hotfixes to move faster.",
        ),
    ]

    brief = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)

    # preference_key_dupes lists the two colliding ids.
    dupe_keys = brief["preference_key_dupes"]
    assert any(len(entry["ids"]) >= 2 for entry in dupe_keys), brief["preference_key_dupes"]

    # stale_candidates includes the weak learning with a weakness reason.
    stale_ids = {entry["id"]: entry for entry in brief["stale_candidates"]}
    assert weak["id"] in stale_ids
    assert stale_ids[weak["id"]]["weakness"]

    # contradiction_pairs references the contradicting learning vs today_topic.
    contra_pairs = [
        p for p in brief["contradiction_pairs"]
        if p["existing_id"] == contra["id"] and p["with"] == "today_topic"
    ]
    assert contra_pairs, brief["contradiction_pairs"]


def test_coverage_flags_existing_topic(isolated_db):
    db, _resolver, prep = _reload_stack()

    db.create_learning(
        "nexo-ops",
        "Guard before edit",
        "Run guard before editing any code to avoid blocking learnings.",
        reasoning="Avoids edits that violate conditioned-file rules.",
    )

    diaries = [
        _diary(
            "Edited code without guard",
            "I edited code and should always run guard before editing to avoid blocking learnings.",
        ),
        _diary(
            "Brand new unrelated area",
            "I forgot to instrument the brand-new telemetry pipeline X-9000.",
        ),
    ]

    brief = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)

    topics = {t["slug"]: t for t in brief["today_topics"]}
    covered = [t for t in brief["today_topics"] if t["has_existing_coverage"]]
    novel = [t for t in brief["today_topics"] if not t["has_existing_coverage"]]

    assert covered, brief["today_topics"]
    assert all(t["covering_ids"] for t in covered)
    assert novel, brief["today_topics"]


def test_read_only_no_mutation(isolated_db):
    db, _resolver, prep = _reload_stack()
    _seed_corpus(db, 30)

    conn = db.get_db()
    before_count = conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    before_rows = conn.execute(
        "SELECT id, title, content, status, weight FROM learnings ORDER BY id"
    ).fetchall()
    before_checksum = hashlib.md5(
        json.dumps([list(map(str, r)) for r in before_rows]).encode()
    ).hexdigest()

    diaries = [_diary("subsystem-5 work", "verify config-5 and validate output-5.")]
    prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)

    after_count = conn.execute("SELECT COUNT(*) FROM learnings").fetchone()[0]
    after_rows = conn.execute(
        "SELECT id, title, content, status, weight FROM learnings ORDER BY id"
    ).fetchall()
    after_checksum = hashlib.md5(
        json.dumps([list(map(str, r)) for r in after_rows]).encode()
    ).hexdigest()

    assert after_count == before_count
    assert after_checksum == before_checksum


def test_helper_handles_empty_corpus(isolated_db):
    db, _resolver, prep = _reload_stack()

    diaries = [_diary("Some session", "I should validate the deploy config next time.")]
    brief = prep.build_consolidation_brief(diaries, conn=db.get_db(), max_chars=6000)

    assert brief["corpus_size"] == 0
    assert brief["shortlist"] == []
    assert brief["contradiction_pairs"] == []
    assert brief["stale_candidates"] == []
    assert brief["preference_key_dupes"] == []
    assert brief["truncated"] is False
    # today_topics still emitted so the LLM knows what it judged.
    assert len(brief["today_topics"]) == 1
