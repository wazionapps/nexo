"""Regression tests for scripts/check_no_personal_data.sh.

B9 privacy guard — ensure the CI gate actually fails on a synthetic
leak and stays silent on a clean tree. The tests exercise the script
through a ``CHECK_ROOT`` override so the repo's real ``src/`` is never
touched; the override is a one-line hook in the script itself.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_no_personal_data.sh"


def _run_guard(root: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CHECK_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    return tmp_path


def test_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} must be executable"


def test_clean_tree_passes(sandbox):
    (sandbox / "src" / "app.py").write_text(
        "# generic module\n"
        'CONTACT = "support@example.com"\n'
        "PORT = 8080\n"
    )
    result = _run_guard(sandbox)
    assert result.returncode == 0, (
        f"expected clean tree to pass.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


def test_fixed_string_leak_fails(sandbox):
    """One of the operator-specific fixed patterns in src/ must fail."""
    (sandbox / "src" / "leak.py").write_text(
        'OWNER_EMAIL = "info@wazion.com"\n'
    )
    result = _run_guard(sandbox)
    assert result.returncode == 1, (
        f"expected fixed-pattern leak to fail.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "LEAK" in result.stdout


def test_regex_email_leak_fails(sandbox):
    """A fresh operator's email would not appear in the fixed list, but the
    regex shape detector (AUDITOR-3RDPASS-V640 §Risk 1) must still catch it.
    """
    (sandbox / "src" / "leak.py").write_text(
        'OWNER_EMAIL = "freshoperator@some-tenant.io"\n'
    )
    result = _run_guard(sandbox)
    assert result.returncode == 1, (
        f"expected regex shape to catch an unknown operator email.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "LEAK" in result.stdout


def test_allowlisted_placeholders_pass(sandbox):
    """Documentation placeholders and RFC 2606 examples must not alarm."""
    (sandbox / "src" / "docs.py").write_text(
        '"""Send to owner@example.com (RFC 2606 example)."""\n'
        'LOCAL = "127.0.0.1"\n'
        'TEST_NET = "192.0.2.1"\n'
    )
    result = _run_guard(sandbox)
    assert result.returncode == 0, (
        f"expected allowlisted placeholders to pass.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_users_path_leak_fails(sandbox):
    (sandbox / "src" / "leak.py").write_text(
        'HOME = "/Users/franciscoc/something"\n'
    )
    result = _run_guard(sandbox)
    assert result.returncode == 1
    assert "LEAK" in result.stdout
