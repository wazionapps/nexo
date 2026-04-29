import json

from support_snapshot import collect_snapshot


def test_collect_snapshot_returns_generic_runtime_payload(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "runtime" / "data").mkdir(parents=True)
    (home / "runtime" / "logs").mkdir(parents=True)
    (home / "runtime" / "operations").mkdir(parents=True)
    (home / "runtime" / "events.ndjson").write_text(json.dumps({"type": "health_alert", "ts": 1}) + "\n")
    (home / "runtime" / "operations" / "sample.log").write_text("line-a\nline-b\n")
    (home / "version.json").write_text(json.dumps({"version": "7.11.7"}))

    monkeypatch.setenv("NEXO_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))

    payload = collect_snapshot(log_lines=10, include_doctor=False)

    assert payload["version"] == "7.11.7"
    assert payload["paths"]["home"]["exists"] is True
    assert payload["platform"]["is_wsl"] is False
    assert payload["windows_runtime"]["supported_brain_mode"] == "wsl"
    assert payload["windows_runtime"]["inside_wsl"] is False
    assert payload["windows_runtime"]["warnings"] == []
    assert "health" in payload
    assert "logs" in payload
    assert isinstance(payload["logs"]["events"], list)
    assert isinstance(payload["logs"]["operations"], list)


def test_collect_snapshot_reports_wsl_runtime_hints(monkeypatch):
    monkeypatch.setenv("NEXO_HOME", "/home/tester/.nexo")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/123_interop")
    monkeypatch.setattr("support_snapshot.platform.system", lambda: "Linux")
    monkeypatch.setattr("support_snapshot.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr("support_snapshot.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("support_snapshot.platform.python_version", lambda: "3.12.0")

    payload = collect_snapshot(log_lines=5, include_doctor=False)

    assert payload["platform"]["is_wsl"] is True
    assert payload["windows_runtime"]["inside_wsl"] is True
    assert payload["windows_runtime"]["wsl_distro"] == "Ubuntu-24.04"
    assert payload["windows_runtime"]["wsl_interop"] is True
    assert payload["windows_runtime"]["warnings"] == []


def test_collect_snapshot_warns_when_nexo_home_lives_on_windows_mount(monkeypatch):
    monkeypatch.setenv("NEXO_HOME", "/mnt/c/Users/francisco/.nexo")
    monkeypatch.setenv("HOME", "/home/francisco")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr("support_snapshot.platform.system", lambda: "Linux")
    monkeypatch.setattr("support_snapshot.platform.release", lambda: "6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr("support_snapshot.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("support_snapshot.platform.python_version", lambda: "3.12.0")

    payload = collect_snapshot(log_lines=5, include_doctor=False)

    assert payload["windows_runtime"]["inside_wsl"] is True
    assert payload["windows_runtime"]["nexo_home_on_windows_mount"] is True
    assert payload["windows_runtime"]["warnings"] == [
        {
            "code": "nexo_home_on_windows_mount",
            "message": "NEXO_HOME is inside /mnt/*; keep the canonical Brain runtime inside the WSL filesystem.",
        }
    ]
