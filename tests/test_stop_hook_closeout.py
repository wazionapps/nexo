from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_stop_scan_blocks_future_commitment_without_followup():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"role": "assistant", "text": "Lo dejo como seguimiento porque está bloqueado por auth."}),
    ])

    assert result["ok"] is False
    assert result["missing_followups"] == 1


def test_stop_scan_accepts_commitment_with_followup_create():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"role": "assistant", "text": "Lo dejo pendiente: revisar el deploy."}),
        json.dumps({"tool": "nexo_followup_create", "input": {"id": "NF-DEPLOY"}}),
    ])

    assert result["ok"] is True
    assert result["followup_creates"] == 1


def test_stop_scan_ignores_bare_generic_words():
    from hooks.stop import scan_closeout_followup_gaps

    # Bare adverbs ("después", "pendiente", "cuando quieras") used to false-positive
    # and block a perfectly clean close. They must NOT trigger on their own.
    result = scan_closeout_followup_gaps([
        json.dumps({"role": "assistant", "text": "Lo reviso después; el informe está pendiente de números pero ya te lo paso ahora."}),
        json.dumps({"role": "assistant", "text": "Cuando quieras te enseño el resultado final."}),
    ])

    assert result["ok"] is True
    assert result["missing_followups"] == 0


def test_stop_scope_to_session_excludes_other_sessions():
    from hooks.stop import _scope_to_session

    lines = [
        json.dumps({"session_id": "S1", "text": "lo dejo como seguimiento"}),
        json.dumps({"session_id": "S2", "text": "lo dejo como seguimiento"}),
    ]
    scoped = _scope_to_session(lines, "S1")
    assert len(scoped) == 1 and "S1" in scoped[0]


def test_stop_scope_falls_back_when_no_session_ids():
    from hooks.stop import _scope_to_session

    lines = [json.dumps({"text": "lo dejo como seguimiento"})]
    # No session ids in the buffer → cannot scope → keep every line (prior behaviour).
    assert _scope_to_session(lines, "S1") == lines


def test_stop_scan_counts_partial_task_close_as_followup_required():
    from hooks.stop import scan_closeout_followup_gaps

    result = scan_closeout_followup_gaps([
        json.dumps({"tool": "nexo_task_close", "tool_input": {"outcome": "partial"}}),
    ])

    assert result["ok"] is False
    assert result["findings"][0]["kind"] == "partial_task_close"


def test_thinking_block_recovery_detects_error_payload():
    from hooks.stop import detect_thinking_block_recovery_needed

    result = detect_thinking_block_recovery_needed(
        [],
        {
            "error": {
                "message": (
                    "Error 400 invalid_request_error: thinking or redacted_thinking "
                    "blocks cannot be modified"
                )
            }
        },
    )

    assert result["match"] is True
    assert result["source"] == "payload"


def test_thinking_block_recovery_ignores_user_prompt_mentions():
    from hooks.stop import detect_thinking_block_recovery_needed

    result = detect_thinking_block_recovery_needed([
        json.dumps({
            "role": "user",
            "text": "Implementa el fix para error 400: thinking blocks cannot be modified.",
        }),
    ])

    assert result["match"] is False


def test_thinking_block_recovery_detects_system_transcript_error():
    from hooks.stop import detect_thinking_block_recovery_needed

    result = detect_thinking_block_recovery_needed([
        json.dumps({
            "role": "system",
            "text": (
                "400 Bad Request: redacted_thinking blocks cannot be modified "
                "after a message is submitted."
            ),
        }),
    ])

    assert result["match"] is True
    assert result["source"] == "transcript:1"
