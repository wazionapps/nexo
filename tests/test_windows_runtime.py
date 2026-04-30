import json

from windows_runtime import (
    build_windows_host_cleanup_plan,
    query_windows_host_special_folders,
    query_windows_host_tasks,
    windows_runtime_status,
)


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_query_windows_host_special_folders_parses_powershell_json(monkeypatch):
    def fake_runner(args, timeout=20):
        assert "powershell.exe" in args[0]
        return _Result(
            0,
            json.dumps(
                {
                    "LocalApplicationData": r"C:\Users\nero\AppData\Local",
                    "ApplicationData": r"C:\Users\nero\AppData\Roaming",
                    "Programs": r"C:\Users\nero\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
                }
            ),
            "",
        )

    payload = query_windows_host_special_folders(
        runner=fake_runner,
        which_func=lambda command: command if command == "powershell.exe" else "",
    )

    assert payload["available"] is True
    assert payload["folders"]["LocalApplicationData"].endswith(r"AppData\Local")
    assert payload["folders"]["ApplicationData"].endswith(r"AppData\Roaming")


def test_query_windows_host_tasks_filters_nexo_task_names():
    csv_rows = "\n".join(
        [
            '"\\NEXO Desktop","Ready","..."',
            '"\\Microsoft\\Windows\\Defrag","Ready","..."',
            '"\\com.nexo.watchdog","Ready","..."',
        ]
    )

    payload = query_windows_host_tasks(
        runner=lambda args, timeout=30: _Result(0, csv_rows, ""),
        which_func=lambda command: command if command == "schtasks.exe" else "",
    )

    assert payload["available"] is True
    assert payload["tasks"] == ["\\NEXO Desktop", "\\com.nexo.watchdog"]


def test_build_windows_host_cleanup_plan_collects_runtime_data_and_shortcuts():
    def fake_runner(args, timeout=20):
        if "powershell.exe" in args[0]:
            return _Result(
                0,
                json.dumps(
                    {
                        "LocalApplicationData": r"C:\Users\nero\AppData\Local",
                        "ApplicationData": r"C:\Users\nero\AppData\Roaming",
                        "Programs": r"C:\Users\nero\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
                    }
                ),
                "",
            )
        return _Result(0, '"\\NEXO Desktop","Ready","..."\n', "")

    plan = build_windows_host_cleanup_plan(
        delete_data=True,
        runner=fake_runner,
        which_func=lambda command: command,
    )

    assert plan["available"] is True
    assert r"C:\Users\nero\AppData\Local\Programs\NEXO Desktop" in plan["runtime_paths"]
    assert r"C:\Users\nero\AppData\Roaming\NEXO Desktop" in plan["data_paths"]
    assert r"C:\Users\nero\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\NEXO Desktop.lnk" in plan["shortcut_paths"]
    assert plan["tasks"] == ["\\NEXO Desktop"]


def test_windows_runtime_status_reports_host_interop_flag(monkeypatch):
    monkeypatch.setenv("NEXO_WINDOWS_HOST", "1")
    monkeypatch.setenv("NEXO_WINDOWS_BRIDGE", "1")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/interop")

    payload = windows_runtime_status(
        candidate := __import__("pathlib").Path("/home/nero/.nexo"),
        system="Linux",
        release="6.6.87.2-microsoft-standard-WSL2",
    )

    assert payload["inside_wsl"] is True
    assert payload["windows_host_bridge"] is True
    assert "windows_host_interop" in payload
