"""Tests for Plan Consolidado 0.X.4 — locations section in build_system_catalog.

NEXO_HOME is forced to tmp_path so ``_locations()`` resolves predictable paths
that the test can assert on without depending on the real runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from system_catalog import _locations, build_system_catalog  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_nexo_home(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    # _locations reads NEXO_HOME at module-load time via a module constant.
    # Reload so the fixture takes effect for this test.
    import importlib
    import system_catalog
    importlib.reload(system_catalog)
    yield
    importlib.reload(system_catalog)


def test_locations_returns_flat_dict_of_absolute_paths():
    from system_catalog import _locations as fresh_locations
    locs = fresh_locations()
    assert isinstance(locs, dict)
    for key, value in locs.items():
        assert isinstance(key, str)
        assert isinstance(value, str)
        assert value.startswith("/"), f"{key!r} is not absolute: {value!r}"


def test_locations_contains_the_canonical_keys():
    from system_catalog import _locations as fresh_locations
    locs = fresh_locations()
    required = {
        "nexo_home",
        "nexo_code",
        "brain.db",
        "brain.calibration",
        "brain.project_atlas",
        "config.guardian",
        "config.guardian_runtime_overrides",
        "logs.guardian_telemetry",
        "logs.guardian_overrides",
        "tool_enforcement_map",
        "reports",
        "snapshots",
    }
    missing = required - set(locs.keys())
    assert not missing, f"missing keys in _locations(): {missing}"


def test_build_system_catalog_exposes_locations(tmp_path):
    from system_catalog import build_system_catalog as fresh_build
    cat = fresh_build()
    assert "locations" in cat
    assert isinstance(cat["locations"], dict)
    assert cat["locations"]["nexo_home"].startswith(str(tmp_path))
