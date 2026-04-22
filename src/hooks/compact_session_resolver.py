"""Resolve the NEXO sid for compaction hook observability.

`hook_runs.session_id` must hold the NEXO sid (`nexo-NNNNNN-N`) so that a
query like "every compaction of session X" works without joining on the
raw Claude Code token. Pre-v7.8.2 the two Python wrappers stored
`os.environ.get("CLAUDE_SESSION_ID", "")` directly, which produced two
problems at once: rows with `session_id=''` when the env was missing,
and rows with the raw Claude token (not a NEXO sid) when it was
present. This helper centralises the resolution against the same rails
the shell scripts use.

Resolution order:
  1. ENV `CLAUDE_SESSION_ID` with `sessions.claude_session_id` match.
  2. ENV `CLAUDE_SESSION_ID` with `session_claude_aliases.claude_session_id`
     match (most recent `last_seen` wins).
  3. Per-conversation sidecar written by pre-compact.sh at
     the compacting folder under the runtime data dir.
  4. Legacy global sidecar at compacting-sid.txt (single-conv legacy path).

Returns (nexo_sid, source) so the caller can stash `source` in the
`hook_runs.metadata` JSON for debugging "why is this row still empty".
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

_NEXO_SID_RE = re.compile(r"^nexo-[0-9]+-[0-9]+$")
_SAFE_CLAUDE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]")


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _candidate_data_dirs() -> list[Path]:
    home = _nexo_home()
    dirs: list[Path] = []
    for cand in (home / "runtime" / "data", home / "data"):
        if cand not in dirs:
            dirs.append(cand)
    return dirs


def _db_path() -> Path | None:
    for d in _candidate_data_dirs():
        p = d / "nexo.db"
        if p.is_file():
            return p
    return None


def _safe_claude_id(claude_session_id: str) -> str:
    return _SAFE_CLAUDE_ID_RE.sub("_", claude_session_id or "")


def _read_sidecar(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return text if _NEXO_SID_RE.match(text) else ""


def _db_lookup(claude_session_id: str) -> tuple[str, str]:
    if not claude_session_id:
        return "", ""
    db = _db_path()
    if db is None:
        return "", ""
    try:
        conn = sqlite3.connect(str(db), timeout=3)
    except Exception:
        return "", ""
    try:
        try:
            row = conn.execute(
                "SELECT sid FROM sessions WHERE claude_session_id = ? LIMIT 1",
                (claude_session_id,),
            ).fetchone()
        except Exception:
            row = None
        if row and row[0] and _NEXO_SID_RE.match(row[0]):
            return row[0], "sessions"
        try:
            row = conn.execute(
                "SELECT sid FROM session_claude_aliases "
                "WHERE claude_session_id = ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (claude_session_id,),
            ).fetchone()
        except Exception:
            row = None
        if row and row[0] and _NEXO_SID_RE.match(row[0]):
            return row[0], "alias"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return "", ""


def resolve_nexo_sid(claude_session_id: str = "") -> tuple[str, str]:
    """Resolve the NEXO sid for the current compaction invocation.

    Returns ``(nexo_sid, source)`` where ``source`` is one of:

    - ``sessions``       resolved via sessions table claude_session_id.
    - ``alias``          resolved via session_claude_aliases.
    - ``sidecar``        per-conversation sidecar file.
    - ``sidecar_legacy`` legacy global sidecar (single-conv path).
    - ``none``           no rail matched; caller stores empty string.
    """
    token = (claude_session_id or os.environ.get("CLAUDE_SESSION_ID", "") or "").strip()

    if token:
        sid, source = _db_lookup(token)
        if sid:
            return sid, source
        safe_id = _safe_claude_id(token)
        if safe_id:
            for base in _candidate_data_dirs():
                side = _read_sidecar(base / "compacting" / f"{safe_id}.txt")
                if side:
                    return side, "sidecar"

    for base in _candidate_data_dirs():
        side = _read_sidecar(base / "compacting-sid.txt")
        if side:
            return side, "sidecar_legacy"

    return "", "none"
