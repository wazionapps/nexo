from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_runtime_packages.py"


def test_check_runtime_packages_fails_when_new_package_is_missing(tmp_path):
    src = tmp_path / "src"
    runtime = tmp_path / "runtime"
    (src / "db").mkdir(parents=True)
    (src / "new_brain_package").mkdir(parents=True)
    (runtime / "db").mkdir(parents=True)
    (src / "db" / "__init__.py").write_text("", encoding="utf-8")
    (src / "new_brain_package" / "__init__.py").write_text("", encoding="utf-8")
    (runtime / "db" / "__init__.py").write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--src",
            str(src),
            "--runtime",
            str(runtime),
            "--no-import",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "new_brain_package" in result.stdout


def test_check_runtime_packages_passes_when_all_packages_are_present(tmp_path):
    src = tmp_path / "src"
    runtime = tmp_path / "runtime"
    for base in (src, runtime):
        (base / "db").mkdir(parents=True)
        (base / "new_brain_package").mkdir(parents=True)
        (base / "db" / "__init__.py").write_text("", encoding="utf-8")
        (base / "new_brain_package" / "__init__.py").write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--src",
            str(src),
            "--runtime",
            str(runtime),
            "--no-import",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "RESULT: OK" in result.stdout
