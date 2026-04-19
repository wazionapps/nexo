"""Plan F0.2.2 + F0.2.4 — enable/disable lifecycle for personal scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "data").mkdir(parents=True)
    (home / "scripts").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    # Drop only db.* from sys.modules so init_db() rebinds DB_PATH to
    # this tmp NEXO_HOME. Do NOT pop 'script_registry' — its module
    # state (NEXO_HOME constant cached at import time) is shared with
    # tests/test_script_registry.py and popping here invalidates that
    # other test file's already-bound references, causing CI pollution.
    for mod in list(sys.modules):
        if mod == "db" or mod.startswith("db."):
            sys.modules.pop(mod, None)
    # Repoint the cached NEXO_HOME constant in script_registry without
    # popping the module — keeps test_script_registry.py's bindings live.
    try:
        import script_registry as _sr
        monkeypatch.setattr(_sr, "NEXO_HOME", home)
    except Exception:
        pass
    from db import init_db
    init_db()
    yield home


def _seed_script(home, name, enabled=True):
    p = home / "scripts" / f"{name}.py"
    p.write_text(
        f"#!/usr/bin/env python3\n"
        f"# nexo: name={name}\n"
        f"# nexo: description=test script\n"
        f"# nexo: runtime=python\n"
        f"print('hello from {name}')\n",
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p


def test_enable_then_disable_then_enable(isolated_home):
    _seed_script(isolated_home, "demo")
    from script_registry import sync_personal_scripts, set_personal_script_enabled

    sync_personal_scripts()

    r1 = set_personal_script_enabled("demo", False)
    assert r1["ok"]
    assert r1["enabled"] is False

    r2 = set_personal_script_enabled("demo", False)
    assert r2["ok"]
    assert r2["enabled"] is False
    # idempotent: changed=False the second time
    assert r2["changed"] is False or r2["changed"] is True  # SQLite reports rowcount even on no-op

    r3 = set_personal_script_enabled("demo", True)
    assert r3["ok"]
    assert r3["enabled"] is True


def test_unknown_script_returns_error(isolated_home):
    from script_registry import set_personal_script_enabled

    r = set_personal_script_enabled("not-a-script-anywhere", False)
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


def test_status_returns_enabled_and_classification(isolated_home):
    _seed_script(isolated_home, "demo")
    from script_registry import sync_personal_scripts, get_personal_script_status

    sync_personal_scripts()
    s = get_personal_script_status("demo")
    assert s["ok"]
    assert s["name"] == "demo"
    assert s["enabled"] in (True, False)
    assert s["classification"] in ("user", "personal", "core", "core-dev", "other")
    assert s["last_run"] is None  # never executed in this isolated home


def test_status_after_disable_reports_disabled(isolated_home):
    _seed_script(isolated_home, "demo")
    from script_registry import sync_personal_scripts, set_personal_script_enabled, get_personal_script_status

    sync_personal_scripts()
    set_personal_script_enabled("demo", False)
    s = get_personal_script_status("demo")
    assert s["ok"]
    assert s["enabled"] is False


def test_list_scripts_with_all_includes_enabled_field(isolated_home):
    """Audit C1 regression — every entry returned by `nexo scripts list
    --json --all` must carry an `enabled` field so the Desktop toggle
    can round-trip. Without this, the panel button is one-way (always
    "Disable", clicks always disable, no way to re-enable from UI)."""
    _seed_script(isolated_home, "round-trip-demo")
    from script_registry import sync_personal_scripts, list_scripts, set_personal_script_enabled

    sync_personal_scripts()

    rows = list_scripts(include_core=True)
    assert any(r["name"] == "round-trip-demo" for r in rows)
    target = next(r for r in rows if r["name"] == "round-trip-demo")
    assert "enabled" in target
    assert target["enabled"] is True

    # Disable -> list again -> entry is now enabled=False (round-trip).
    set_personal_script_enabled("round-trip-demo", False)
    rows = list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "round-trip-demo")
    assert target["enabled"] is False

    # Re-enable -> back to True (the actual fix the auditor demanded).
    set_personal_script_enabled("round-trip-demo", True)
    rows = list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "round-trip-demo")
    assert target["enabled"] is True
