"""r21_legacy_path — warn when a command / edit touches a legacy path.

Fase 2 Protocol Enforcer Fase D item R21. Plan doc 1 reads:

  SI intent Bash o Edit con path contiene entrada de entity_list
     type=legacy_path
  ENTONCES inyectar recordatorio canónico.

Pure decision module. Legacy-path entities are seeded at `nexo init`
from src/presets/entities_universal.json (the ~/claude → ~/.nexo mapping).
Operators may add more via nexo_entity_create.
"""
from __future__ import annotations


INJECTION_PROMPT_TEMPLATE = (
    "R21 legacy path: the operation targets '{legacy}' but that path is "
    "superseded by '{canonical}' in the entity registry (type=legacy_path). "
    "Rerun the operation against the canonical path. Operators on v6+ "
    "runtime do not keep the legacy tree alive; writes there end up "
    "orphaned at the next maintenance sweep. Do not produce visible text "
    "for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
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
