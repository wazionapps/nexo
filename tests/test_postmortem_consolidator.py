from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "scripts" / "nexo-postmortem-consolidator.py"


def _load_module(monkeypatch):
    agent_runner = types.SimpleNamespace(
        AutomationBackendUnavailableError=RuntimeError,
        run_automation_prompt=lambda *args, **kwargs: None,
    )
    client_preferences = types.SimpleNamespace(resolve_user_model=lambda: "")
    monkeypatch.setitem(sys.modules, "agent_runner", agent_runner)
    monkeypatch.setitem(sys.modules, "client_preferences", client_preferences)

    spec = importlib.util.spec_from_file_location("test_postmortem_consolidator_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_process_sensory_register_ingests_pending_events_and_prunes_processed_lines(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)

    ingested = []
    fake_cognitive = types.SimpleNamespace(
        ingest_sensory=lambda **kwargs: ingested.append(kwargs),
    )
    monkeypatch.setitem(sys.modules, "cognitive", fake_cognitive)

    buffer_path = tmp_path / "session_buffer.jsonl"
    buffer_path.write_text(
        "\n".join(
            [
                '{"ts":"2026-04-14T10:00:00Z","tool":"Bash","source":"hook"}',
                '{"ts":"2026-04-14T11:00:00Z","source":"claude","tasks":["Investigate C9 pipeline"]}',
                '{"ts":"2026-04-13T09:00:00Z","tool":"Bash","source":"hook"}',
                "not-json",
            ]
        )
        + "\n"
    )

    monkeypatch.setattr(module, "SESSION_BUFFER", buffer_path)
    monkeypatch.setattr(module, "TODAY_STR", "2026-04-14")
    monkeypatch.setattr(module, "log", lambda msg: None)

    module.process_sensory_register()

    assert len(ingested) == 3
    assert any("Tool activity via hook: Bash" in item["content"] for item in ingested)
    assert any("Tasks: Investigate C9 pipeline" in item["content"] for item in ingested)
    assert any(item["created_at"] == "2026-04-13T09:00:00Z" for item in ingested)

    remaining = buffer_path.read_text().splitlines()
    assert "not-json" in remaining
    assert '{"ts":"2026-04-14T10:00:00Z","tool":"Bash","source":"hook"}' not in remaining
    assert '{"ts":"2026-04-14T11:00:00Z","source":"claude","tasks":["Investigate C9 pipeline"]}' not in remaining
    assert '{"ts":"2026-04-13T09:00:00Z","tool":"Bash","source":"hook"}' not in remaining
