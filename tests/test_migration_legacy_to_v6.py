"""v6.0.0 — Verifies apply_v6_purge() drops every legacy v5.10 field.

Fixture simulates a calibration.json/schedule.json pair from a v5.10
installation with:
  - preferences.protocol_strictness = "off"
  - client_runtime_profiles.claude_code.model = "opus-4-6"
  - client_runtime_profiles.claude_code.reasoning_effort = "xhigh"
  - preferences.show_pending_at_start = True

After migration we expect:
  - protocol_strictness gone from calibration
  - show_pending_at_start gone from calibration
  - model/reasoning_effort gone from both client_runtime_profiles entries
  - preferences.default_resonance seeded to "alto" (since none was set)
  - Every other field untouched
  - An already-set default_resonance is NEVER overwritten on a second run
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from calibration_migration import apply_v6_purge  # noqa: E402


def _seed_v5_home(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    config = tmp_path / "config"
    brain.mkdir()
    config.mkdir()
    (brain / "calibration.json").write_text(json.dumps({
        "version": 1,
        "user": {"name": "Francisco"},
        "personality": {"autonomy": "full"},
        "preferences": {
            "protocol_strictness": "off",
            "show_pending_at_start": True,
            "menu_on_demand": True,
        },
    }, indent=2))
    (config / "schedule.json").write_text(json.dumps({
        "timezone": "Europe/Madrid",
        "client_runtime_profiles": {
            "claude_code": {"model": "opus-4-6", "reasoning_effort": "xhigh"},
            "codex":       {"model": "gpt-5.4",  "reasoning_effort": "high"},
        },
        "automation_enabled": True,
    }, indent=2))
    return tmp_path


def test_purge_removes_all_legacy_fields_and_seeds_default_resonance(tmp_path):
    home = _seed_v5_home(tmp_path)
    result = apply_v6_purge(nexo_home=home)

    cal = json.loads((home / "brain" / "calibration.json").read_text())
    sched = json.loads((home / "config" / "schedule.json").read_text())

    # Legacy keys gone
    assert "protocol_strictness" not in cal.get("preferences", {})
    assert "show_pending_at_start" not in cal.get("preferences", {})
    for client in ("claude_code", "codex"):
        assert "model" not in sched["client_runtime_profiles"][client]
        assert "reasoning_effort" not in sched["client_runtime_profiles"][client]

    # New canonical fields present
    assert cal["preferences"]["default_resonance"] == "alto"

    # Untouched surroundings
    assert cal["user"]["name"] == "Francisco"
    assert cal["personality"]["autonomy"] == "full"
    assert cal["preferences"]["menu_on_demand"] is True
    assert sched["timezone"] == "Europe/Madrid"
    assert sched["automation_enabled"] is True

    assert result["status"] == "migrated"
    assert result["calibration_changed"] is True
    assert result["schedule_changed"] is True
    assert result["seeded_default_resonance"] is True


def test_existing_default_resonance_is_never_overwritten(tmp_path):
    home = _seed_v5_home(tmp_path)
    cal_path = home / "brain" / "calibration.json"
    cal = json.loads(cal_path.read_text())
    cal["preferences"]["default_resonance"] = "maximo"
    cal_path.write_text(json.dumps(cal, indent=2))

    apply_v6_purge(nexo_home=home)

    cal_after = json.loads(cal_path.read_text())
    assert cal_after["preferences"]["default_resonance"] == "maximo"


def test_purge_is_idempotent(tmp_path):
    home = _seed_v5_home(tmp_path)
    first = apply_v6_purge(nexo_home=home)
    second = apply_v6_purge(nexo_home=home)

    assert first["status"] == "migrated"
    assert second["status"] == "noop"
    # default_resonance must not be rewritten on the second pass
    assert second["seeded_default_resonance"] is False


def test_purge_survives_missing_files(tmp_path):
    # No calibration and no schedule — must return noop, not raise.
    result = apply_v6_purge(nexo_home=tmp_path)
    assert result["status"] == "noop"
    assert result["calibration_changed"] is False
    assert result["schedule_changed"] is False


def test_purge_handles_top_level_protocol_strictness(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "calibration.json").write_text(json.dumps({
        "protocol_strictness": "warn",  # legacy top-level placement
        "preferences": {},
    }))
    apply_v6_purge(nexo_home=tmp_path)
    cal = json.loads((brain / "calibration.json").read_text())
    assert "protocol_strictness" not in cal
