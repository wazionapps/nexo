from __future__ import annotations

import local_context
from tools_hot_context import handle_pre_action_context


def test_pre_action_context_includes_local_evidence(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "maria-seguro.txt"
    path.write_text("Maria tiene un seguro de coche con BMW adjunto en este expediente.", encoding="utf-8")

    local_context.add_root(str(root))
    local_context.run_once(limit=20, process_limit=20)

    rendered = handle_pre_action_context(query="que sabes sobre Maria y seguro coche", limit=4)

    assert "LOCAL CONTEXT EVIDENCE" in rendered
    assert "maria-seguro.txt" in rendered
    assert "Local relations:" in rendered
