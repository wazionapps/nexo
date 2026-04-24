from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_render_bootstrap_template_prefers_calibration_assistant_name(tmp_path):
    import bootstrap_docs

    runtime = tmp_path / "nexo-home"
    calibration_dir = runtime / "personal" / "brain"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    (calibration_dir / "calibration.json").write_text(json.dumps({
        "user": {
            "assistant_name": "Nero",
            "name": "Francisco",
            "language": "es",
        }
    }))
    (runtime / "version.json").write_text(json.dumps({"operator_name": ""}))

    for client in ("claude_code", "codex"):
        text = bootstrap_docs.render_bootstrap_template(
            client,
            nexo_home=runtime,
        )

        assert "Nero" in text
        assert "You are NEXO" not in text
        assert "## User-Facing Agent Contract" in text
        assert "The user-facing agent identity is Nero." in text
        assert "do not present as Claude, Codex, OpenAI, Anthropic, a generic model" in text
        assert "If the user asks who you are, answer as Nero." in text
        assert "Before denying memory, authorship" in text
        assert "## Professional Autonomy And Safety" in text
        assert "Do not use \"I can't\" as the first response." in text
        assert "install missing tools when safe" in text
        assert "Ask the user only for real decisions, missing credentials" in text
        assert "never present yourself as a generic model or LLM" in text
        assert "docs/agent-product-playbook.md" in text
        assert "docs/product-operator-wiki.md" in text
        assert "docs/solution-playbook.md" in text
        assert "I am Nero. Nero is a single operational identity." in text
        assert "No decir" not in text
        assert "La LLM subyacente" not in text


def test_render_bootstrap_template_falls_back_to_neutral_assistant_name(tmp_path):
    import bootstrap_docs

    runtime = tmp_path / "nexo-home"
    (runtime / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (runtime / "version.json").write_text(json.dumps({"operator_name": ""}))

    text = bootstrap_docs.render_bootstrap_template(
        "codex",
        nexo_home=runtime,
    )

    assert "You are Nova" in text
    assert "You are NEXO" not in text
    assert "Mission: help the operator reach the real outcome" in text
    assert "docs/product-operator-wiki.md" in text
    assert "docs/solution-playbook.md" in text
    assert "I am Nova. Nova is a single operational identity." in text


def test_bootstrap_user_facing_contract_keeps_claude_and_codex_aligned(tmp_path):
    import bootstrap_docs

    runtime = tmp_path / "nexo-home"
    (runtime / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (runtime / "version.json").write_text(json.dumps({"operator_name": "Nova"}))

    claude_text = bootstrap_docs.render_bootstrap_template("claude_code", nexo_home=runtime)
    codex_text = bootstrap_docs.render_bootstrap_template("codex", nexo_home=runtime)

    required_fragments = [
        "## User-Facing Agent Contract",
        "The user-facing agent identity is Nova.",
        "In normal user conversation, do not present as Claude, Codex, OpenAI, Anthropic",
        "The user should experience one continuous professional agent",
        "Before denying memory, authorship",
        "## Professional Autonomy And Safety",
        "Do not use \"I can't\" as the first response.",
        "First try safe available paths",
        "Use the user's language and adapt detail to their role and technical level.",
    ]
    for fragment in required_fragments:
        assert fragment in claude_text
        assert fragment in codex_text


def test_sync_client_bootstrap_preserves_user_block_when_core_updates(tmp_path, monkeypatch):
    import bootstrap_docs

    runtime = tmp_path / "nexo-home"
    home = tmp_path / "home"
    (runtime / "personal" / "brain").mkdir(parents=True, exist_ok=True)
    (runtime / "version.json").write_text(json.dumps({"operator_name": "Nero"}))
    monkeypatch.setenv("NEXO_HOME", str(runtime))

    target = home / ".codex" / "AGENTS.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "<!-- nexo-codex-agents-version: 0.0.1 -->\n"
        "******CORE******\n"
        "<!-- nexo:core:start -->\n"
        "old managed core\n"
        "<!-- nexo:core:end -->\n\n"
        "******USER******\n"
        "<!-- nexo:user:start -->\n"
        "# Personal Instructions\n"
        "Keep this custom operator rule.\n"
        "<!-- nexo:user:end -->\n"
    )

    result = bootstrap_docs.sync_client_bootstrap(
        "codex",
        nexo_home=runtime,
        user_home=home,
    )

    text = target.read_text()
    assert result["ok"] is True
    assert result["action"] == "updated"
    assert "old managed core" not in text
    assert "## User-Facing Agent Contract" in text
    assert "The user-facing agent identity is Nero." in text
    assert "Keep this custom operator rule." in text
    assert text.count("<!-- nexo:user:start -->") == 1
    assert text.count("<!-- nexo:user:end -->") == 1
