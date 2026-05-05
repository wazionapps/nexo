from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = SRC / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_followup_runner_no_longer_uses_six_hour_cli_timeout():
    from constants import AUTOMATION_SUBPROCESS_TIMEOUT

    module = _load_script("nexo-followup-runner.py")

    assert module.CLI_TIMEOUT == AUTOMATION_SUBPROCESS_TIMEOUT
    assert module.CLI_TIMEOUT == 10800
    assert "timeout=21600" not in (SCRIPTS / "nexo-followup-runner.py").read_text(encoding="utf-8")


def test_email_monitor_metadata_has_bounded_runtime_timeout():
    text = (SCRIPTS / "nexo-email-monitor.py").read_text(encoding="utf-8")

    assert "# nexo: timeout=1800" in text
    assert "# nexo: timeout=21600" not in text
