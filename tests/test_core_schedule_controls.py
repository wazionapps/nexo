from __future__ import annotations

import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _write_manifest(home: Path, crons: list[dict]) -> None:
    manifest = home / "runtime" / "crons" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"crons": crons}))


def test_list_core_schedules_excludes_toggleable_automations_and_helpers(tmp_path, monkeypatch):
    import core_schedule_controls

    home = tmp_path / "nexo-home"
    _write_manifest(home, [
        {"id": "watchdog", "script": "scripts/nexo-watchdog.sh", "interval_seconds": 1800, "core": True},
        {"id": "email-monitor", "script": "scripts/nexo-email-monitor.py", "interval_seconds": 60, "core": True},
        {"id": "prevent-sleep", "script": "scripts/nexo-prevent-sleep.sh", "keep_alive": True, "core": True},
        {"id": "dashboard", "script": "scripts/nexo-dashboard.sh", "keep_alive": True, "core": True},
        {"id": "evolution", "script": "scripts/nexo-evolution-run.py", "schedule": {"hour": 5, "minute": 0, "weekday": 0}, "core": True},
    ])
    monkeypatch.setenv("NEXO_HOME", str(home))

    rows = core_schedule_controls.list_core_schedules()
    names = [row["name"] for row in rows]

    assert names == ["watchdog", "dashboard", "evolution"]
    assert rows[0]["desktop_editable"] is True
    assert rows[1]["desktop_editable"] is False
    assert rows[1]["cli_editable"] is False
    assert rows[2]["desktop_editable"] is False
    assert rows[2]["cli_editable"] is True


def test_set_core_schedule_clamps_interval_and_persists_override(tmp_path, monkeypatch):
    import core_schedule_controls

    home = tmp_path / "nexo-home"
    _write_manifest(home, [
        {"id": "watchdog", "script": "scripts/nexo-watchdog.sh", "interval_seconds": 1800, "core": True},
    ])
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setattr(core_schedule_controls, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})

    result = core_schedule_controls.set_core_schedule("watchdog", interval_seconds=60)

    assert result["ok"] is True
    assert result["interval_seconds"] == 600
    assert result["warning"]
    overrides = json.loads((home / "personal" / "config" / "schedule-overrides.json").read_text())
    assert overrides == {"watchdog": {"interval_seconds": 600}}


def test_set_core_schedule_persists_calendar_override_as_start_hour(tmp_path, monkeypatch):
    import core_schedule_controls

    home = tmp_path / "nexo-home"
    _write_manifest(home, [
        {"id": "deep-sleep", "script": "scripts/nexo-deep-sleep.sh", "schedule": {"hour": 4, "minute": 30}, "core": True},
    ])
    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setattr(core_schedule_controls, "_sync_core_crons_runtime", lambda: {"ok": True, "method": "test"})

    result = core_schedule_controls.set_core_schedule("deep-sleep", daily_at="05:45")

    assert result["ok"] is True
    assert result["schedule_type"] == "calendar"
    assert result["schedule"] == {"hour": 5, "minute": 45}
    overrides = json.loads((home / "personal" / "config" / "schedule-overrides.json").read_text())
    assert overrides == {"deep-sleep": {"start_hour": "05:45"}}


def test_set_core_schedule_rejects_toggleable_product_automation(tmp_path, monkeypatch):
    import core_schedule_controls

    home = tmp_path / "nexo-home"
    _write_manifest(home, [
        {"id": "email-monitor", "script": "scripts/nexo-email-monitor.py", "interval_seconds": 60, "core": True},
    ])
    monkeypatch.setenv("NEXO_HOME", str(home))

    result = core_schedule_controls.set_core_schedule("email-monitor", interval_seconds=300)

    assert result["ok"] is False
    assert "Preferences -> Automations" in result["error"]
