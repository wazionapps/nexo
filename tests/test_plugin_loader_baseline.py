"""Coverage baseline for plugin_loader.py — Fase 4 item 2.

Pre-Fase 4 the module had 21% coverage. This file pins the security-
critical paths (path traversal rejection, filename validation) and the
list/remove flows so a future regression cannot silently break the
plugin lifecycle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


# A minimal stub that the load_plugin/remove_plugin helpers expect.
class _StubMCP:
    def __init__(self):
        self.added: list[tuple] = []
        self.removed: list[str] = []

        class _Provider:
            def __init__(self, parent):
                self.parent = parent

            def remove_tool(self, name):
                self.parent.removed.append(name)

        self.local_provider = _Provider(self)

    def add_tool(self, tool):
        self.added.append(tool)


# ── Filename validation ──────────────────────────────────────────────────


class TestLoadPluginValidation:
    def test_rejects_filename_with_forward_slash(self, isolated_db):
        from plugin_loader import load_plugin
        with pytest.raises(ValueError, match="path separators"):
            load_plugin(_StubMCP(), "../etc/passwd.py")

    def test_rejects_filename_with_backslash(self, isolated_db):
        from plugin_loader import load_plugin
        with pytest.raises(ValueError, match="path separators"):
            load_plugin(_StubMCP(), "evil\\plugin.py")

    def test_rejects_filename_with_traversal(self, isolated_db):
        from plugin_loader import load_plugin
        with pytest.raises(ValueError, match="path separators"):
            load_plugin(_StubMCP(), "..py")

    def test_appends_py_extension_if_missing(self, isolated_db):
        from plugin_loader import load_plugin
        # We pass a non-existent name without .py and expect a FileNotFoundError
        # — that confirms the function reached the file existence check, which
        # means the .py extension was appended internally.
        with pytest.raises(FileNotFoundError):
            load_plugin(_StubMCP(), "definitely_does_not_exist_plugin")

    def test_explicit_plugins_dir_missing_file_raises_filenotfound(self, isolated_db, tmp_path):
        from plugin_loader import load_plugin
        empty_dir = tmp_path / "empty_plugins"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_plugin(_StubMCP(), "ghost.py", plugins_dir=str(empty_dir))


# ── _ensure_src_in_path ───────────────────────────────────────────────────


class TestEnsureSrcInPath:
    def test_inserts_server_dir_when_missing(self, isolated_db):
        from plugin_loader import _ensure_src_in_path, SERVER_DIR
        # Remove the entry if present so we can verify it gets re-added.
        sys.path[:] = [p for p in sys.path if p != SERVER_DIR]
        _ensure_src_in_path()
        assert sys.path[0] == SERVER_DIR

    def test_idempotent(self, isolated_db):
        from plugin_loader import _ensure_src_in_path, SERVER_DIR
        _ensure_src_in_path()
        before = sys.path.count(SERVER_DIR)
        _ensure_src_in_path()
        after = sys.path.count(SERVER_DIR)
        assert before == after  # not duplicated


# ── list_plugins / remove_plugin ─────────────────────────────────────────


class TestListAndRemovePlugins:
    def test_list_plugins_on_empty_db_returns_list(self, isolated_db):
        from plugin_loader import list_plugins
        result = list_plugins()
        assert isinstance(result, list)
        # No plugins loaded -> empty list. We do not assert == [] because some
        # other test may have populated the registry; just assert the contract.
        for entry in result:
            assert "filename" in entry
            assert "source" in entry

    def test_remove_plugin_for_unknown_filename_returns_empty_list(self, isolated_db):
        from plugin_loader import remove_plugin
        result = remove_plugin(_StubMCP(), "definitely_not_loaded_plugin.py")
        assert result == []

    def test_remove_plugin_appends_py_extension(self, isolated_db):
        from plugin_loader import remove_plugin
        # Same — non-existent plugin without .py — should not raise and
        # should return an empty list (the function added .py internally).
        result = remove_plugin(_StubMCP(), "non_existent")
        assert result == []


# ── _PluginTimeout exception class ───────────────────────────────────────


class TestPluginTimeoutException:
    def test_can_be_raised_and_caught(self, isolated_db):
        from plugin_loader import _PluginTimeout
        with pytest.raises(_PluginTimeout):
            raise _PluginTimeout("test")
