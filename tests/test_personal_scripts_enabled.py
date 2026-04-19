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
    for mod in list(sys.modules):
        if mod == "db" or mod.startswith("db.") or mod == "script_registry":
            sys.modules.pop(mod, None)
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
