"""Tests for R13 pre-edit guard decision logic (Fase 2 spec 0.14 spike)."""
from __future__ import annotations

import time
import pytest


def test_watched_tool_without_guard_injects():
    from r13_pre_edit_guard import should_inject_r13
    now = 1000.0
    assert should_inject_r13("Edit", ["/x/a.py"], [], current_ts=now) == "r13:/x/a.py"


def test_guard_within_window_suppresses():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    recent = [ToolCallRecord("nexo_guard_check", now - 5, ("/x/a.py",))]
    assert should_inject_r13("Edit", ["/x/a.py"], recent, current_ts=now) is None


def test_guard_on_different_path_still_injects():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    recent = [ToolCallRecord("nexo_guard_check", now - 5, ("/y/b.py",))]
    assert should_inject_r13("Edit", ["/x/a.py"], recent, current_ts=now) == "r13:/x/a.py"


def test_guard_outside_time_window_injects():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    recent = [ToolCallRecord("nexo_guard_check", now - 120, ("/x/a.py",))]
    assert should_inject_r13("Edit", ["/x/a.py"], recent, current_ts=now, window_seconds=60) == "r13:/x/a.py"


def test_non_watched_tool_never_fires():
    from r13_pre_edit_guard import should_inject_r13
    assert should_inject_r13("Bash", [], [], current_ts=1000.0) is None
    assert should_inject_r13("Read", [], [], current_ts=1000.0) is None
    assert should_inject_r13("Grep", [], [], current_ts=1000.0) is None


def test_mcp_nexo_prefix_variant_matches():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    recent = [ToolCallRecord("mcp__nexo__nexo_guard_check", now - 1, ("/x/a.py",))]
    assert should_inject_r13("Edit", ["/x/a.py"], recent, current_ts=now) is None


def test_unknown_target_without_path():
    from r13_pre_edit_guard import should_inject_r13
    assert should_inject_r13("Edit", [], [], current_ts=1000.0) == "r13:unknown-target"


def test_path_less_edit_allows_any_recent_guard():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    recent = [ToolCallRecord("nexo_guard_check", now - 3, ("/z/c.py",))]
    # Edit without a specific path target is considered announced by any recent guard.
    assert should_inject_r13("Edit", [], recent, current_ts=now) is None


def test_window_calls_exhausted_injects():
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord
    now = 1000.0
    # guard_check is OLDER than the last 30 calls
    recent = [ToolCallRecord("nexo_guard_check", now - 1, ("/x/a.py",))]
    for i in range(35):
        recent.append(ToolCallRecord("Bash", now - 0.5 + i * 0.01, ()))
    assert should_inject_r13("Edit", ["/x/a.py"], recent, window_calls=30, current_ts=now) == "r13:/x/a.py"


def test_dedup_tag_stable():
    from r13_pre_edit_guard import should_inject_r13
    now = 1000.0
    # Same path → same tag (enables engine-level dedup)
    tag1 = should_inject_r13("Edit", ["/x/a.py"], [], current_ts=now)
    tag2 = should_inject_r13("Edit", ["/x/a.py"], [], current_ts=now + 10)
    assert tag1 == tag2 == "r13:/x/a.py"


def test_different_paths_get_different_tags():
    from r13_pre_edit_guard import should_inject_r13
    now = 1000.0
    assert should_inject_r13("Edit", ["/x/a.py"], [], current_ts=now) == "r13:/x/a.py"
    assert should_inject_r13("Edit", ["/x/b.py"], [], current_ts=now) == "r13:/x/b.py"
