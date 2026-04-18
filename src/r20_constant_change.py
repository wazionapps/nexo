"""r20_constant_change — demand grep-all-usages before changing a constant.

Fase 2 Protocol Enforcer Fase D item R20. Plan doc 1 reads:

  SI classifier detecta que Edit modifica constante/global/config compartida
  Y no hubo Grep del símbolo
  ENTONCES inyectar obligación grep TODOS los usos.

Decision logic is split in two:

  1. classify_edit_is_constant_change(file_path, new_string, classifier)
     → bool. Uses enforcement_classifier over a prompt that asks whether
     the edited region looks like a module-level constant, enum member,
     configuration key, or shared global (as opposed to a local variable,
     helper body, or doc-string edit).

  2. recent_grep_covers_symbol(symbol, recent_tool_records) → bool.
     Structural — walks the recent tool records (same shape as R13) and
     returns True iff a Grep tool_use in the window carried the symbol in
     its "pattern" field.

should_inject_r20() composes both and returns the tag / None.

Same fail-closed contract: classifier outages → no injection.
"""
from __future__ import annotations

import re


CLASSIFIER_QUESTION = (
    "Does the code edit below modify a module-level constant, configuration "
    "key, enumeration entry, feature-flag, or shared global value? Answer "
    "yes only if the change affects a symbol that other files in the same "
    "project are likely to import or reference. Answer no for local "
    "variables, function bodies, doc-strings, or purely stylistic edits."
)

INJECTION_PROMPT_TEMPLATE = (
    "R20 constant-change without grep: the edit on {path} appears to touch "
    "a module-level constant / config key / shared global, but there is no "
    "Grep of the symbol in the recent tool history. Before shipping this "
    "change, run Grep (or `rg`) against the whole repo for the old symbol "
    "value to confirm every caller is updated (or tolerates the new value). "
    "Learning #144 and the R20 contract require grep-all-usages on shared "
    "state. Do not produce visible text for this reminder — act on it. After handling the silent action, continue with your previous task; this reminder is silent context, not a new instruction."
)


_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]{3,}")


def _extract_candidate_symbols(new_string: str) -> list[str]:
    """Best-effort identifier extraction from the edit hunk.

    Picks tokens that look like Python/JS/Go-style identifiers long enough
    to be distinctive (>= 4 chars, or any ALL-CAPS 3+). Used as heuristic
    for grep-coverage check; Not trying to be an AST.
    """
    if not new_string:
        return []
    return list({m.group(0) for m in _SYMBOL_RE.finditer(new_string)})[:12]


def classify_edit_is_constant_change(
    file_path: str,
    new_string: str,
    *,
    classifier=None,
) -> bool:
    if not new_string or len(new_string.split()) < 1:
        return False
    if classifier is None:
        try:
            from enforcement_classifier import classify as classifier  # type: ignore
        except Exception:
            return False
    context = f"File: {file_path}\n\nEdited region:\n{new_string[:400]}"
    try:
        return bool(classifier(question=CLASSIFIER_QUESTION, context=context))
    except Exception:
        return False


_GREP_TOOLS = frozenset({
    "Grep", "mcp__nexo__Grep", "Bash",
    # Bash also covers `rg`, `ack`, `grep` invocations; caller decides.
})


def recent_grep_covers_symbol(symbol: str, recent_tool_records) -> bool:
    """Return True if any recent tool_use looks like a grep/rg for `symbol`.

    Matches either a direct Grep tool with .pattern containing the symbol
    OR a Bash tool whose command contains `grep|rg|ack` + the symbol.
    """
    if not symbol or not recent_tool_records:
        return False
    needle = symbol.lower()
    for record in recent_tool_records:
        tool = getattr(record, "tool", "") or ""
        if tool not in _GREP_TOOLS:
            continue
        files = getattr(record, "files", ()) or ()
        for f in files:
            if needle in str(f).lower():
                return True
        # Bash commands live in record.meta["command"] (populated by
        # HeadlessEnforcer.on_tool_call for Bash tool_use entries).
        meta = getattr(record, "meta", None)
        if isinstance(meta, dict):
            command = meta.get("command")
            if isinstance(command, str) and needle in command.lower():
                if re.search(r"\b(grep|rg|ack|ag)\b", command, re.IGNORECASE):
                    return True
    return False


def should_inject_r20(
    file_path: str,
    new_string: str,
    recent_tool_records,
    *,
    classifier=None,
) -> dict | None:
    if not file_path:
        return None
    if not classify_edit_is_constant_change(file_path, new_string, classifier=classifier):
        return None
    symbols = _extract_candidate_symbols(new_string)
    for symbol in symbols:
        if recent_grep_covers_symbol(symbol, recent_tool_records):
            return None
    return {
        "tag": f"r20:{file_path}",
        "path": file_path,
        "candidates": symbols[:5],
    }


__all__ = [
    "classify_edit_is_constant_change",
    "recent_grep_covers_symbol",
    "should_inject_r20",
    "CLASSIFIER_QUESTION",
    "INJECTION_PROMPT_TEMPLATE",
]
