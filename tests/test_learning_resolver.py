from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_stack():
    import db
    import learning_resolver

    importlib.reload(db)
    importlib.reload(learning_resolver)
    db.init_db()
    return db, learning_resolver


def test_resolver_decides_merge_for_duplicate_title(isolated_db):
    db, resolver = _reload_stack()
    db.create_learning("nexo-ops", "Guard before edit", "Run guard before editing.")

    result = resolver.resolve_learning_candidate(
        category="nexo-ops",
        title="guard before edit",
        content="Same rule again.",
        source_authority="explicit_instruction",
    )

    assert result["action"] == "merge"
    assert result["reason"] == "exact_title_duplicate"
    assert result["target_id"] == 1


def test_resolver_requires_review_when_low_authority_conflicts(isolated_db):
    db, resolver = _reload_stack()
    db.create_learning(
        "nexo-ops",
        "Always validate releases",
        "Always validate releases before pushing.",
    )
    db.update_learning(1, reasoning="Critical operator rule.")
    conn = db.get_db()
    conn.execute(
        "UPDATE learnings SET applies_to = ?, priority = ? WHERE id = 1",
        ("/repo/CHANGELOG.md", "critical"),
    )
    conn.commit()

    result = resolver.resolve_learning_candidate(
        category="nexo-ops",
        title="Skip release validation",
        content="Skip release validation on hotfixes.",
        applies_to="/repo/CHANGELOG.md",
        source_authority="deep_sleep",
    )

    assert result["action"] == "conflict_review"
    assert result["target_id"] == 1


def test_resolver_allows_francisco_correction_to_supersede_conflict(isolated_db):
    db, resolver = _reload_stack()
    db.create_learning(
        "nexo-ops",
        "Do not edit protocol directly",
        "Never edit protocol.py directly.",
    )
    conn = db.get_db()
    conn.execute(
        "UPDATE learnings SET applies_to = ?, priority = ? WHERE id = 1",
        ("/repo/src/plugins/protocol.py", "high"),
    )
    conn.commit()

    result = resolver.resolve_learning_candidate(
        category="nexo-ops",
        title="Edit protocol through controlled hotfix",
        content="Edit protocol.py directly only when Francisco explicitly approves a controlled hotfix.",
        applies_to="/repo/src/plugins/protocol.py",
        source_authority="francisco_correction",
    )

    assert result["action"] == "supersede"
    assert result["reason"] == "higher_authority_conflict"
    assert result["target_id"] == 1


def test_normalized_key_and_candidate_similarity_public(isolated_db):
    _db, resolver = _reload_stack()

    # normalized_key collapses casing/whitespace of the title.
    key_a = resolver.normalized_key("Guard Before Edit")
    key_b = resolver.normalized_key("  guard   before edit ")
    assert key_a == key_b

    # applies_to ordering does not change the key.
    key_c = resolver.normalized_key("Rule", "/a/x.py, /b/y.py")
    key_d = resolver.normalized_key("Rule", "/b/y.py, /a/x.py")
    assert key_c == key_d

    # candidate_similarity: 1.0 for identical text, low for unrelated.
    same = resolver.candidate_similarity(
        "Always run guard before editing code",
        "Always run guard before editing code",
    )
    assert same == 1.0
    unrelated = resolver.candidate_similarity(
        "Pin fastembed minimum version in requirements",
        "Rotate the Stripe billing API key after exposure",
    )
    assert unrelated < 0.85
    # Empty input yields 0.0.
    assert resolver.candidate_similarity("", "anything") == 0.0
