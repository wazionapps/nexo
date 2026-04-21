from __future__ import annotations

import db
import db._learnings as learnings_mod
import db._reminders as reminders_mod


def test_find_similar_learnings_accepts_strong_semantic_paraphrase(isolated_db, monkeypatch):
    created = db.create_learning(
        category="ops",
        title="Operator routing fallback",
        content="Use operator mailboxes as fallback destinations for escalations.",
    )

    def fake_hybrid(candidate_text, existing_text, **kwargs):
        if "fallback recipients" in candidate_text.lower() and "fallback destinations" in existing_text.lower():
            return 0.88
        return 0.0

    monkeypatch.setattr(learnings_mod, "hybrid_similarity_score", fake_hybrid)
    matches = learnings_mod.find_similar_learnings(
        999,
        "Operator email routing",
        "Use operator mailboxes as fallback recipients for escalations.",
        "ops",
    )

    assert matches == [(created["id"], 0.88)]


def test_find_similar_learnings_keeps_keyword_fallback_when_semantic_unavailable(isolated_db, monkeypatch):
    created = db.create_learning(
        category="ops",
        title="Agent mailbox cadence",
        content="Monitor the agent mailbox every minute and process one email per run.",
    )

    monkeypatch.setattr(
        learnings_mod,
        "hybrid_similarity_score",
        lambda candidate_text, existing_text, **kwargs: 0.36
        if "agent mailbox" in candidate_text.lower() and "agent mailbox" in existing_text.lower()
        else 0.0,
    )
    matches = learnings_mod.find_similar_learnings(
        999,
        "Agent mailbox cadence",
        "Monitor the agent mailbox every minute to process one email per run.",
        "ops",
    )

    assert matches == [(created["id"], 0.36)]


def test_find_similar_followups_accepts_semantic_paraphrase(isolated_db, monkeypatch):
    db.create_followup(
        "NF-EMAIL-001",
        "Review unread messages in the accounting mailbox and reply if needed",
        date="2026-05-01",
    )

    def fake_hybrid(candidate_text, existing_text, **kwargs):
        candidate_norm = candidate_text.lower()
        if "factur" in candidate_norm and "accounting mailbox" in existing_text.lower():
            return 0.84
        return 0.0

    monkeypatch.setattr(reminders_mod, "hybrid_similarity_score", fake_hybrid)
    matches = reminders_mod.find_similar_followups(
        "Revisar correos sin leer del buzón de facturación y contestarlos si hace falta"
    )

    assert len(matches) == 1
    assert matches[0]["id"] == "NF-EMAIL-001"
    assert matches[0]["_similarity"] == 0.84


def test_find_similar_followups_rejects_weak_semantic_noise(isolated_db, monkeypatch):
    db.create_followup(
        "NF-RUNNER-001",
        "Run the morning report and send the digest to the operator",
        date="2026-05-01",
    )

    monkeypatch.setattr(reminders_mod, "hybrid_similarity_score", lambda *args, **kwargs: 0.22)
    matches = reminders_mod.find_similar_followups(
        "Rotate nginx logs and archive old compressed bundles"
    )

    assert matches == []
