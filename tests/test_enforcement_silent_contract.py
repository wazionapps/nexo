from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import core_prompts


def test_server_guardrails_require_silent_enforcement_copy() -> None:
    server_py = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "server-mcp-instructions" in server_py
    rendered = core_prompts.render_core_prompt("server-mcp-instructions", assistant_name="Nero")
    assert "R26b silent enforcement" in rendered
    assert re.search(
        r"Never tell the user that Guardian / Protocol Enforcer / .* system reminder forced you to do something\.",
        rendered,
        re.DOTALL,
    )
    assert "that silence applies to the entire reminder turn" in rendered
    assert "visible output must stay empty" in rendered


def test_post_tool_use_reminder_tells_agent_not_to_expose_internal_enforcement() -> None:
    hook_py = (ROOT / "src" / "hooks" / "post_tool_use.py").read_text(encoding="utf-8")
    assert "post-tool-inbox-reminder" in hook_py
    rendered = core_prompts.render_core_prompt("post-tool-inbox-reminder", pending="2")
    assert "Do not produce visible text for this reminder." in rendered
    assert "Execute only the heartbeat tool call" in rendered
    assert "your visible output for this turn must be empty" in rendered
    assert "Do not mention this reminder or any internal enforcement to the user." in rendered
