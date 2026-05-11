from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "src" / "scripts" / "nexo-tcc-approve.sh"


def _write_tool(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _base_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    home = tmp_path / "home"
    nexo_home = tmp_path / "nexo"
    versions = home / ".local" / "share" / "claude" / "versions"
    tcc_dir = home / "Library" / "Application Support" / "com.apple.TCC"
    versions.mkdir(parents=True)
    tcc_dir.mkdir(parents=True)
    (versions / "2.1.test").write_text("#!/bin/bash\n", encoding="utf-8")
    (tcc_dir / "TCC.db").touch()
    return {
        **os.environ,
        "HOME": str(home),
        "NEXO_HOME": str(nexo_home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }


def test_tcc_approve_marks_full_disk_access_required_without_marking_version(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_tool(fake_bin / "uname", "#!/bin/bash\necho Darwin\n")
    _write_tool(fake_bin / "sqlite3", "#!/bin/bash\necho 'authorization denied' >&2\nexit 1\n")
    _write_tool(fake_bin / "python3", f"#!/bin/bash\nexec {sys.executable} \"$@\"\n")

    env = _base_env(tmp_path, fake_bin)

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0
    assert "Full Disk Access required" in result.stdout
    marker = Path(env["NEXO_HOME"]) / "runtime" / "data" / ".tcc-approved" / "2.1.test"
    assert not marker.exists()
    log = Path(env["NEXO_HOME"]) / "runtime" / "logs" / "tcc-auto-approve.log"
    assert "authorization denied" in log.read_text(encoding="utf-8")
    assert "FAILED: Claude 2.1.test" in log.read_text(encoding="utf-8")
    state = json.loads((Path(env["NEXO_HOME"]) / "runtime" / "state" / "full-disk-access-required.json").read_text(encoding="utf-8"))
    assert state["status"] == "later"
    assert state["source"] == "tcc-approve"
    schedule = json.loads((Path(env["NEXO_HOME"]) / "personal" / "config" / "schedule.json").read_text(encoding="utf-8"))
    assert schedule["full_disk_access_status"] == "later"
    assert any("/bin/bash" in reason for reason in schedule["full_disk_access_reasons"])


def test_tcc_approve_keeps_non_permission_sqlite_failures_red(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_tool(fake_bin / "uname", "#!/bin/bash\necho Darwin\n")
    _write_tool(fake_bin / "sqlite3", "#!/bin/bash\necho 'database disk image is malformed' >&2\nexit 1\n")

    env = _base_env(tmp_path, fake_bin)

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 1
    assert "TCC auto-approve failed" in result.stderr
    assert not (Path(env["NEXO_HOME"]) / "runtime" / "state" / "full-disk-access-required.json").exists()
    marker = Path(env["NEXO_HOME"]) / "runtime" / "data" / ".tcc-approved" / "2.1.test"
    assert not marker.exists()


def test_tcc_approve_marks_version_only_after_all_services_succeed(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_tool(fake_bin / "uname", "#!/bin/bash\necho Darwin\n")
    _write_tool(fake_bin / "sqlite3", "#!/bin/bash\nexit 0\n")

    env = _base_env(tmp_path, fake_bin)

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "approved 1 Claude version(s)" in result.stdout
    marker = Path(env["NEXO_HOME"]) / "runtime" / "data" / ".tcc-approved" / "2.1.test"
    assert marker.exists()
