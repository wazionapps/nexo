"""Contract tests for scripts/semantic-classify.py — JSON-in JSON-out CLI.

The CLI is the Brain-side endpoint for the Desktop bridge. It must:

  - Reject empty / malformed input with exit code 1 and a JSON payload
    that says ``ok: false`` so the Desktop parser never chokes.
  - Accept well-formed input and emit a JSON payload that mirrors the
    ``RouterResult`` dataclass.
  - Never require a running MCP server or database connection.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "semantic-classify.py"


def _run_cli(payload: str) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(CLI)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def test_cli_rejects_empty_stdin():
    code, out, _err = _run_cli("")
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert "empty" in (data["error"] or "")


def test_cli_rejects_invalid_json():
    code, out, _err = _run_cli("{not valid json")
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert "invalid JSON" in (data["error"] or "")


def test_cli_rejects_non_object_payload():
    code, out, _err = _run_cli(json.dumps(["a", "list", "not", "an", "object"]))
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert "must be a JSON object" in (data["error"] or "")


def test_cli_rejects_missing_decision_kind():
    code, out, _err = _run_cli(json.dumps({"question": "anything"}))
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert "decision_kind" in (data["error"] or "")


def test_cli_rejects_missing_question():
    code, out, _err = _run_cli(json.dumps({"decision_kind": "session_end_intent"}))
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert "question" in (data["error"] or "")


def test_cli_returns_structured_failure_for_unknown_kind():
    code, out, _err = _run_cli(
        json.dumps({"decision_kind": "made_up_kind", "question": "anything"})
    )
    # Well-formed input always exits 0 so Desktop can parse the body.
    assert code == 0
    data = json.loads(out)
    assert data["ok"] is False
    assert data["decision_kind"] == "made_up_kind"
    assert data["route_used"] == "no_route"
    assert data["degraded"] is True
    assert "unknown decision_kind" in (data["error"] or "")


def test_cli_passes_labels_through_to_router():
    code, out, _err = _run_cli(
        json.dumps(
            {
                "decision_kind": "r16_declared_done",
                "question": "ya está",
                "labels": ["done_claim", "noise"],
                "allow_remote_fallback": False,
            }
        )
    )
    assert code == 0
    data = json.loads(out)
    assert data["decision_kind"] == "r16_declared_done"
    # allow_remote_fallback=False + unavailable local layers (no HF model
    # installed in CI) means the router falls through to no_route.
    assert data["route_used"] in {"fast_local", "semantic_reasoner", "no_route"}


def test_cli_output_shape_matches_router_result():
    code, out, _err = _run_cli(
        json.dumps({"decision_kind": "session_end_intent", "question": "hasta mañana"})
    )
    assert code == 0
    data = json.loads(out)
    required_keys = {
        "ok",
        "decision_kind",
        "verdict",
        "label",
        "confidence",
        "route_used",
        "degraded",
        "error",
        "meta",
    }
    assert required_keys.issubset(set(data.keys()))
    assert isinstance(data["meta"], dict)
    assert isinstance(data["confidence"], (int, float))
