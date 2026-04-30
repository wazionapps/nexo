import json

from health_check import _check_crons, _check_runtime


def test_check_runtime_reports_windows_runtime_hints_for_wsl(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    (home / "version.json").write_text(json.dumps({"version": "7.12.1"}))

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setattr("health_check.platform.system", lambda: "Linux")
    monkeypatch.setattr("health_check.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/interop")
    monkeypatch.setenv("NEXO_WINDOWS_HOST", "1")
    monkeypatch.setenv("NEXO_WINDOWS_BRIDGE", "1")

    payload = _check_runtime()

    assert payload["status"] == "ok"
    assert payload["is_wsl"] is True
    assert payload["windows_runtime"]["inside_wsl"] is True
    assert payload["windows_runtime"]["windows_host_bridge"] is True
    assert payload["windows_runtime"]["bridge_mode"] == "wsl-exec"
    assert payload["windows_runtime"]["wsl_distro"] == "Ubuntu-24.04"
    assert payload["windows_runtime"]["warnings"] == []


def test_check_runtime_degrades_when_nexo_home_is_on_windows_mount(monkeypatch):
    monkeypatch.setenv("NEXO_HOME", "/mnt/c/Users/francisco/.nexo")
    monkeypatch.setattr("health_check.platform.system", lambda: "Linux")
    monkeypatch.setattr("health_check.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.delenv("NEXO_WINDOWS_HOST", raising=False)
    monkeypatch.delenv("NEXO_WINDOWS_BRIDGE", raising=False)

    payload = _check_runtime()

    assert payload["status"] == "degraded"
    assert payload["windows_runtime"]["inside_wsl"] is True
    assert payload["windows_runtime"]["nexo_home_on_windows_mount"] is True
    assert payload["windows_runtime"]["warnings"] == [
        {
            "code": "nexo_home_on_windows_mount",
            "message": "NEXO_HOME is inside /mnt/*; keep the canonical Brain runtime inside the WSL filesystem.",
        }
    ]


def test_check_crons_reports_systemd_units_inside_wsl(tmp_path, monkeypatch):
    home = tmp_path / "home"
    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "nexo-watchdog.service").write_text("[Unit]\n")
    (unit_dir / "nexo-watchdog.timer").write_text("[Timer]\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(home / ".nexo"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/interop")
    monkeypatch.setattr("health_check.platform.system", lambda: "Linux")
    monkeypatch.setattr("health_check.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr("health_check.query_windows_host_tasks", lambda: {"available": False, "tasks": [], "error": "schtasks_missing"})

    payload = _check_crons()

    assert payload["platform"] == "wsl"
    assert payload["systemd_services"] == 1
    assert payload["systemd_timers"] == 1
    assert payload["status"] == "ok"
    assert payload["windows_host_tasks"]["available"] is False
