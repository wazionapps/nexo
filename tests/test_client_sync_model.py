"""Tests for client_sync.sync_claude_code_model.

Claude Code reads the default model from ~/.claude/settings.json, not from
NEXO's internal client_runtime_profiles. When a NEXO default changes, this
helper must propagate the new model — but only conservatively: never seed
a "model" field the user did not already have.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from client_sync import sync_claude_code_model  # noqa: E402


def _settings_path(user_home: Path) -> Path:
    return user_home / ".claude" / "settings.json"


def _write_settings(user_home: Path, payload: dict) -> Path:
    path = _settings_path(user_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def test_updates_model_when_field_present(tmp_path):
    path = _write_settings(tmp_path, {
        "model": "claude-opus-4-6[1m]",
        "permissions": {"allow": ["Bash"]},
    })

    result = sync_claude_code_model("claude-opus-4-7[1m]", user_home=tmp_path)

    assert result["ok"] is True
    assert result["action"] == "updated"
    assert result["previous_model"] == "claude-opus-4-6[1m]"
    assert result["new_model"] == "claude-opus-4-7[1m]"
    payload = json.loads(path.read_text())
    assert payload["model"] == "claude-opus-4-7[1m]"
    # Must preserve unrelated fields.
    assert payload["permissions"] == {"allow": ["Bash"]}


def test_skips_when_settings_missing(tmp_path):
    result = sync_claude_code_model("claude-opus-4-7[1m]", user_home=tmp_path)

    assert result["ok"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "settings.json missing"
    # Must NOT create the file.
    assert not _settings_path(tmp_path).exists()


def test_skips_when_no_model_field(tmp_path):
    """User never opted in to NEXO managing their model → do not seed it."""
    path = _write_settings(tmp_path, {"permissions": {"allow": ["Bash"]}})

    result = sync_claude_code_model("claude-opus-4-7[1m]", user_home=tmp_path)

    assert result["ok"] is True
    assert result["action"] == "skipped"
    assert result["reason"] == "no model field in settings.json"
    payload = json.loads(path.read_text())
    assert "model" not in payload


def test_skips_when_already_matches(tmp_path):
    """No-op when model is already correct — avoid spurious rewrites/mtime bumps."""
    path = _write_settings(tmp_path, {"model": "claude-opus-4-7[1m]"})
    mtime_before = path.stat().st_mtime_ns

    result = sync_claude_code_model("claude-opus-4-7[1m]", user_home=tmp_path)

    assert result["action"] == "skipped"
    assert result["reason"] == "already matches"
    assert path.stat().st_mtime_ns == mtime_before


def test_skips_empty_model_arg(tmp_path):
    _write_settings(tmp_path, {"model": "claude-opus-4-6[1m]"})

    result = sync_claude_code_model("", user_home=tmp_path)

    assert result["action"] == "skipped"
    assert result["reason"] == "empty model"
    # Original value untouched.
    payload = json.loads(_settings_path(tmp_path).read_text())
    assert payload["model"] == "claude-opus-4-6[1m]"


def test_handles_malformed_json_gracefully(tmp_path):
    path = _settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json")

    result = sync_claude_code_model("claude-opus-4-7[1m]", user_home=tmp_path)

    assert result["ok"] is False
    assert result["action"] == "skipped"
    assert "read failed" in result["reason"]
    # Must not overwrite the malformed file.
    assert path.read_text() == "{ this is not json"
