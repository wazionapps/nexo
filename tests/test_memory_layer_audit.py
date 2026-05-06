from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_memory_layer_audit_flags_legacy_memory_and_profile_conflict(tmp_path):
    from memory_layer_audit import audit_memory_layers, format_memory_layer_warnings

    home = tmp_path / "home"
    nexo_home = home / ".nexo"
    brain = nexo_home / "brain"
    brain.mkdir(parents=True)
    (brain / "profile.json").write_text(
        json.dumps({"current_residence": {"city": "Tenerife", "country": "Spain"}}),
        encoding="utf-8",
    )
    (brain / "calibration.json").write_text(json.dumps({"user": {"language": "es"}}), encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "MEMORY.md").write_text("old memory", encoding="utf-8")
    (home / ".claude" / "CLAUDE.md").write_text(
        "Maria lives in Llucmajor, Mallorca.\n",
        encoding="utf-8",
    )

    report = audit_memory_layers(home=home, nexo_home=nexo_home)

    warning_types = {warning["type"] for warning in report["warnings"]}
    assert "legacy_client_memory_present" in warning_types
    assert "possible_identity_location_conflict" in warning_types
    lines = format_memory_layer_warnings(report)
    assert any("Legacy MEMORY present" in line for line in lines)
    assert any("Possible profile conflict" in line for line in lines)


def test_memory_layer_audit_stays_quiet_when_only_canonical_sources_exist(tmp_path):
    from memory_layer_audit import audit_memory_layers

    home = tmp_path / "home"
    nexo_home = home / ".nexo"
    brain = nexo_home / "brain"
    brain.mkdir(parents=True)
    (brain / "profile.json").write_text(
        json.dumps({"current_residence": "Tenerife"}),
        encoding="utf-8",
    )

    report = audit_memory_layers(home=home, nexo_home=nexo_home)

    assert report["warnings"] == []
