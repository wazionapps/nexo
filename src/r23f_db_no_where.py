"""R23f — production DB DELETE/UPDATE without WHERE.

Pure decision module. Part of Fase D2 (hard bloqueante).
Fires when Bash runs a SQL-bearing command against an entity of
type=db env=production and the SQL lacks a WHERE clause on the target
table.
"""
from __future__ import annotations

import re


INJECTION_PROMPT_TEMPLATE = (
    "R23f destructive SQL without WHERE: the command '{cmd}' contains a "
    "{verb} statement with no WHERE clause against a production DB. "
    "Abort. If the intent is a full wipe, run it interactively against "
    "the DB console with explicit operator confirmation, not via the "
    "enforcement wrapper."
)

# Simple SQL heuristic. Looks for DELETE FROM <tbl> or UPDATE <tbl> SET
# without a trailing WHERE before the next statement boundary
# (`;`, newline, pipe, backtick closer, or end of string).
# Regex variants:
#   _SQL_SEG_RE           — single-line SQL (stops at newline).
#   _SQL_SEG_MULTILINE_RE — heredoc SQL (spans newlines, stops at ; or heredoc
#                           terminator). Used only when _HEREDOC_RE matches.
_SQL_SEG_RE = re.compile(
    r"\b(DELETE\s+FROM|UPDATE)\s+[\w.`\"\[\]]+"
    r"(?:\s+SET\s+[^;\n`]+)?"
    r"(?P<tail>[^;\n`]*)",
    re.IGNORECASE,
)
_SQL_SEG_MULTILINE_RE = re.compile(
    r"\b(DELETE\s+FROM|UPDATE)\s+[\w.`\"\[\]]+"
    r"(?:\s+SET\s+[^;`]+?)?"
    r"(?P<tail>[^;`]*?)(?:;|$)",
    re.IGNORECASE | re.DOTALL,
)
# Heredoc marker: `<<EOF`, `<<'END'`, `<<-EOF`, etc. When detected we scan
# the bash body with multiline-aware regex so WHERE on the next line counts.
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w+['\"]?")
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def detect_sql_no_where(cmd: str) -> tuple[bool, str, str]:
    """Return (blocks, verb, snippet). Handles both single-line SQL and
    heredoc-delimited multi-line SQL bodies."""
    if not cmd or not isinstance(cmd, str):
        return False, "", ""
    uses_heredoc = bool(_HEREDOC_RE.search(cmd))
    pattern = _SQL_SEG_MULTILINE_RE if uses_heredoc else _SQL_SEG_RE
    for match in pattern.finditer(cmd):
        verb = match.group(1).upper().replace("DELETE FROM", "DELETE")
        # Check WHERE on the full matched segment. For heredoc bodies, the
        # multiline regex captures up to `;` or end of string, so a WHERE
        # on the next line is inside match.group(0) and correctly suppresses
        # the warning.
        if _WHERE_RE.search(match.group(0)):
            continue
        if verb == "UPDATE" and " SET " not in match.group(0).upper():
            continue
        snippet = match.group(0).strip()
        # Compact multi-line snippet for the injection prompt.
        snippet_compact = " ".join(snippet.split())
        return True, verb, snippet_compact
    return False, "", ""


def _command_invokes_db(cmd: str) -> bool:
    """Heuristic: command pipes SQL to a DB client or embeds SQL in -e."""
    if not isinstance(cmd, str):
        return False
    patterns = [
        r"\bmysql\b",
        r"\bmariadb\b",
        r"\bpsql\b",
        r"\bmongosh?\b",
        r"\bredis-cli\b",
        r"\bsqlite3?\b",
        r"--execute=",
        r"-e\s+['\"]",
    ]
    return any(re.search(p, cmd, re.IGNORECASE) for p in patterns)


def should_inject_r23f(tool_name: str, tool_input, production_markers: list[str] | None = None) -> tuple[bool, str]:
    """Fire when SQL with no WHERE runs against suspected production.

    `production_markers` is a list of DB hostnames / connection URIs that
    entity registry flagged env=production. If any marker appears in the
    command string, treat the SQL as production-targeted. Default: any
    `mysql`/`psql`-family command is treated as production when no
    marker is supplied (conservative — better to warn than miss).
    """
    if tool_name != "Bash":
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False, ""
    if not _command_invokes_db(cmd):
        return False, ""
    blocked, verb, snippet = detect_sql_no_where(cmd)
    if not blocked:
        return False, ""
    if production_markers:
        if not any(m.lower() in cmd.lower() for m in production_markers if m):
            return False, ""
    prompt = INJECTION_PROMPT_TEMPLATE.format(verb=verb, cmd=snippet)
    return True, prompt
