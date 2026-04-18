"""Tests for src/resonance_map.py — the central tier/caller registry.

Guard rails:
    - Every caller registered in the map resolves to a valid tier.
    - User-facing callers honour the user's default; system-owned ignore it.
    - Unknown callers raise UnregisteredCallerError (no silent fallback).
    - If a future backend drops effort tiers, model+effort resolution still
      returns something sane rather than raising.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

import resonance_map as rmap


def test_all_tiers_cover_both_backends():
    for tier in rmap.TIERS:
        entry = rmap._RESONANCE_TABLE[tier]
        assert "claude_code" in entry and "codex" in entry
        model_c, effort_c = entry["claude_code"]
        assert model_c
        model_x, effort_x = entry["codex"]
        assert model_x


def test_user_facing_callers_use_user_default():
    model_a, effort_a = rmap.resolve_model_and_effort(
        "nexo_chat", "claude_code", user_default="maximo"
    )
    model_b, effort_b = rmap.resolve_model_and_effort(
        "nexo_chat", "claude_code", user_default="bajo"
    )
    assert effort_a != effort_b  # the user's default does affect the outcome
    # Sanity: maximo actually resolves to the maximum effort defined.
    assert (model_a, effort_a) == rmap._RESONANCE_TABLE["maximo"]["claude_code"]
    assert (model_b, effort_b) == rmap._RESONANCE_TABLE["bajo"]["claude_code"]


def test_user_facing_caller_with_invalid_user_default_falls_back():
    model, effort = rmap.resolve_model_and_effort(
        "nexo_chat", "claude_code", user_default="garbage"
    )
    assert (model, effort) == rmap._RESONANCE_TABLE[rmap.DEFAULT_RESONANCE]["claude_code"]


def test_user_facing_caller_with_no_user_default_uses_alto(monkeypatch):
    # Isolate from the real user calibration.json on the machine running
    # the suite — without this, the operator's "maximo" preference bleeds in
    # and the assertion fails.
    monkeypatch.setattr(rmap, "_load_user_default_resonance", lambda: "")
    model, effort = rmap.resolve_model_and_effort("nexo_chat", "claude_code")
    assert (model, effort) == rmap._RESONANCE_TABLE["alto"]["claude_code"]


def test_system_owned_caller_ignores_user_default():
    """deep-sleep/extract is fixed at ALTO; the user asking for BAJO must
    not downgrade the deep-sleep run below what we decided it needs."""
    model, effort = rmap.resolve_model_and_effort(
        "deep-sleep/extract", "claude_code", user_default="bajo"
    )
    assert (model, effort) == rmap._RESONANCE_TABLE["alto"]["claude_code"]


def test_system_owned_synthesize_is_maximo():
    """synthesize consolidates findings across every session and benefits
    from the most reasoning budget we have. Locked at MAXIMO."""
    tier = rmap.resolve_tier_for_caller("deep-sleep/synthesize")
    assert tier == "maximo"


def test_evolution_is_maximo():
    tier = rmap.resolve_tier_for_caller("evolution/run")
    assert tier == "maximo"


def test_unknown_caller_raises():
    with pytest.raises(rmap.UnregisteredCallerError):
        rmap.resolve_model_and_effort("made_up_caller", "claude_code")


def test_empty_caller_raises():
    with pytest.raises(rmap.UnregisteredCallerError):
        rmap.resolve_model_and_effort("", "claude_code")
    with pytest.raises(rmap.UnregisteredCallerError):
        rmap.resolve_tier_for_caller("")


def test_unknown_backend_returns_empty_pair():
    """Unknown backends fall back to empty strings rather than raising so
    callers can still decide whether to error or carry on with defaults."""
    model, effort = rmap.resolve_model_and_effort(
        "deep-sleep/extract", "hypothetical_new_backend"
    )
    assert model == ""
    assert effort == ""


def test_register_and_unregister_system_caller_roundtrip():
    rmap.register_system_caller("test/synthetic_caller", "medio")
    try:
        tier = rmap.resolve_tier_for_caller("test/synthetic_caller")
        assert tier == "medio"
    finally:
        rmap.unregister_system_caller("test/synthetic_caller")
    with pytest.raises(rmap.UnregisteredCallerError):
        rmap.resolve_tier_for_caller("test/synthetic_caller")


def test_register_with_invalid_tier_raises():
    with pytest.raises(ValueError):
        rmap.register_system_caller("test/bad", "ultra")


def test_default_resonance_is_alto():
    """The documented default is ALTO. Changing this is a user-visible
    behaviour shift and should require an explicit decision."""
    assert rmap.DEFAULT_RESONANCE == "alto"


def test_user_facing_registry_is_small():
    """Only these entry points should ever honour the user default:
    terminal chat, Desktop new session, the interactive updater, and the
    dashboard's "Open followup in Terminal" launcher (v6.0.4+).
    Any additions to this list are a design change that needs review."""
    assert set(rmap.USER_FACING_CALLERS.keys()) == {
        "nexo_chat",
        "desktop_new_session",
        "nexo_update_interactive",
        "nexo_followup_terminal",
    }


def test_load_user_default_reads_from_calibration_first(tmp_path, monkeypatch):
    """The preferences UI in NEXO Desktop writes to
    ``brain/calibration.json``; the CLI also writes to
    ``config/schedule.json``. When both exist the calibration value wins
    (it is the source the UI shows the user)."""
    import json as _json
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True)
    (home / "config").mkdir()
    (home / "brain" / "calibration.json").write_text(
        _json.dumps({"preferences": {"default_resonance": "medio"}})
    )
    (home / "config" / "schedule.json").write_text(
        _json.dumps({"default_resonance": "bajo"})
    )
    monkeypatch.setenv("NEXO_HOME", str(home))
    tier = rmap._load_user_default_resonance()
    assert tier == "medio"


def test_load_user_default_falls_back_to_schedule(tmp_path, monkeypatch):
    import json as _json
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "schedule.json").write_text(
        _json.dumps({"default_resonance": "bajo"})
    )
    monkeypatch.setenv("NEXO_HOME", str(home))
    assert rmap._load_user_default_resonance() == "bajo"


def test_load_user_default_returns_empty_when_neither_source(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-empty"))
    assert rmap._load_user_default_resonance() == ""


def test_load_user_default_rejects_invalid_tier(tmp_path, monkeypatch):
    """Garbage in calibration.json shouldn't downgrade to DEFAULT silently
    — it returns empty so callers can fall back deliberately."""
    import json as _json
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True)
    (home / "brain" / "calibration.json").write_text(
        _json.dumps({"preferences": {"default_resonance": "ultra-high"}})
    )
    monkeypatch.setenv("NEXO_HOME", str(home))
    assert rmap._load_user_default_resonance() == ""


def test_resolve_tier_for_caller_picks_up_calibration(tmp_path, monkeypatch):
    """nexo_chat with no explicit user_default must read calibration.json
    so that whatever Desktop wrote takes effect without any CLI argument."""
    import json as _json
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True)
    (home / "brain" / "calibration.json").write_text(
        _json.dumps({"preferences": {"default_resonance": "maximo"}})
    )
    monkeypatch.setenv("NEXO_HOME", str(home))
    tier = rmap.resolve_tier_for_caller("nexo_chat", user_default=None)
    assert tier == "maximo"


def test_explicit_user_default_still_overrides_calibration(tmp_path, monkeypatch):
    """If the caller passes user_default explicitly that wins over the file
    — the per-call override path stays intact for CLI flags like
    `nexo chat --resonance max`."""
    import json as _json
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True)
    (home / "brain" / "calibration.json").write_text(
        _json.dumps({"preferences": {"default_resonance": "bajo"}})
    )
    monkeypatch.setenv("NEXO_HOME", str(home))
    tier = rmap.resolve_tier_for_caller("nexo_chat", user_default="maximo")
    assert tier == "maximo"
