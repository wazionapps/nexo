from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _reload_user_context(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("user_context", None)
    import user_context

    return importlib.reload(user_context)


def test_user_context_defaults_to_neutral_assistant_name_when_missing(monkeypatch, tmp_path):
    home = tmp_path / ".nexo"
    (home / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (home / "personal" / "brain" / "calibration.json").write_text(json.dumps({}))

    module = _reload_user_context(monkeypatch, home)
    module._ctx = None
    ctx = module.get_context()

    assert ctx.assistant_name == module.DEFAULT_ASSISTANT_NAME
    assert ctx.assistant_name == "Nova"


def test_user_context_uses_version_operator_name_only_as_override(monkeypatch, tmp_path):
    home = tmp_path / ".nexo"
    (home / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (home / "personal" / "brain" / "calibration.json").write_text(json.dumps({}))
    (home / "version.json").write_text(json.dumps({"operator_name": "Atlas"}))

    module = _reload_user_context(monkeypatch, home)
    module._ctx = None
    ctx = module.get_context()

    assert ctx.assistant_name == "Atlas"
