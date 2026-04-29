import json

from health_check import _check_runtime


def test_check_runtime_reports_windows_runtime_hints_for_wsl(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    (home / "version.json").write_text(json.dumps({"version": "7.12.1"}))

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setattr("health_check.platform.system", lambda: "Linux")
    monkeypatch.setattr("health_check.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/interop")

    payload = _check_runtime()

    assert payload["status"] == "ok"
    assert payload["is_wsl"] is True
    assert payload["windows_runtime"]["inside_wsl"] is True
    assert payload["windows_runtime"]["wsl_distro"] == "Ubuntu-24.04"
    assert payload["windows_runtime"]["warnings"] == []


def test_check_runtime_degrades_when_nexo_home_is_on_windows_mount(monkeypatch):
    monkeypatch.setenv("NEXO_HOME", "/mnt/c/Users/francisco/.nexo")
    monkeypatch.setattr("health_check.platform.system", lambda: "Linux")
    monkeypatch.setattr("health_check.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.delenv("WSL_INTEROP", raising=False)

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
