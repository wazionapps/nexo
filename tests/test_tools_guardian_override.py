"""Tests for tools_guardian — Plan Consolidado 0.17 writer.

NEXO_HOME is forced to tmp_path so these tests never touch the real
~/.nexo/ directory. Honours learning #437.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tools_guardian  # noqa: E402
from tools_guardian import (  # noqa: E402
    GuardianOverrideError,
    VALID_TTLS,
    clear_guardian_rule_override,
    handle_guardian_rule_override,
    set_guardian_rule_override,
)


@pytest.fixture(autouse=True)
def _isolate_nexo_home(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    yield


def test_set_override_writes_file_with_expected_shape():
    out = set_guardian_rule_override("R15_project_context", "shadow", "1h")
    assert out["ok"] is True
    assert out["rule_id"] == "R15_project_context"
    assert out["mode"] == "shadow"
    assert out["ttl_label"] == "1h"
    assert out["expires_at"] > time.time()
    path = Path(out["path"])
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["R15_project_context"]["mode"] == "shadow"


def test_core_rule_cannot_be_turned_off():
    with pytest.raises(GuardianOverrideError) as exc:
        set_guardian_rule_override("R13_pre_edit_guard", "off", "1h")
    assert "cannot be set to 'off'" in str(exc.value)


def test_invalid_mode_is_rejected():
    with pytest.raises(GuardianOverrideError):
        set_guardian_rule_override("R17_promise_debt", "nuclear", "1h")


def test_invalid_ttl_is_rejected():
    with pytest.raises(GuardianOverrideError):
        set_guardian_rule_override("R17_promise_debt", "shadow", "7d")


def test_set_then_clear_override_removes_entry(tmp_path):
    set_guardian_rule_override("R19_project_grep", "soft", "24h")
    out = clear_guardian_rule_override("R19_project_grep")
    assert out["cleared"] is True
    path = Path(out["path"])
    data = json.loads(path.read_text())
    assert "R19_project_grep" not in data


def test_clear_nonexistent_is_idempotent():
    out = clear_guardian_rule_override("R99_does_not_exist")
    assert out["ok"] is True
    assert out["cleared"] is False


def test_handle_tool_json_shape_on_success():
    raw = handle_guardian_rule_override("R17_promise_debt", "hard", "24h")
    parsed = json.loads(raw)
    assert parsed["ok"] is True
    assert parsed["mode"] == "hard"
    assert parsed["ttl_label"] == "24h"


def test_handle_tool_returns_structured_error_on_bad_mode():
    raw = handle_guardian_rule_override("R17_promise_debt", "nuclear", "24h")
    parsed = json.loads(raw)
    assert parsed["ok"] is False
    assert "invalid mode" in parsed["error"].lower()


def test_ttl_session_is_bounded_by_12h():
    out = set_guardian_rule_override("R17_promise_debt", "shadow", "session")
    delta = out["expires_at"] - time.time()
    assert 12 * 3600 - 30 < delta <= 12 * 3600 + 1


def test_valid_ttls_table_matches_spec():
    assert VALID_TTLS == {"1h": 3600, "24h": 86400, "session": 12 * 3600}


def test_log_file_accumulates_ndjson():
    set_guardian_rule_override("R17_promise_debt", "shadow", "1h")
    clear_guardian_rule_override("R17_promise_debt")
    log = Path(tools_guardian._log_path())
    assert log.is_file()
    lines = log.read_text().strip().split("\n")
    events = [json.loads(l) for l in lines]
    assert {e["event"] for e in events} == {"override_set", "override_clear"}
