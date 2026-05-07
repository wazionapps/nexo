from __future__ import annotations

import ast
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "scripts" / "nexo-daily-self-audit.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_self_audit_inline_batch_limit_is_at_least_50():
    tree = ast.parse(_source())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value

    limit = values.get("SELF_AUDIT_INLINE_BATCH_LIMIT")
    assert isinstance(limit, ast.Constant)
    assert int(limit.value) >= 50


def test_self_audit_no_longer_caps_prevention_and_formalization_at_five():
    source = _source()
    assert "list(repeated.items())[:5]" not in source
    assert "list(loose_topics.items())[:5]" not in source
    assert source.count("[:SELF_AUDIT_INLINE_BATCH_LIMIT]") >= 3
