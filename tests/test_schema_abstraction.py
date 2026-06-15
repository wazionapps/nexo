"""Ola 4 SCHEMA-ABSTRACTION tests — narrow slice (silent-failure archetype).

Precision-first contract under test:
  (a) DISTILL: 3 incidents of the SAME archetype → exactly ONE template with a
      complete diagnosis + prevention.
  (b) ANTI-NOISE: disparate incidents / fewer than 3 / one-off → NO template.
  (c) INJECTION: an action that matches the archetype → template injected into
      pre-action context; an action that does NOT match → no injection.
  (d) IDEMPOTENT: re-running the distiller does not duplicate templates.

All runs use the isolated temp DB from conftest. Note conftest sets
NEXO_SKIP_SEMANTIC_SIMILARITY=1, so similarity is pure keyword Jaccard — the
clustering must work under keyword-only matching.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_stack():
    import db
    import failure_prevention
    import learning_resolver
    import schema_abstraction

    importlib.reload(db)
    importlib.reload(failure_prevention)
    importlib.reload(learning_resolver)
    importlib.reload(schema_abstraction)
    db.init_db()
    return db, failure_prevention, schema_abstraction


def _ingest_silent_failure(fp, *, area: str, variant: int):
    """Three differently-worded reports of the SAME silent-failure archetype.

    Same shape ("cron exited 0 but the tool failed silently / swallowed the
    error / produced an empty result"), different wording → must cluster.
    """
    symptoms = {
        1: (
            "Weekly cron exited 0 but the reviews scrape failed silently — the wrapper "
            "swallowed the error with || echo {} so the scheduler looked green while the "
            "badges stayed frozen for three weeks."
        ),
        2: (
            "Scheduled launchd job returned status 0 yet the tool never actually ran; the "
            "exception was suppressed and the scrape produced an empty result, so the data "
            "looked stale but no alert fired."
        ),
        3: (
            "Cron reported success (exit 0) but the underlying fetch crashed and was masked "
            "by a bare handler — the output was empty/frozen and nobody noticed because no "
            "error escalated."
        ),
    }
    return fp.ingest_failure(
        failure_type="tool",
        area=area,
        primary_source_type="manual_review",
        primary_source_ref=f"evidence:silent-failure-{area}-{variant}",
        symptom=symptoms[variant],
        missed_signal="No alert path on a missing/empty source",
        root_cause="Wrapper exit code trusted instead of the tool's own output",
        corrective_action="Assert the tool ran and produced a fresh non-empty result; escalate on missing source",
        severity="p2",
        confidence=0.9,
    )


def _ingest_unrelated(fp, *, area: str, ref: str, symptom: str):
    return fp.ingest_failure(
        failure_type="communication",
        area=area,
        primary_source_type="manual_review",
        primary_source_ref=f"evidence:{ref}",
        symptom=symptom,
        severity="p3",
        confidence=0.6,
    )


# ── (a) DISTILL ───────────────────────────────────────────────────────────


def test_three_silent_failures_distill_one_template(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        res = _ingest_silent_failure(fp, area="recambios", variant=v)
        assert res["ok"] is True

    report = sa.distill_templates()
    assert report["ok"] is True
    assert report["templates_created"] == 1, report
    assert len(report["templates_minted"]) == 1

    templates = sa.list_templates(status="active")
    assert len(templates) == 1
    tpl = templates[0]
    assert tpl["archetype"] == "silent_failure"
    assert tpl["incident_count"] == 3
    assert tpl["confidence"] >= sa.MIN_TEMPLATE_CONFIDENCE
    # Complete diagnosis + prevention are present and load-bearing.
    assert len(tpl["diagnosis_steps"]) >= 3
    joined = " ".join(tpl["diagnosis_steps"]).lower()
    assert "exit" in joined and ("swallow" in joined or "|| echo" in joined or "swallowing" in joined)
    assert tpl["prevention"]
    assert "exit code" in tpl["prevention"].lower() or "wrapper exit" in tpl["prevention"].lower()


# ── (b) ANTI-NOISE ──────────────────────────────────────────────────────────


def test_two_silent_failures_below_threshold_no_template(isolated_db):
    db, fp, sa = _reload_stack()
    # Only 2 incidents → below MIN_CLUSTER_SIZE (3).
    _ingest_silent_failure(fp, area="recambios", variant=1)
    _ingest_silent_failure(fp, area="recambios", variant=2)

    report = sa.distill_templates()
    assert report["templates_created"] == 0
    assert sa.list_templates(status="active") == []
    # The below-threshold cluster is recorded as a low-signal skip, not a template.
    assert any(s["reason"] == "below_min_cluster_size" for s in report["skipped_low_signal"])


def test_disparate_incidents_no_template(isolated_db):
    db, fp, sa = _reload_stack()
    # Three UNRELATED incidents that are not the silent-failure archetype.
    _ingest_unrelated(fp, area="comms", ref="a", symptom="Used the wrong customer name in an email greeting")
    _ingest_unrelated(fp, area="comms", ref="b", symptom="Sent a WhatsApp template in the wrong language to a contact")
    _ingest_unrelated(fp, area="comms", ref="c", symptom="The newsletter subject line had a typo in the promo code")

    report = sa.distill_templates()
    assert report["templates_created"] == 0
    assert sa.list_templates(status="active") == []
    # None of them even classified into an archetype.
    assert report["incidents"] == 0


def test_oneoff_silent_failure_no_template(isolated_db):
    db, fp, sa = _reload_stack()
    _ingest_silent_failure(fp, area="recambios", variant=1)
    report = sa.distill_templates()
    assert report["templates_created"] == 0
    assert sa.list_templates(status="active") == []


def test_same_archetype_different_areas_do_not_combine(isolated_db):
    db, fp, sa = _reload_stack()
    # 2 in one area + 1 in another → neither area reaches 3 → no template.
    _ingest_silent_failure(fp, area="recambios", variant=1)
    _ingest_silent_failure(fp, area="recambios", variant=2)
    _ingest_silent_failure(fp, area="wazion", variant=3)

    report = sa.distill_templates()
    assert report["templates_created"] == 0
    assert sa.list_templates(status="active") == []


# ── (c) INJECTION ───────────────────────────────────────────────────────────


def test_matching_action_injects_template(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)
    assert sa.distill_templates()["templates_created"] == 1

    from db import build_pre_action_context, format_pre_action_context_bundle

    # An action squarely in the silent-failure shape.
    bundle = build_pre_action_context(
        query="verify the weekly cron actually ran the reviews scrape and did not swallow the error with || echo",
        hours=24,
        limit=5,
    )
    assert bundle.get("diagnostic_templates"), bundle
    assert bundle["diagnostic_templates"][0]["archetype"] == "silent_failure"

    rendered = format_pre_action_context_bundle(bundle)
    assert "PRIMED DIAGNOSIS" in rendered
    assert "exit" in rendered.lower()


def test_nonmatching_action_does_not_inject(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)
    assert sa.distill_templates()["templates_created"] == 1

    from db import build_pre_action_context

    # A totally unrelated action — must NOT match the silent-failure archetype.
    bundle = build_pre_action_context(
        query="draft a friendly birthday message for a customer in Spanish",
        hours=24,
        limit=5,
    )
    assert not bundle.get("diagnostic_templates"), bundle["diagnostic_templates"]


def test_match_helper_precision(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)
    sa.distill_templates()

    # Clear match.
    hits = sa.match_templates_for_action(
        query="the scheduled cron exited silently and the scrape output is stale/empty"
    )
    assert len(hits) == 1
    assert hits[0]["archetype"] == "silent_failure"

    # No match.
    assert sa.match_templates_for_action(query="optimize the pricing engine margin calculation") == []


# ── (d) IDEMPOTENT ──────────────────────────────────────────────────────────


def test_distill_is_idempotent(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)

    first = sa.distill_templates()
    assert first["templates_created"] == 1

    second = sa.distill_templates()
    assert second["templates_created"] == 0  # no new template
    assert second["templates_refreshed"] == 0  # same member set → no churn

    # Still exactly one template.
    assert len(sa.list_templates(status="active")) == 1


def test_new_incident_refreshes_not_duplicates(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)
    assert sa.distill_templates()["templates_created"] == 1

    # A 4th distinct incident of the same archetype/area arrives — a clear
    # paraphrase that shares the cluster's vocabulary (cron / exit 0 / scrape /
    # swallowed / frozen / alert), so it joins under keyword-only similarity.
    fp.ingest_failure(
        failure_type="tool",
        area="recambios",
        primary_source_type="manual_review",
        primary_source_ref="evidence:silent-failure-recambios-4",
        symptom=(
            "Yet another weekly cron exited 0 while the reviews scrape silently failed; the "
            "wrapper swallowed the error so the scheduler looked green and the badges stayed "
            "frozen with no alert fired."
        ),
        missed_signal="No alert path on a missing/empty source",
        root_cause="Wrapper exit code trusted instead of the tool output",
        corrective_action="Assert the tool ran and produced a fresh non-empty result escalate on missing source",
        severity="p2",
        confidence=0.9,
    )
    report = sa.distill_templates()
    assert report["templates_created"] == 0  # NOT a new template
    assert report["templates_refreshed"] == 1  # member set grew
    templates = sa.list_templates(status="active")
    assert len(templates) == 1
    assert templates[0]["incident_count"] == 4


def test_retire_template_lifecycle(isolated_db):
    db, fp, sa = _reload_stack()
    for v in (1, 2, 3):
        _ingest_silent_failure(fp, area="recambios", variant=v)
    sa.distill_templates()
    tpl = sa.list_templates(status="active")[0]

    res = sa.retire_template(tpl["template_uid"], reason="verified fix shipped")
    assert res["retired"] is True
    assert sa.list_templates(status="active") == []
    # A retired template no longer injects.
    assert sa.match_templates_for_action(query="cron exit 0 swallowed error scrape empty") == []
