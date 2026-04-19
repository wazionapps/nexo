from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCRIPT = REPO_ROOT / "src" / "auto_close_sessions.py"


def test_packaged_auto_close_sessions_imports_db_from_core_parent(tmp_path):
    runtime_root = tmp_path / "runtime"
    core_dir = runtime_root / "core"
    scripts_dir = core_dir / "scripts"
    db_dir = core_dir / "db"
    scripts_dir.mkdir(parents=True)
    db_dir.mkdir(parents=True)

    (scripts_dir / "auto_close_sessions.py").write_text(SOURCE_SCRIPT.read_text())
    (db_dir / "__init__.py").write_text(
        "def init_db():\n    return None\n"
        "def get_db():\n    return None\n"
        "def get_diary_draft(_sid):\n    return None\n"
        "def delete_diary_draft(_sid):\n    return None\n"
        "def get_orphan_sessions(_ttl):\n    return []\n"
        "def read_checkpoint(_sid):\n    return {}\n"
        "def write_session_diary(**_kwargs):\n    return None\n"
        "def now_epoch():\n    return 0\n"
        "SESSION_STALE_SECONDS = 900\n"
    )

    probe = (
        "import importlib.util, pathlib; "
        f"p = pathlib.Path({str(scripts_dir / 'auto_close_sessions.py')!r}); "
        "spec = importlib.util.spec_from_file_location('auto_close_sessions_packaged', p); "
        "mod = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(mod); "
        "print('ok')"
    )
    env = {
        **os.environ,
        "NEXO_HOME": str(runtime_root),
        "NEXO_CODE": str(runtime_root),
        "PYTHONPATH": str(runtime_root),
    }
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
