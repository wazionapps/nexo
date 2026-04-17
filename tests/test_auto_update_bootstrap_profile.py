"""Tests for auto_update._bootstrap_profile_from_calibration_meta.

v5.10.2 auto-creates ``brain/profile.json`` when it does not exist yet and the
operator has at least one of ``meta.role``, ``meta.technical_level``,
``name``, ``language`` recorded in ``brain/calibration.json``. The function
must be idempotent, conservative, and swallow all errors — the update path
can never raise because of this code.
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


def _seed_calibration(home: Path, payload: dict | None) -> None:
    p = home / "brain" / "calibration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        return
    p.write_text(json.dumps(payload, ensure_ascii=False))


def _load_profile(home: Path) -> dict | None:
    p = home / "brain" / "profile.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def test_bootstraps_from_meta_role_and_technical_level(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {"meta": {"role": "founder", "technical_level": "advanced"}})
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert any("profile-bootstrap:" in a for a in actions)
    prof = _load_profile(home)
    assert prof is not None
    assert prof["role"] == "founder"
    assert prof["technical_level"] == "advanced"
    assert prof["source"] == "auto_update._bootstrap_profile_from_calibration_meta"


def test_includes_name_and_language_when_present(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {
        "name": "Francisco",
        "language": "es",
        "meta": {"role": "founder"},
    })
    au = _reload_auto_update(monkeypatch, home)
    au._bootstrap_profile_from_calibration_meta(home)
    prof = _load_profile(home)
    assert prof["name"] == "Francisco"
    assert prof["language"] == "es"
    assert prof["role"] == "founder"


def test_noop_when_profile_already_has_content(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {"meta": {"role": "founder"}})
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "brain" / "profile.json").write_text(json.dumps({"role": "existing"}))
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert actions == []
    assert _load_profile(home)["role"] == "existing"  # untouched


def test_rewrites_empty_profile_when_calibration_has_data(tmp_path, monkeypatch):
    """An empty {} profile is treated as "not bootstrapped yet" and replaced."""
    home = tmp_path / "home"
    _seed_calibration(home, {"meta": {"role": "founder"}})
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "brain" / "profile.json").write_text("{}")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert any("profile-bootstrap:" in a for a in actions)
    assert _load_profile(home)["role"] == "founder"


def test_noop_when_no_calibration_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert actions == []
    assert _load_profile(home) is None


def test_noop_when_calibration_has_no_useful_fields(tmp_path, monkeypatch):
    """Calibration without role, technical_level, name, or language → no seed."""
    home = tmp_path / "home"
    _seed_calibration(home, {"autonomy": "balanced", "communication": "concise"})
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert actions == []
    assert _load_profile(home) is None


def test_noop_when_calibration_is_corrupt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "brain").mkdir(parents=True)
    (home / "brain" / "calibration.json").write_text("{not json")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert actions == []
    assert _load_profile(home) is None


def test_idempotent_second_run_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {"meta": {"role": "founder"}})
    au = _reload_auto_update(monkeypatch, home)

    first = au._bootstrap_profile_from_calibration_meta(home)
    assert any("profile-bootstrap:" in a for a in first)

    second = au._bootstrap_profile_from_calibration_meta(home)
    assert second == []
    assert _load_profile(home)["role"] == "founder"


def test_corrupt_profile_is_rewritten_from_calibration(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {"meta": {"role": "founder"}})
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "brain" / "profile.json").write_text("{not json")
    au = _reload_auto_update(monkeypatch, home)
    actions = au._bootstrap_profile_from_calibration_meta(home)
    assert any("profile-bootstrap:" in a for a in actions)
    assert _load_profile(home)["role"] == "founder"


def test_ignores_empty_string_values_in_meta(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _seed_calibration(home, {
        "name": "",
        "language": "",
        "meta": {"role": "", "technical_level": "advanced"},
    })
    au = _reload_auto_update(monkeypatch, home)
    au._bootstrap_profile_from_calibration_meta(home)
    prof = _load_profile(home)
    assert prof is not None
    assert prof["technical_level"] == "advanced"
    assert "role" not in prof
    assert "name" not in prof
    assert "language" not in prof
