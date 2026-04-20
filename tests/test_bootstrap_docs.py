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

    text = bootstrap_docs.render_bootstrap_template(
        "codex",
        nexo_home=runtime,
    )

    assert "You are Nero" in text
    assert "You are NEXO" not in text
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
