"""Tests for scripts/measure_drift_baseline.py — Plan Consolidado 0.15.

NEXO_HOME is forced to tmp_path so these tests never scan the real diary
tree. Honours learning #437.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "measure_drift_baseline.py"


@pytest.fixture(autouse=True)
def _reset_module_cache():
    if "measure_drift_baseline" in sys.modules:
        del sys.modules["measure_drift_baseline"]
    yield


@pytest.fixture
def loaded_module(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("measure_drift_baseline", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _seed_diary(home: Path, name: str, body: str) -> Path:
    d = home / "brain" / "session_archive"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(body, encoding="utf-8")
    return path


def test_measure_with_no_diaries_returns_empty(loaded_module, tmp_path):
    out = loaded_module.measure(home=tmp_path)
    assert out["diaries_scanned"] == 0
    assert out["diaries_with_hits"] == 0
    assert out["rule_counts"]  # all rules registered, values 0


def test_measure_counts_matching_patterns(loaded_module, tmp_path):
    _seed_diary(
        tmp_path, "2026-04-01.md",
        "NEXO edité sin guard_check tres veces hoy. Dije listo sin verificar.\n",
    )
    _seed_diary(
        tmp_path, "2026-04-02.md",
        "Hoy se olvidó capturar learning tras corrección.\n",
    )
    out = loaded_module.measure(home=tmp_path)
    assert out["diaries_scanned"] == 2
    assert out["diaries_with_hits"] >= 1
    # At least the two seeded rules fire.
    assert out["rule_counts"].get("R13_pre_edit_guard", 0) >= 1
    assert out["rule_counts"].get("R16_declared_done", 0) >= 1


def test_write_report_creates_json_under_reports(loaded_module, tmp_path):
    _seed_diary(tmp_path, "x.md", "dije hecho sin verificar\n")
    result = loaded_module.measure(home=tmp_path)
    path = loaded_module.write_report(result, home=tmp_path)
    assert path.parent == tmp_path / "reports"
    assert path.name.startswith("drift-baseline-")
    assert path.read_text().startswith("{")


def test_main_returns_nonzero_when_no_diaries(loaded_module, tmp_path, monkeypatch):
    # Already empty — no seed.
    rc = loaded_module.main([])
    assert rc == 2
