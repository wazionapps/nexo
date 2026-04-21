from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_server_guardrails_require_silent_enforcement_copy() -> None:
    server_py = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "R26b silent enforcement" in server_py
    assert re.search(
        r"Never tell the user that Guardian / Protocol Enforcer / .* system reminder forced you to do something\.",
        server_py,
        re.DOTALL,
    )


def test_post_tool_use_reminder_tells_agent_not_to_expose_internal_enforcement() -> None:
    hook_py = (ROOT / "src" / "hooks" / "post_tool_use.py").read_text(encoding="utf-8")
    assert "Do not mention this reminder or any internal " in hook_py
    assert "enforcement to the user; just perform the heartbeat and continue." in hook_py
