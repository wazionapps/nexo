"""Tests for ``auto_update._run_post_install_hooks_fresh`` (v7.3.0).

Fixes the post-v7.2.0 bug where ``_run_runtime_post_sync`` called newly
added post-install hooks against the already-loaded (old) module. Any
function added to ``auto_update`` in the current release never ran on
the first ``nexo update`` introducing it. The fresh-hooks helper
delegates to a clean subprocess that imports from the freshly-copied
tree, so a v7.2.0 → v7.3.0 upgrade sees every post-install hook fire on
the first run — no second ``nexo update`` needed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def fresh_tree(tmp_path):
    """Build a minimal fake 'freshly-copied' tree at tmp/core/ that exposes
    a stub auto_update module with two hook functions the parent calls."""
    core = tmp_path / "core"
    core.mkdir(parents=True)
    (core / "auto_update.py").write_text(
        "def _persist_guardian_hard_defaults(dest):\n"
        "    (dest / 'persisted.marker').write_text('ok')\n"
        "    return True, None\n"
        "def _maybe_promote_adaptive_weights_empirically(dest):\n"
        "    (dest / 'promoted.marker').write_text('ok')\n"
        "    return False, 'adaptive-promote-skipped:no-data'\n"
    )
    return tmp_path


def test_fresh_hooks_runs_from_copied_tree_and_reports_actions(fresh_tree, monkeypatch):
    import auto_update
    monkeypatch.setenv("NEXO_HOME", str(fresh_tree))
    actions = auto_update._run_post_install_hooks_fresh(fresh_tree)
    # changed=True for persist → tag appears; skipped promote → message appears.
    assert "guardian-hard-persisted" in actions
    assert any("adaptive-promote-skipped:no-data" in a for a in actions)
    assert (fresh_tree / "persisted.marker").is_file()
    assert (fresh_tree / "promoted.marker").is_file()


def test_fresh_hooks_handles_missing_function_gracefully(tmp_path, monkeypatch):
    """Legacy installs where one of the whitelisted hooks is not yet in the
    tree: the subprocess must not crash, just surface ``fn_missing``."""
    core = tmp_path / "core"
    core.mkdir(parents=True)
    (core / "auto_update.py").write_text(
        "def _persist_guardian_hard_defaults(dest):\n"
        "    return True, None\n"
        # _maybe_promote_adaptive_weights_empirically intentionally absent.
    )
    import auto_update
    actions = auto_update._run_post_install_hooks_fresh(tmp_path)
    assert "guardian-hard-persisted" in actions
    assert any("fn_missing:" in a for a in actions)


def test_fresh_hooks_uses_dest_root_when_core_absent(tmp_path):
    """Dev/legacy installs without a ``core/`` subdir: fallback to ``dest``."""
    (tmp_path / "auto_update.py").write_text(
        "def _persist_guardian_hard_defaults(dest):\n"
        "    (dest / 'persisted-no-core.marker').write_text('ok')\n"
        "    return True, None\n"
        "def _maybe_promote_adaptive_weights_empirically(dest):\n"
        "    return False, None\n"
    )
    import auto_update
    actions = auto_update._run_post_install_hooks_fresh(tmp_path)
    assert "guardian-hard-persisted" in actions
    assert (tmp_path / "persisted-no-core.marker").is_file()


def test_fresh_hooks_survives_corrupt_auto_update_import(tmp_path):
    """Broken auto_update in the fresh tree must surface as warning, not crash."""
    core = tmp_path / "core"
    core.mkdir(parents=True)
    (core / "auto_update.py").write_text("raise RuntimeError('synthetic')\n")
    import auto_update
    actions = auto_update._run_post_install_hooks_fresh(tmp_path)
    assert any("import_auto_update_failed" in a for a in actions)


def test_fresh_hooks_survives_hook_exception_per_entry(tmp_path):
    core = tmp_path / "core"
    core.mkdir(parents=True)
    (core / "auto_update.py").write_text(
        "def _persist_guardian_hard_defaults(dest):\n"
        "    raise ValueError('boom')\n"
        "def _maybe_promote_adaptive_weights_empirically(dest):\n"
        "    return True, None\n"
    )
    import auto_update
    actions = auto_update._run_post_install_hooks_fresh(tmp_path)
    # The second hook still runs even though the first one blew up.
    assert "adaptive-weights-promoted" in actions
    assert any("error:ValueError" in a for a in actions)
