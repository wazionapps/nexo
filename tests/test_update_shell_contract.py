from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "src" / "scripts" / "nexo-update.sh"


def test_nexo_update_shell_script_delegates_to_python_core():
    text = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "from plugins.update import handle_update" in text
    assert "handle_update(remote=remote, branch=branch)" in text
    assert "git pull" not in text
    assert "git reset --hard" not in text
