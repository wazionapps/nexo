import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SOURCE = ROOT / "scripts" / "nexo-live-source-preflight.py"
SECRET_VIEW = ROOT / "scripts" / "nexo-safe-secret-view.py"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_live_source_preflight_requires_complete_matrix(tmp_path):
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"domains": {"wazion": {"repo": "/repo"}}}))
    result = run_script(LIVE_SOURCE, "--domain", "wazion", "--matrix-file", str(matrix))
    assert result.returncode == 2
    assert "missing required field 'branch'" in result.stderr


def test_live_source_preflight_accepts_expected_wazion_matrix():
    result = run_script(
        LIVE_SOURCE,
        "--domain",
        "wazion",
        "--repo",
        "/Users/franciscoc/Documents/_PhpstormProjects/WAzion/WAzion",
        "--branch",
        "main",
        "--cloud-project",
        "recambiosyaccesoriosbmw",
        "--table",
        "allinoneapp_main",
    )
    assert result.returncode == 0, result.stderr
    assert "OK: live-source matrix fixed domain=wazion" in result.stdout


def test_safe_secret_view_masks_env_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-test-secret-value\nDB_PASSWORD=short\n")
    result = run_script(SECRET_VIEW, "--file", str(env_file))
    assert result.returncode == 0, result.stderr
    assert "OPENAI_API_KEY=sk...ue <len=20 sha256=" in result.stdout
    assert "sk-test-secret-value" not in result.stdout
    assert "DB_PASSWORD=<masked len=5 sha256=" in result.stdout


def test_safe_secret_view_blocks_full_read_commands():
    result = run_script(SECRET_VIEW, "--check-command", "cat /srv/app/.env")
    assert result.returncode == 2
    assert "BLOCKED: cat would dump a sensitive file" in result.stderr


def test_safe_secret_view_allows_non_sensitive_cat():
    result = run_script(SECRET_VIEW, "--check-command", "cat README.md")
    assert result.returncode == 0
    assert "OK: command does not match" in result.stdout
