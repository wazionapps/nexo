from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "scripts" / "nexo-postmortem-consolidator.py"


def _load_module(monkeypatch, *, run_automation_prompt=None, build_consolidation_brief=None):
    agent_runner = types.SimpleNamespace(
        AutomationBackendUnavailableError=RuntimeError,
        run_automation_prompt=run_automation_prompt or (lambda *args, **kwargs: None),
    )
    client_preferences = types.SimpleNamespace(resolve_user_model=lambda: "")
    consolidation_prep = types.SimpleNamespace(
        build_consolidation_brief=build_consolidation_brief
        or (
            lambda *a, **k: {
                "corpus_size": 0,
                "today_topics": [],
                "shortlist": [],
                "contradiction_pairs": [],
                "truncated": False,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, "agent_runner", agent_runner)
    monkeypatch.setitem(sys.modules, "client_preferences", client_preferences)
    monkeypatch.setitem(sys.modules, "consolidation_prep", consolidation_prep)

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


def test_consolidate_passes_bounded_brief_and_restricted_tools(monkeypatch):
    captured = {}

    def fake_run(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def fake_brief(diaries_with_critique, *a, **k):
        return {
            "corpus_size": 879,
            "today_topics": [
                {"slug": "guard-before-edit", "title": "guard before edit",
                 "has_existing_coverage": True, "covering_ids": [42]},
            ],
            "shortlist": [
                {"id": 42, "title": "Guard before edit", "category": "nexo-ops",
                 "applies_to": "", "content_preview": "Run guard before editing."},
            ],
            "contradiction_pairs": [],
            "supersession_stubs": [],
            "stale_candidates": [],
            "preference_key_dupes": [],
            "truncated": False,
        }

    module = _load_module(
        monkeypatch,
        run_automation_prompt=fake_run,
        build_consolidation_brief=fake_brief,
    )
    monkeypatch.setattr(module, "log", lambda msg: None)

    data = {
        "date": "2026-06-14",
        "diaries": [
            {
                "id": 1,
                "session_id": "s1",
                "summary": "Edited code",
                "self_critique": "I should always run guard before editing code.",
                "user_signals": "",
                "mental_state": "",
                "domain": "nexo",
                "created_at": "2026-06-14T10:00:00",
            }
        ],
        "existing_feedbacks": ["feedback_postmortem_guard"],
        "history_summary": {"recent_rules": []},
    }

    assert module.consolidate_with_cli(data) is True

    allowed = captured["kwargs"]["allowed_tools"]
    assert "nexo_learning_add" in allowed
    assert "mcp__nexo__*" not in allowed
    assert "nexo_learning_list" not in allowed
    assert "nexo_learning_search" not in allowed

    # The rendered prompt must carry the precomputed brief section + the corpus
    # size from the brief, and the explicit do-not-rescan instruction.
    prompt = captured["prompt"]
    assert "PRECOMPUTED CORPUS ANALYSIS" in prompt
    assert "879" in prompt
    assert "Do NOT call nexo_learning_list" in prompt
