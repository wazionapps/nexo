"""Regression tests for the Ola 0 critical fixes (2026-06-14).

Covers: co-activation determinism, auto error->learning capture, the
guard_context fake-evidence security hole, and the confidence_checks
answer-contract fulfillment table.
"""
import hashlib
import json


# --- #1 co-activation: deterministic, process-stable id (not builtin hash) ---

def test_canonical_co_id_is_process_stable_not_builtin_hash():
    from cognitive._search import _canonical_co_id

    expected = int.from_bytes(
        hashlib.blake2b(b"stm:1", digest_size=8).digest(), "big"
    ) % (2 ** 31)
    assert _canonical_co_id("stm", 1) == expected
    # stable on repeated calls and distinct per (store, mid)
    assert _canonical_co_id("stm", 1) == _canonical_co_id("stm", 1)
    assert _canonical_co_id("stm", 1) != _canonical_co_id("ltm", 1)


# --- #3 auto error->learning capture (handle_learning_add, not add_learning) ---

def test_auto_learning_add_persists_real_learning():
    import tools_learnings
    from hooks.auto_capture import _auto_learning_add
    from db import get_db

    # The public symbol must exist (the bug called a non-existent add_learning).
    assert hasattr(tools_learnings, "handle_learning_add")
    assert not hasattr(tools_learnings, "add_learning")

    ok = _auto_learning_add(
        "Ola0 auto-capture regression",
        "verifies corrections persist a durable learning via handle_learning_add",
    )
    assert ok is True
    rows = get_db().execute(
        "SELECT COUNT(*) FROM learnings WHERE title = ?",
        ("Ola0 auto-capture regression",),
    ).fetchone()[0]
    assert rows >= 1


# --- #2 guard_context: never fake evidence; surface real conditioned learnings ---

def test_guard_context_no_files_returns_empty():
    import pre_answer_router as par

    req = par.SourceRequest(query="", intent="modify_existing", files="")
    res = par._source_guard_context(req)
    assert res.result_count == 0
    assert not res.evidence_refs


def test_guard_context_does_not_fake_evidence_when_clean():
    import pre_answer_router as par

    req = par.SourceRequest(
        query="", intent="modify_existing", files="/tmp/nexo_ola0/none.py"
    )
    res = par._source_guard_context(req)
    # The old stub returned evidence_refs=["guard_context:requested"], result_count=1
    # WITHOUT any check. That must never happen again.
    assert "guard_context:requested" not in (res.evidence_refs or [])
    assert res.result_count == 0
    assert "verified" in res.rendered.lower()


def test_guard_context_surfaces_real_conditioned_learning():
    import tools_learnings
    import pre_answer_router as par

    target = "/tmp/nexo_ola0/guarded_file.py"
    tools_learnings.handle_learning_add(
        category="testing",
        title="Ola0 guard conditioned rule",
        content="must surface as guard_context evidence",
        applies_to=target,
        priority="high",
    )
    req = par.SourceRequest(query="", intent="modify_existing", files=target)
    res = par._source_guard_context(req)
    assert res.result_count >= 1
    assert any(ref.startswith("learning:") for ref in res.evidence_refs)
    assert "guard_context:requested" not in (res.evidence_refs or [])


# --- #4 confidence_checks: handle_confidence_check persists; g1 detects fulfillment ---

def test_confidence_check_persists_row_and_g1_detects_fulfillment():
    from plugins import protocol
    from hooks.g1_enforcer import _has_followup_event_since
    from db import get_db

    conn = get_db()
    sid = "nexo-test-cc-ola0"
    conn.execute(
        "INSERT INTO sessions (sid, task, started_epoch, last_update_epoch) VALUES (?, ?, ?, ?)",
        (sid, "ola0 cc test", 1.0, 2.0),
    )
    conn.commit()

    out = json.loads(protocol.handle_confidence_check(
        goal="Should I claim this is done without evidence?",
        task_type="answer",
        sid=sid,
    ))
    assert out["ok"] is True

    n = conn.execute(
        "SELECT COUNT(*) FROM confidence_checks WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    assert n == 1

    # g1 must now see the contract as fulfillable for this session.
    assert _has_followup_event_since(conn, "confidence_checks", sid, "2000-01-01 00:00:00") is True


def test_confidence_checks_table_exists_after_migrations():
    from db import get_db

    row = get_db().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='confidence_checks'"
    ).fetchone()
    assert row is not None
