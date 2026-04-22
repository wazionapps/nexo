"""Contract: no reserved product identity (NEXO/NEXO Brain/NEXO Desktop)
should ever leak as a fallback agent name. The agent identity must always
resolve to either the user-chosen name stored in calibration, the canonical
default, or an equivalent neutral placeholder — never the product name.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _reload(name: str):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def test_default_assistant_name_is_not_reserved():
    user_context = _reload("user_context")
    desktop_bridge = _reload("desktop_bridge")
    assert user_context.DEFAULT_ASSISTANT_NAME not in desktop_bridge.RESERVED_ASSISTANT_NAME_VALUES


def test_reserved_values_cover_nexo_product_identities():
    desktop_bridge = _reload("desktop_bridge")
    reserved = set(desktop_bridge.RESERVED_ASSISTANT_NAME_VALUES)
    # NEXO, NEXO Brain, and NEXO Desktop are the three canonical product names.
    assert "NEXO" in reserved
    assert "NEXO Brain" in reserved
    assert "NEXO Desktop" in reserved


def test_resolve_placeholders_does_not_leak_reserved_name(monkeypatch, tmp_path):
    # Force _resolve_placeholders to exercise its except-path: pretend
    # user_context.get_context explodes. The fallback must route through
    # DEFAULT_ASSISTANT_NAME, never a hard-coded "NEXO".
    user_context = _reload("user_context")
    auto_update = _reload("auto_update")
    desktop_bridge = _reload("desktop_bridge")

    class _Boom(Exception):
        pass

    def _boom():
        raise _Boom("intentional")

    monkeypatch.setattr(auto_update, "NEXO_HOME", tmp_path)
    monkeypatch.setattr(user_context, "get_context", _boom)

    out = auto_update._resolve_placeholders("hi {{NAME}}, home={{NEXO_HOME}}")

    # The rendered name must not be a reserved product identity.
    for reserved in desktop_bridge.RESERVED_ASSISTANT_NAME_VALUES:
        # Use word-boundary so "NEXO Brain" inside a longer sentence still
        # gets caught, but the literal "{{NAME}}" placeholder obviously never
        # survives rendering.
        assert re.search(rf"\b{re.escape(reserved)}\b", out) is None, (
            f"Fallback leaked reserved product identity {reserved!r}: {out!r}"
        )
    assert user_context.DEFAULT_ASSISTANT_NAME in out


def test_auto_update_sourcefile_has_no_nexo_name_literal():
    """Regression against the two hard-coded ``_name = 'NEXO'`` fallbacks that
    used to live in ``src/auto_update.py``. Keeping this as an explicit grep
    stops future refactors from reintroducing the reserved product identity
    as an agent-name fallback. Matches are allowed inside comments only when
    they refer to the reserved identity set, not as an assignment to ``name``.
    """
    source = (SRC / "auto_update.py").read_text()
    # No assignment ``_name = "NEXO"`` / ``name = "NEXO"`` at any indent.
    for pattern in (r'^\s*_name\s*=\s*"NEXO"\s*$', r'^\s*name\s*=\s*"NEXO"\s*$'):
        assert re.search(pattern, source, flags=re.MULTILINE) is None, (
            f"auto_update.py re-introduced a reserved NEXO fallback via {pattern!r}"
        )
