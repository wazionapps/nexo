"""Tests for auto_update._migrate_effort_to_resonance.

v5.9.0 introduced the resonance map + ``preferences.default_resonance``
(``maximo`` / ``alto`` / ``medio`` / ``bajo``). v5.10.0 made the
resonance map prevail over the legacy ``client_runtime_profiles.claude_code.reasoning_effort``
setting. Users whose only recorded preference was the legacy effort
silently fell back to ``DEFAULT_RESONANCE="alto"`` even if they had set
``reasoning_effort="max"`` before. v5.10.1 adds this migration to
recover the choice, exactly once, non-destructively.

The function must be:
  - idempotent (second call is a no-op)
  - conservative (never override an explicit default_resonance)
  - safe (swallow errors, never raise)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_auto_update(monkeypatch, home: Path):
    import importlib
    monkeypatch.setenv("NEXO_HOME", str(home))
    import auto_update as au
    importlib.reload(au)
    return au


def _seed_schedule(home: Path, effort: str | None = None,
                   default_resonance: str | None = None) -> None:
    p = home / "config" / "schedule.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if effort is not None:
        payload["client_runtime_profiles"] = {
            "claude_code": {"model": "claude-opus-4-7[1m]", "reasoning_effort": effort}
        }
    if default_resonance is not None:
        payload["default_resonance"] = default_resonance
    p.write_text(json.dumps(payload))


def _seed_calibration(home: Path, default_resonance: str | None = None) -> None:
    p = home / "brain" / "calibration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if default_resonance is not None:
        payload["preferences"] = {"default_resonance": default_resonance}
    p.write_text(json.dumps(payload))


def _load_calibration_tier(home: Path) -> str:
    p = home / "brain" / "calibration.json"
    if not p.exists():
        return ""
    data = json.loads(p.read_text())
    prefs = data.get("preferences") if isinstance(data, dict) else None
    if not isinstance(prefs, dict):
        return ""
    return str(prefs.get("default_resonance") or "").strip()


def test_migrates_max_to_maximo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="max")
    # No calibration pref set → must migrate
    au = _reload_auto_update(monkeypatch, home)
    actions = au._migrate_effort_to_resonance(home)
    assert any("max->maximo" in a for a in actions)
    assert _load_calibration_tier(home) == "maximo"


def test_migrates_xhigh_to_alto(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="xhigh")
    au = _reload_auto_update(monkeypatch, home)
    au._migrate_effort_to_resonance(home)
    assert _load_calibration_tier(home) == "alto"


def test_migrates_high_to_medio(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="high")
    au = _reload_auto_update(monkeypatch, home)
    au._migrate_effort_to_resonance(home)
    assert _load_calibration_tier(home) == "medio"


def test_migrates_medium_to_bajo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="medium")
    au = _reload_auto_update(monkeypatch, home)
    au._migrate_effort_to_resonance(home)
    assert _load_calibration_tier(home) == "bajo"


def test_noop_when_calibration_pref_already_set(tmp_path, monkeypatch):
    """If the user has already chosen a tier through the Desktop UI or the
    CLI (calibration.json), migration must NOT touch it."""
    home = tmp_path / "home"
    _seed_schedule(home, effort="max")
    _seed_calibration(home, default_resonance="bajo")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._migrate_effort_to_resonance(home)
    assert actions == []
    assert _load_calibration_tier(home) == "bajo"  # untouched


def test_noop_when_schedule_pref_already_set(tmp_path, monkeypatch):
    """Same principle for the schedule.json legacy CLI location."""
    home = tmp_path / "home"
    _seed_schedule(home, effort="max", default_resonance="medio")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._migrate_effort_to_resonance(home)
    assert actions == []
    assert _load_calibration_tier(home) == ""  # no calibration pref written


def test_noop_when_no_effort_hint(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "brain").mkdir(parents=True)
    (home / "config" / "schedule.json").write_text("{}")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._migrate_effort_to_resonance(home)
    assert actions == []


def test_idempotent_second_run_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="max")
    au = _reload_auto_update(monkeypatch, home)

    first = au._migrate_effort_to_resonance(home)
    assert any("max->maximo" in a for a in first)

    # Second call should detect the already-set preference and do nothing.
    second = au._migrate_effort_to_resonance(home)
    assert second == []
    assert _load_calibration_tier(home) == "maximo"


def test_unknown_effort_is_skipped_safely(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="custom_effort_value")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._migrate_effort_to_resonance(home)
    assert actions == []
    assert _load_calibration_tier(home) == ""


def test_swallows_corrupt_calibration_json(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_schedule(home, effort="max")
    # Corrupt calibration.json
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "brain" / "calibration.json").write_text("{not json")
    au = _reload_auto_update(monkeypatch, home)
    # Should still migrate and overwrite the corrupt file.
    actions = au._migrate_effort_to_resonance(home)
    assert any("max->maximo" in a for a in actions)
    assert _load_calibration_tier(home) == "maximo"
