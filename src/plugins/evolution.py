"""Legacy Evolution plugin module.

Evolution is no longer a callable NEXO Desktop capability. The module remains
only so older imports fail closed instead of re-exposing MCP tools when all
plugins are loaded in legacy mode.
"""

from __future__ import annotations


RETIRED_MESSAGE = "Evolution has been removed from NEXO Desktop."
TOOLS: list[dict] = []


def _retired_message() -> str:
    return RETIRED_MESSAGE


def handle_evolution_status() -> str:
    """Compatibility shim for old imports."""
    return _retired_message()


def handle_evolution_history(limit: int = 10) -> str:
    """Compatibility shim for old imports."""
    return _retired_message()


def handle_evolution_propose() -> str:
    """Compatibility shim for old imports."""
    return _retired_message()


def handle_evolution_approve(log_id: int, notes: str = "") -> str:
    """Compatibility shim for old imports."""
    return _retired_message()


def handle_evolution_reject(log_id: int, reason: str = "") -> str:
    """Compatibility shim for old imports."""
    return _retired_message()
