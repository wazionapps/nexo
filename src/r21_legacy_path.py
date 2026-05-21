"""r21_legacy_path — warn when a command / edit touches a legacy path.

Phase 2 Protocol Enforcer Phase D item R21. Plan doc 1 reads:

  IF Bash or Edit intent path contains an entity_list entry
     type=legacy_path
  THEN inject the canonical reminder.

Pure decision module. Legacy-path entities are seeded at `nexo init`
from src/presets/entities_universal.json (the ~/claude → ~/.nexo mapping).
Operators may add more via nexo_entity_create.
"""
from __future__ import annotations

from core_prompts import render_core_prompt

INJECTION_PROMPT_TEMPLATE = render_core_prompt(
    "r21-legacy-path-injection",
    legacy="{legacy}",
    canonical="{canonical}",
)


def _resolve_tokens(tool_input) -> list[str]:
    """Best-effort list of strings that might contain a legacy path."""
    out: list[str] = []
    if not isinstance(tool_input, dict):
        return out
    for key in ("file_path", "path", "filepath"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            out.append(val)
    for key in ("file_paths", "paths"):
        arr = tool_input.get(key)
        if isinstance(arr, list):
            out.extend(str(p) for p in arr if p)
    cmd = tool_input.get("command")
    if isinstance(cmd, str) and cmd:
        out.append(cmd)
    return out


def should_inject_r21(
    tool_name: str,
    tool_input,
    legacy_mappings,
) -> dict | None:
    """Return {tag, legacy, canonical} when R21 should fire.

    legacy_mappings: list of {"old": str, "canonical": str}. `old` is a
    substring match against any path in tool_input.
    """
    if not legacy_mappings:
        return None
    tokens = _resolve_tokens(tool_input)
    if not tokens:
        return None
    import os as _os
    for mapping in legacy_mappings:
        if not isinstance(mapping, dict):
            continue
        old_str = str(mapping.get("old") or "").strip()
        canonical = str(mapping.get("canonical") or "").strip()
        if not old_str or not canonical:
            continue
        # Expand ~ and $HOME so comparisons match absolute paths too.
        candidates = {old_str}
        if "~" in old_str:
            candidates.add(_os.path.expanduser(old_str))
        for variant in candidates:
            for token in tokens:
                if variant in token:
                    return {
                        "tag": f"r21:{old_str}",
                        "legacy": old_str,
                        "canonical": canonical,
                    }
    return None


__all__ = [
    "should_inject_r21",
    "INJECTION_PROMPT_TEMPLATE",
]
