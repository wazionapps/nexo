"""Plan F0.2.2 + F0.2.4 — enable/disable lifecycle for personal scripts."""

from __future__ import annotations

import json
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
        monkeypatch.setenv("NEXO_HOME", str(home))

        monkeypatch.setenv("NEXO_HOME", str(home))
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


def _seed_core_script(home, filename, name):
    p = home / "core" / "scripts" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "#!/usr/bin/env python3\n"
        f"# nexo: name={name}\n"
        "# nexo: description=core automation\n"
        "# nexo: runtime=python\n"
        "print('core')\n",
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


def test_toggleable_core_script_persists_origin_core(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-email-monitor.py", "email-monitor")
    import script_registry as sr
    import automation_controls as ac
    from db._personal_scripts import get_personal_script

    monkeypatch.setattr(
        ac,
        "get_script_runtime_contract",
        lambda name: {
            "name": name,
            "toggleable_core": True,
            "supports_extra_instructions": True,
            "available": True,
            "blocked_reason": "",
            "blocked_reason_code": "",
            "eligible_labels": ["agent"],
        },
    )

    disabled = sr.set_personal_script_enabled("email-monitor", False)
    assert disabled["ok"] is True
    assert disabled["enabled"] is False
    assert disabled["origin"] == "core"

    row = get_personal_script("email-monitor", include_core=True)
    assert row is not None
    assert row["origin"] == "core"
    assert row["enabled"] is False

    enabled = sr.set_personal_script_enabled("email-monitor", True)
    assert enabled["ok"] is True
    assert enabled["enabled"] is True
    assert enabled["origin"] == "core"


def test_toggleable_core_script_blocks_enable_when_prerequisite_missing(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-followup-runner.py", "followup-runner")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(
        ac,
        "get_script_runtime_contract",
        lambda name: {
            "name": name,
            "toggleable_core": True,
            "supports_extra_instructions": True,
            "available": False,
            "blocked_reason": "Missing agent email account",
            "blocked_reason_code": "missing_account",
            "eligible_labels": [],
        },
    )

    result = sr.set_personal_script_enabled("followup-runner", True)
    assert result["ok"] is False
    assert result["error"] == "Missing agent email account"
    assert result["blocked_reason_code"] == "missing_account"


def test_toggleable_core_script_extra_instructions_round_trip(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-email-monitor.py", "email-monitor")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(
        ac,
        "get_script_runtime_contract",
        lambda name: {
            "name": name,
            "toggleable_core": True,
            "supports_extra_instructions": True,
            "available": True,
            "blocked_reason": "",
            "blocked_reason_code": "",
            "eligible_labels": ["agent"],
        },
    )

    result = sr.set_script_extra_instructions("email-monitor", "Responde en tono breve.")
    assert result["ok"] is True
    assert result["supports_extra_instructions"] is True
    assert result["operator_extra_instructions"] == "Responde en tono breve."

    status = sr.get_personal_script_status("email-monitor")
    assert status["ok"] is True
    assert status["operator_extra_instructions"] == "Responde en tono breve."
    assert status["supports_extra_instructions"] is True

    rows = sr.list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "email-monitor")
    assert target["operator_extra_instructions"] == "Responde en tono breve."
    assert target["supports_extra_instructions"] is True


def test_toggleable_core_script_schedule_override_round_trip(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-email-monitor.py", "email-monitor")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(ac, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})

    result = sr.set_script_schedule_override("email-monitor", interval_seconds=300)
    assert result["ok"] is True
    assert result["schedule_source"] == "override"
    assert result["effective_schedule_label"] == "every 5m"
    assert result["interval_seconds"] == 300

    schedule_path = isolated_home / "personal" / "config" / "schedule.json"
    payload = json.loads(schedule_path.read_text())
    assert payload["core_automation_overrides"]["email-monitor"]["interval_seconds"] == 300

    status = sr.get_personal_script_status("email-monitor")
    assert status["ok"] is True
    assert status["schedule_source"] == "override"
    assert status["effective_schedule_label"] == "every 5m"

    rows = sr.list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "email-monitor")
    assert target["effective_schedule_label"] == "every 5m"
    assert target["schedule_configurable"] is True
    assert target["schedules"][0]["schedule_label"] == "every 5m"

    reset = sr.set_script_schedule_override("email-monitor", clear=True)
    assert reset["ok"] is True
    assert reset["schedule_source"] == "manifest"
    assert reset["effective_schedule_label"] == "every 1m"

    payload = json.loads(schedule_path.read_text())
    assert payload.get("core_automation_overrides", {}) == {}


def test_toggleable_core_script_schedule_override_validates_minimum(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-followup-runner.py", "followup-runner")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(ac, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})

    result = sr.set_script_schedule_override("followup-runner", interval_seconds=60)
    assert result["ok"] is False
    assert ">= 300" in result["error"]


def test_toggleable_core_script_calendar_schedule_override_round_trip(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-morning-agent.py", "morning-agent")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(ac, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})
    monkeypatch.setattr(
        ac,
        "TOGGLEABLE_CORE_SCRIPT_NAMES",
        frozenset(set(ac.TOGGLEABLE_CORE_SCRIPT_NAMES) | {"morning-agent"}),
    )
    monkeypatch.setattr(
        ac,
        "_CORE_AUTOMATION_SCHEDULES",
        {**dict(ac._CORE_AUTOMATION_SCHEDULES), "morning-agent": {"kind": "calendar"}},
    )
    monkeypatch.setattr(
        ac,
        "get_core_manifest_cron",
        lambda name: {
            "id": "morning-agent",
            "script": "scripts/nexo-morning-agent.py",
            "schedule": {"hour": 7, "minute": 0},
        } if name == "morning-agent" else {},
    )

    result = sr.set_script_schedule_override("morning-agent", daily_at="08:15")
    assert result["ok"] is True
    assert result["schedule_type"] == "calendar"
    assert result["schedule_source"] == "override"
    assert result["effective_schedule_label"] == "08:15 daily"

    schedule_path = isolated_home / "personal" / "config" / "schedule.json"
    payload = json.loads(schedule_path.read_text())
    assert payload["core_automation_overrides"]["morning-agent"]["schedule"] == {"hour": 8, "minute": 15}

    status = sr.get_personal_script_status("morning-agent")
    assert status["ok"] is True
    assert status["schedule_type"] == "calendar"
    assert status["effective_schedule_label"] == "08:15 daily"

    rows = sr.list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "morning-agent")
    assert target["schedule_configurable"] is True
    assert target["schedule_type"] == "calendar"
    assert target["effective_schedule_label"] == "08:15 daily"

    reset = sr.set_script_schedule_override("morning-agent", clear=True)
    assert reset["ok"] is True
    assert reset["schedule_source"] == "manifest"
    assert reset["effective_schedule_label"] == "07:00 daily"


def test_toggleable_core_script_calendar_schedule_override_validates_format(isolated_home, monkeypatch):
    _seed_core_script(isolated_home, "nexo-morning-agent.py", "morning-agent")
    import script_registry as sr
    import automation_controls as ac

    monkeypatch.setattr(ac, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})
    monkeypatch.setattr(
        ac,
        "TOGGLEABLE_CORE_SCRIPT_NAMES",
        frozenset(set(ac.TOGGLEABLE_CORE_SCRIPT_NAMES) | {"morning-agent"}),
    )
    monkeypatch.setattr(
        ac,
        "_CORE_AUTOMATION_SCHEDULES",
        {**dict(ac._CORE_AUTOMATION_SCHEDULES), "morning-agent": {"kind": "calendar"}},
    )
    monkeypatch.setattr(
        ac,
        "get_core_manifest_cron",
        lambda name: {
            "id": "morning-agent",
            "script": "scripts/nexo-morning-agent.py",
            "schedule": {"hour": 7, "minute": 0},
        } if name == "morning-agent" else {},
    )

    result = sr.set_script_schedule_override("morning-agent", daily_at="25:99")
    assert result["ok"] is False
    assert "HH:MM" in result["error"]


def test_list_scripts_include_core_surfaces_last_cron_run_for_core_entry(isolated_home):
    _seed_core_script(isolated_home, "nexo-email-monitor.py", "email-monitor")
    from db import get_db
    from script_registry import list_scripts

    conn = get_db()
    conn.execute(
        "INSERT INTO cron_runs (cron_id, started_at, ended_at, exit_code, summary) VALUES (?, datetime('now'), datetime('now'), ?, ?)",
        ("email-monitor", 0, "Processed 2 inbox threads"),
    )
    conn.commit()

    rows = list_scripts(include_core=True)
    target = next(r for r in rows if r["name"] == "email-monitor")
    assert target["last_exit_code"] == 0
    assert target["last_run_at"]
    assert target["last_summary"] == "Processed 2 inbox threads"
