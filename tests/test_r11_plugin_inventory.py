"""Tests for Plan Consolidado R11 — plugin_load pre-inventory.

We do NOT import plugin_loader's load_all_plugins (it needs an mcp
server). Instead we unit-test the pure helpers ``verify_plugin_in_inventory``
+ ``_collect_declared_plugin_tool_names``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import plugin_loader  # noqa: E402
from plugin_loader import (  # noqa: E402
    _collect_declared_plugin_tool_names,
    verify_plugin_in_inventory,
)


def _write_plugin(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def test_empty_plugin_passes(tmp_path):
    path = _write_plugin(tmp_path, "empty.py", "# no tools here\n")
    ok, reason = verify_plugin_in_inventory("empty.py", str(path))
    assert ok is True
    assert "no tools declared" in reason


def test_allow_listed_init_passes(tmp_path):
    path = _write_plugin(tmp_path, "__init__.py", "# scaffolding\n")
    ok, reason = verify_plugin_in_inventory("__init__.py", str(path))
    assert ok is True
    assert "allow-listed" in reason


def test_plugin_with_tool_not_in_map_is_rejected(tmp_path, monkeypatch):
    path = _write_plugin(tmp_path, "stray.py", '@mcp.tool\ndef nexo_fake_tool(): return "nexo_fake_tool"\n')
    # Force the map reader to return an empty-but-present set so the
    # gate runs its real reject path.
    monkeypatch.setattr(plugin_loader, "_collect_declared_plugin_names_from_map", lambda: {"nexo_other_tool"})
    ok, reason = verify_plugin_in_inventory("stray.py", str(path))
    assert ok is False
    assert "not present in tool-enforcement-map" in reason


def test_plugin_matching_map_entry_passes(tmp_path, monkeypatch):
    path = _write_plugin(tmp_path, "ok.py", '"""ok"""\nX = "nexo_known_tool"\n')
    monkeypatch.setattr(plugin_loader, "_collect_declared_plugin_names_from_map", lambda: {"nexo_known_tool"})
    ok, reason = verify_plugin_in_inventory("ok.py", str(path))
    assert ok is True
    assert "matched" in reason


def test_map_unavailable_soft_passes(tmp_path, monkeypatch):
    # Plugin declares a tool. Map unreadable → soft pass (empty set).
    path = _write_plugin(tmp_path, "soft.py", 'X = "nexo_anything"\n')
    monkeypatch.setattr(plugin_loader, "_collect_declared_plugin_names_from_map", lambda: set())
    ok, reason = verify_plugin_in_inventory("soft.py", str(path))
    assert ok is True
    assert "map unavailable" in reason


def test_collect_tool_names_regex_basic(tmp_path):
    src = 'abc = "nexo_foo_bar"\ny = "nexo_baz"\nother = "not_a_tool"\n'
    p = _write_plugin(tmp_path, "x.py", src)
    names = _collect_declared_plugin_tool_names(str(p))
    assert names == {"nexo_foo_bar", "nexo_baz"}
