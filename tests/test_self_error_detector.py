"""Ola 2 — auto-detection of own prior errors → immediate learning + prevention.

These tests pin the PRECISION contract: high-confidence objective evidence
fires exactly ONE learning with a prevention rule; everything ambiguous /
iterative / refactor-shaped produces ZERO learnings (anti-noise), and the same
revealed error never duplicates.

Two layers:
  * pure-function tests on ``self_error_detector.evaluate_self_error`` (no I/O,
    exact decisions);
  * integration tests driving the real ``handle_task_open``/``handle_task_close``
    round-trip against the isolated /tmp DB from conftest.
"""
from __future__ import annotations

import json

import pytest


# ─────────────────────────────────────────────────────────────────────
# Layer 1 — pure evaluator (deterministic, no DB)
# ─────────────────────────────────────────────────────────────────────


def _prior_done(task_id="PT-prior", files=None, area="nexo-ops", status="done"):
    return {
        "task_id": task_id,
        "status": status,
        "area": area,
        "goal": "shipped the recambios review fetcher",
        "files": json.dumps(files or ["/repo/fetch-reviews.py"]),
        "files_changed": json.dumps(files or ["/repo/fetch-reviews.py"]),
    }


def _current(task_id="PT-cur", files=None, area="nexo-ops", goal="add the weekly cron"):
    return {
        "task_id": task_id,
        "status": "done",
        "area": area,
        "goal": goal,
        "files": json.dumps(files or ["/repo/fetch-reviews.py"]),
        "files_changed": json.dumps(files or ["/repo/fetch-reviews.py"]),
    }


def test_positive_file_overlap_omission_fires():
    """The canonical 'code shipped but the cron was never created' case."""
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(),
        prior_tasks=[_prior_done()],
        closure_text=(
            "While verifying the weekly run I noticed the previous task shipped the "
            "fetch code but never created the cron in GCloud, so the job never ran. "
            "Created the missing cron now and confirmed it triggers."
        ),
        correction_happened=False,
    )
    assert ev["decision"] == "fire"
    assert ev["confidence"] >= sed.FIRE_THRESHOLD
    assert ev["signal"] == "file_overlap_correction"
    assert ev["prior_task_id"] == "PT-prior"
    assert "/repo/fetch-reviews.py" in ev["overlap_files"]


def test_positive_reopen_of_done_fires():
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(goal="redo the broken deploy"),
        prior_tasks=[_prior_done()],
        closure_text=(
            "Had to reopen the earlier task: it was previously marked as done but "
            "the deploy was not actually complete, the change never reached prod."
        ),
        correction_happened=False,
    )
    assert ev["decision"] == "fire"
    assert ev["signal"] == "reopen_of_done"
    assert ev["confidence"] >= sed.FIRE_THRESHOLD


def test_positive_correction_flag_on_overlapping_done_fires():
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(),
        prior_tasks=[_prior_done()],
        closure_text=(
            "Patched the fetcher again; the prior closed version omitted the retry path "
            "and the weekly job silently produced empty results."
        ),
        correction_happened=True,
    )
    assert ev["decision"] == "fire"
    assert ev["signal"] == "file_overlap_correction"


# ── NEGATIVES — anti-noise (the critical part) ─────────────────────────


def test_negative_clean_done_no_signal():
    """A normal clean close with no self-error semantics → nothing."""
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(goal="ship the fetcher"),
        prior_tasks=[_prior_done()],
        closure_text="Implemented the fetcher and verified pytest -q: 12 passed, 0 failed.",
        correction_happened=False,
    )
    assert ev["decision"] == "none"


def test_negative_refactor_iteration_does_not_fire():
    """Refactor / improvement on a previously-done file is forward work."""
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(goal="refactor the fetcher for readability"),
        prior_tasks=[_prior_done()],
        closure_text=(
            "Refactored and cleaned up the fetcher, renamed helpers and improved the "
            "logging. Next iteration will continue with the dashboard."
        ),
        correction_happened=False,
    )
    assert ev["decision"] == "none"


def test_negative_omission_language_but_no_prior_done_is_candidate_not_fire():
    """Omission language with NO prior done task on overlapping files → candidate, never fire."""
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(),
        prior_tasks=[],  # nothing previously declared done
        closure_text="It turned out the cron was missing, created it now.",
        correction_happened=False,
    )
    assert ev["decision"] in {"candidate", "none"}
    assert ev["decision"] != "fire"


def test_negative_prior_was_partial_not_done_does_not_fire():
    """A prior PARTIAL/FAILED task never claimed completeness → later fix is expected."""
    import self_error_detector as sed

    prior_partial = _prior_done(status="partial")
    ev = sed.evaluate_self_error(
        current_task=_current(),
        prior_tasks=[prior_partial],
        closure_text="Finished the part that was left; the earlier pass was only partial.",
        correction_happened=True,
    )
    # No prior *done* task → at most a candidate, never a learning.
    assert ev["decision"] != "fire"


def test_negative_different_area_no_false_overlap():
    """Same filename in a different area must not be treated as the same prior work."""
    import self_error_detector as sed

    prior = _prior_done(area="wazion", files=["/shared/utils.py"])
    cur = _current(area="recambios", files=["/shared/utils.py"])
    ev = sed.evaluate_self_error(
        current_task=cur,
        prior_tasks=[prior],
        closure_text="Forgot to create the cron earlier, adding it now.",
        correction_happened=False,
    )
    # Cross-area overlap is filtered out → cannot confirm same prior work.
    assert ev["decision"] != "fire"


def test_negative_trivial_evidence_capped_below_fire():
    """Strong signal words but trivially short evidence cannot reach fire."""
    import self_error_detector as sed

    ev = sed.evaluate_self_error(
        current_task=_current(),
        prior_tasks=[_prior_done()],
        closure_text="forgot cron",  # under MIN_EVIDENCE_CHARS
        correction_happened=False,
    )
    assert ev["decision"] != "fire"


# ─────────────────────────────────────────────────────────────────────
# Layer 2 — integration through the real task_close handler
# ─────────────────────────────────────────────────────────────────────


def _register_session(sid: str) -> str:
    from db import register_session

    register_session(sid, "self-error integration test")
    return sid


def _self_error_learnings() -> list[dict]:
    from db._core import get_db

    rows = get_db().execute(
        "SELECT id, title, prevention, content, reasoning FROM learnings "
        "WHERE status = 'active' AND title LIKE 'Self-error%'"
    ).fetchall()
    return [dict(r) for r in rows]


def _open_self_error_candidate_debts(task_id: str) -> list[dict]:
    from db import list_protocol_debts

    return list_protocol_debts(
        status="open", task_id=task_id, debt_type="self_error_candidate", limit=10
    )


FETCH_FILE = "/repo/recambios/fetch-reviews.py"


def _close_prior_done(sid, *, goal, files):
    """Open + close a task as DONE (the prior 'shipped' work)."""
    from plugins.protocol import handle_task_open, handle_task_close

    opened = json.loads(
        handle_task_open(
            sid=sid, goal=goal, task_type="edit", area="recambios",
            files=files, plan='["edit","verify"]', verification_step="pytest",
            stakes="low",  # isolate the detector from the unrelated cortex gate
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid, task_id=opened["task_id"], outcome="done",
            evidence="pytest -q: 8 passed, 0 failed; fetcher code landed and unit-tested.",
            change_summary="shipped the review fetcher", change_why="needed reviews badge",
            change_verify="pytest -q",
        )
    )
    assert closed["ok"] is True
    return opened["task_id"]


def test_integration_self_error_fires_one_learning(monkeypatch):
    """Prior task done on a file + later close revealing a missing step →
    exactly ONE self-error learning, with a prevention rule and
    code_test_evidence authority (NOT francisco_correction)."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-7001-2001")
    _close_prior_done(sid, goal="ship recambios review fetcher", files=FETCH_FILE)

    se_before = len(_self_error_learnings())

    opened = json.loads(
        handle_task_open(
            sid=sid, goal="create the missing weekly cron for the fetcher",
            task_type="execute", area="recambios", files=FETCH_FILE,
            plan='["inspect","create cron","verify"]', verification_step="cron list",
            stakes="low",  # isolate the detector from the unrelated cortex gate
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid, task_id=opened["task_id"], outcome="done",
            evidence=(
                "While verifying the weekly run I found the previous task shipped the "
                "fetch code but never created the cron in GCloud, so it never ran. "
                "Created the missing cron and confirmed it triggers."
            ),
            change_summary="created the weekly cron the prior task forgot",
            change_why="the earlier done task omitted the cron creation step",
            change_verify="gcloud scheduler jobs list shows the job; manual run OK",
            correction_happened=True,
        )
    )
    assert closed["ok"] is True
    se = closed.get("self_error")
    assert se is not None, f"expected self_error block, got: {closed}"
    assert se["decision"] == "fire"
    assert se["learning_ok"] is True

    se_learnings = _self_error_learnings()
    assert len(se_learnings) == 1, f"expected exactly 1 self-error learning, got {se_learnings}"
    learning = se_learnings[0]
    assert learning["prevention"], "self-error learning must carry a prevention rule"
    assert (
        "cron" in (learning["prevention"] + learning["title"]).lower()
        or "side artifact" in learning["prevention"].lower()
    )
    # The learning must be attributed to the self-error detector (objective
    # code/ledger evidence), NOT to a Francisco correction.
    assert "self-error detector" in learning["reasoning"].lower()
    assert "francisco" not in learning["reasoning"].lower()
    # And the payload passes source_authority=code_test_evidence to the resolver
    # so it can never override a higher-authority Francisco rule.
    import self_error_detector as sed

    payload = sed.build_self_error_learning(
        current_task={"area": "recambios", "goal": "x", "task_id": "PT-cur"},
        evaluation={
            "signal": "file_overlap_correction",
            "overlap_files": ["/repo/recambios/fetch-reviews.py"],
            "prior_task_id": "PT-prior",
            "confidence": 0.8,
            "reasons": [],
        },
    )
    assert payload["source_authority"] == "code_test_evidence"

    # Exactly ONE self-error learning was added by this close (the standard
    # correction-path learning is separate and expected; precision is about
    # not spawning more than one self-error learning per revealed error).
    assert len(_self_error_learnings()) == se_before + 1


def test_integration_clean_close_creates_zero_self_error_learnings():
    """A normal clean close on a fresh file → ZERO self-error learnings, clean status."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-7002-2002")
    opened = json.loads(
        handle_task_open(
            sid=sid, goal="ship a brand new module", task_type="edit", area="recambios",
            files="/repo/recambios/new_module.py", plan='["edit","test"]',
            verification_step="pytest", stakes="low",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid, task_id=opened["task_id"], outcome="done",
            evidence="pytest -q: 15 passed, 0 failed, 0 skipped; new module verified clean.",
            change_summary="added new module", change_why="new feature",
            change_verify="pytest -q",
        )
    )
    assert closed["ok"] is True
    assert closed.get("self_error") is None
    assert _self_error_learnings() == []
    # No self-error candidate debt was opened on a clean close.
    assert _open_self_error_candidate_debts(opened["task_id"]) == []


def test_integration_refactor_on_done_file_creates_zero_learnings():
    """Refactor/improvement on a previously-done file = iteration, not self-error."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-7003-2003")
    _close_prior_done(sid, goal="ship recambios review fetcher", files=FETCH_FILE)

    opened = json.loads(
        handle_task_open(
            sid=sid, goal="refactor the fetcher for readability", task_type="edit",
            area="recambios", files=FETCH_FILE, plan='["refactor","test"]',
            verification_step="pytest", stakes="low",
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid, task_id=opened["task_id"], outcome="done",
            evidence=(
                "Refactored and cleaned up the fetcher, renamed helpers, improved logging. "
                "pytest -q: 8 passed, 0 failed. Next iteration: the dashboard."
            ),
            change_summary="refactor fetcher", change_why="readability",
            change_verify="pytest -q",
        )
    )
    assert closed["ok"] is True
    # No self-error fired; if anything it's a candidate at most, never a learning.
    se = closed.get("self_error")
    assert se is None or se["decision"] != "fire"
    assert _self_error_learnings() == []


def test_integration_idempotent_same_error_twice_no_duplicate():
    """Closing two tasks that reveal the SAME prior self-error must not create
    two learnings — the learning resolver / R05 merge dedups by content."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-7004-2004")
    _close_prior_done(sid, goal="ship recambios review fetcher", files=FETCH_FILE)

    def _reveal(n):
        opened = json.loads(
            handle_task_open(
                sid=sid, goal="create the missing weekly cron for the fetcher",
                task_type="execute", area="recambios", files=FETCH_FILE,
                plan='["create cron"]', verification_step="cron list",
                stakes="low",  # isolate the detector from the unrelated cortex gate
            )
        )
        return json.loads(
            handle_task_close(
                sid=sid, task_id=opened["task_id"], outcome="done",
                evidence=(
                    "The previous task shipped the fetch code but never created the cron "
                    "in GCloud so it never ran. Created the missing cron and confirmed it "
                    f"triggers. (pass {n})"
                ),
                change_summary="created the weekly cron the prior task forgot",
                change_why="the earlier done task omitted the cron creation step",
                change_verify="gcloud scheduler jobs list shows the job",
                correction_happened=True,
            )
        )

    first = _reveal(1)
    second = _reveal(2)
    assert first["ok"] is True and second["ok"] is True
    assert first.get("self_error", {}).get("decision") == "fire"
    # Second close must still detect (fire) but NOT create a duplicate learning row.
    se_learnings = _self_error_learnings()
    assert len(se_learnings) == 1, f"idempotency broken: {se_learnings}"
    # The re-fire is a dedup/merge (no new row): telemetry must report it as a
    # successful no-op, not a phantom failure. learning_ok stays True (deduped).
    second_se = second.get("self_error", {})
    assert second_se.get("decision") == "fire"
    assert second_se.get("learning_ok") is True, second_se


def test_integration_candidate_does_not_create_learning_or_flip_status():
    """Forgotten-step followup with NO prior done task on overlapping files →
    a low-confidence candidate (INFO debt), ZERO learnings, and the close
    must still report status 'clean' (candidate is not actionable debt)."""
    from plugins.protocol import handle_task_open, handle_task_close

    sid = _register_session("nexo-7005-2005")
    # Non-release area so the ONLY possible open debt is the self-error
    # candidate — this makes the "candidate must not flip status" assertion
    # exact (a release-area heuristic would add unrelated debt).
    opened = json.loads(
        handle_task_open(
            sid=sid, goal="set up a brand new monitor", task_type="execute",
            area="nexo-ops", files="/repo/nexo/brand_new_monitor.py",
            plan='["create"]', verification_step="manual",
            stakes="low",  # isolate the detector from the unrelated cortex gate
        )
    )
    closed = json.loads(
        handle_task_close(
            sid=sid, task_id=opened["task_id"], outcome="done",
            evidence=(
                "Set up the monitor and verified it runs. Separately noting that I "
                "previously forgot to create the cron for an unrelated brand new "
                "pipeline; tracking it as a followup to fix later."
            ),
            change_summary="set up monitor", change_why="reliability",
            change_verify="manual check",
            followup_needed=True,
            followup_description="Forgot to create the cron for the new pipeline; create it.",
        )
    )
    assert closed["ok"] is True, f"close blocked: {closed}"
    # No prior done task on overlapping files → no fire, no learning.
    assert _self_error_learnings() == []
    se = closed.get("self_error")
    if se is not None:
        assert se["decision"] != "fire"
    # An INFO candidate debt (if recorded) must not flip the close into
    # done_with_debts — the candidate is an informational signal, not debt.
    assert closed["status"] == "clean", f"candidate polluted status: {closed}"
