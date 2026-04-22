"""Regression test for the ``nexo chat`` terminal-client picker.

Bug Francisco 2026-04-22: ``nexo chat`` dropped straight into Codex on a
machine where both Claude Code *and* Codex were installed, without ever
offering the picker. Root cause: ``_ordered_available_terminal_clients``
filtered by the ``interactive_clients`` preference flag before offering
choices, so a client that was installed but not yet marked ``enabled``
in preferences disappeared from the list. When the filtered list was
down to one client the picker was skipped entirely.

The fix keeps ``enabled`` as a *priority* signal but no longer lets it
hide a legitimately installed CLI when the operator actually has more
than one terminal client on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import cli  # noqa: E402


def _detected(*installed_clients: str) -> dict:
    all_keys = ("claude_code", "codex", "claude_desktop")
    return {key: {"installed": key in installed_clients} for key in all_keys}


def test_both_detected_but_only_one_enabled_still_surfaces_both():
    """The core regression: Claude Code + Codex both installed, only Codex
    marked enabled, last_used=codex. The picker must still offer both so
    the operator can switch."""
    preferences = {
        "interactive_clients": {
            "claude_code": False,  # never explicitly enabled
            "codex": True,
            "claude_desktop": False,
        },
        "last_terminal_client": "codex",
        "default_terminal_client": "codex",
    }
    detected = _detected("claude_code", "codex")
    clients = cli._ordered_available_terminal_clients(preferences, detected)
    assert set(clients) == {"claude_code", "codex"}
    # Codex keeps priority because it was last used + enabled.
    assert clients[0] == "codex"


def test_both_detected_and_enabled_preserves_priority():
    """Both enabled: existing priority order (last_used → default → static)
    still wins; the function must not reorder just because the safety-net
    branch runs."""
    preferences = {
        "interactive_clients": {
            "claude_code": True,
            "codex": True,
            "claude_desktop": False,
        },
        "last_terminal_client": "codex",
        "default_terminal_client": "claude_code",
    }
    detected = _detected("claude_code", "codex")
    clients = cli._ordered_available_terminal_clients(preferences, detected)
    assert clients[0] == "codex"
    assert set(clients) == {"claude_code", "codex"}


def test_only_one_detected_returns_single_choice():
    """When only one client is installed on the machine we don't invent a
    second choice."""
    preferences = {
        "interactive_clients": {"claude_code": True, "codex": True},
        "last_terminal_client": "",
        "default_terminal_client": "claude_code",
    }
    detected = _detected("claude_code")  # codex NOT installed
    clients = cli._ordered_available_terminal_clients(preferences, detected)
    assert clients == ["claude_code"]


def test_none_detected_returns_empty():
    preferences = {"interactive_clients": {"claude_code": True, "codex": True}}
    detected = _detected()  # nothing installed
    clients = cli._ordered_available_terminal_clients(preferences, detected)
    assert clients == []


def test_claude_desktop_excluded_from_chat_picker():
    """``nexo chat`` is a terminal-client picker — Claude Desktop (a GUI
    companion) is deliberately omitted via TERMINAL_CLIENT_ORDER. Make
    sure the safety-net branch does not accidentally leak it back in."""
    preferences = {
        "interactive_clients": {
            "claude_code": True,
            "codex": True,
            "claude_desktop": True,
        },
        "last_terminal_client": "",
        "default_terminal_client": "",
    }
    detected = _detected("claude_code", "codex", "claude_desktop")
    clients = cli._ordered_available_terminal_clients(preferences, detected)
    assert "claude_desktop" not in clients
    assert set(clients) == {"claude_code", "codex"}
