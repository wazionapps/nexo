from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "tool-enforcement-map.json"


def test_map_silent_prompts_define_turn_wide_contract() -> None:
    payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    missing: list[str] = []
    for tool_name, meta in (payload.get("tools") or {}).items():
        enforcement = (meta or {}).get("enforcement") or {}
        for field in ("inject_prompt", "session_end_inject_prompt"):
            prompt = str(enforcement.get(field, "") or "")
            if "Do not produce visible text" not in prompt:
                continue
            if "entire reminder turn" not in prompt or "visible output must stay empty" not in prompt:
                missing.append(f"{tool_name}.{field}")
    assert not missing, (
        "Silent reminder prompts in tool-enforcement-map.json must define the "
        "turn-wide silence contract. Missing: " + ", ".join(sorted(missing))
    )
