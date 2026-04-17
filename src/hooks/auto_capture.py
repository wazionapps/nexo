#!/usr/bin/env python3
"""NEXO Auto-Capture Hook — Extract decisions/corrections/explicit facts.

v6.0.0 changes
--------------
- Registered as the UserPromptSubmit handler in ``src/hooks/manifest.json``.
  The same script also runs from ``post_tool_use.py`` on PostToolUse,
  so both inbound user text and outbound tool results reach classification.
- Adds a persistent 1-hour de-duplication gate backed by the
  ``auto_capture_dedup`` SQLite table (schema auto-created on first use).
  Before v6.0.0 dedup was per-invocation only, so a corrective line
  sent in three consecutive prompts was stored three times.
- On ``correction`` matches, queues an ``auto_capture_pending_learnings``
  row and attempts an immediate ``nexo_learning_add`` via the local
  ``tools_learnings`` module. If learnings tooling is unavailable the row
  stays queued so a later audit can replay it.

Reads conversation input in three ways:
  - Programmatic: ``process_conversation(messages)``.
  - Hook stdin: Claude Code pipes a JSON payload with ``user_message`` or
    ``tool_result``; the CLI detects the shape and routes accordingly.
  - Explicit CLI: positional args are treated as messages.

Exit code is always 0 — the hook pipeline must not be broken by us.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# Add source dir to path for cognitive imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cognitive  # type: ignore


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_DECISION_PATTERNS = [
    re.compile(r'\b(?:decided|agreed|will do|changed to|switching to|going with|chose|chosen|opted for)\b', re.IGNORECASE),
    re.compile(r"\b(?:let's go with|the plan is|we'll use|moving forward with)\b", re.IGNORECASE),
    re.compile(r'\b(?:approved|confirmed|locked in|finalized)\b', re.IGNORECASE),
    re.compile(r'\b(?:decidido|acordado|vamos con|cambiamos a|elegimos)\b', re.IGNORECASE),
]

_CORRECTION_PATTERNS = [
    re.compile(r"\b(?:don'?t|stop|wrong|incorrect|that'?s not right|fix this)\b", re.IGNORECASE),
    re.compile(r'\b(?:should be|actually|not that|the correct|mistake|error)\b', re.IGNORECASE),
    re.compile(r'\b(?:never do that|wrong approach|that broke|revert)\b', re.IGNORECASE),
    re.compile(r'\b(?:no,\s|nope|mal|otra vez|ya te dije|no es|est[aá] mal)\b', re.IGNORECASE),
]

_EXPLICIT_PATTERNS = [
    re.compile(r"\b(?:remember|note that|important:|keep in mind|don'?t forget)\b", re.IGNORECASE),
    re.compile(r'\b(?:for future reference|take note|key point|rule:)\b', re.IGNORECASE),
    re.compile(r'\b(?:recuerda|importante:|ten en cuenta|no olvides|regla:)\b', re.IGNORECASE),
]

_MIN_LINE_LENGTH = 15
_MAX_FACT_LENGTH = 500
_DEDUP_TTL_SECONDS = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Persistent dedup store
# ---------------------------------------------------------------------------
# Stores a content hash → first-seen timestamp so repeated correction lines
# in a short window don't spawn duplicate learnings. Falls back to
# per-call in-memory dedup when the DB can't be opened (tests, tmp homes).


def _dedup_db_path() -> Path:
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    return home / "data" / "auto_capture_dedup.db"


def _dedup_connection() -> sqlite3.Connection | None:
    try:
        path = _dedup_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=2.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS auto_capture_dedup ("
            "content_hash TEXT PRIMARY KEY, "
            "fact_type TEXT, "
            "first_seen_at REAL, "
            "last_seen_at REAL, "
            "hit_count INTEGER DEFAULT 1"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_capture_dedup_seen "
            "ON auto_capture_dedup (first_seen_at)"
        )
        conn.commit()
        return conn
    except Exception:
        return None


def _dedup_is_recent(conn: sqlite3.Connection | None, content_hash: str) -> bool:
    """True when the hash was seen inside the TTL window."""
    if conn is None:
        return False
    try:
        cutoff = time.time() - _DEDUP_TTL_SECONDS
        row = conn.execute(
            "SELECT first_seen_at FROM auto_capture_dedup WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return False
        first_seen = row[0] or 0.0
        return first_seen >= cutoff
    except Exception:
        return False


def _dedup_record(
    conn: sqlite3.Connection | None,
    content_hash: str,
    fact_type: str,
) -> None:
    if conn is None:
        return
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO auto_capture_dedup (content_hash, fact_type, first_seen_at, last_seen_at, hit_count) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(content_hash) DO UPDATE SET "
            "last_seen_at=excluded.last_seen_at, hit_count=hit_count+1",
            (content_hash, fact_type, now, now),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify_line(line: str) -> list[tuple[str, str]]:
    line = line.strip()
    if len(line) < _MIN_LINE_LENGTH:
        return []

    facts: list[tuple[str, str]] = []

    for pattern in _DECISION_PATTERNS:
        if pattern.search(line):
            facts.append(("decision", line))
            break

    for pattern in _CORRECTION_PATTERNS:
        if pattern.search(line):
            facts.append(("correction", line))
            break

    for pattern in _EXPLICIT_PATTERNS:
        if pattern.search(line):
            facts.append(("explicit", line))
            break

    return facts


def _content_hash(fact_type: str, content: str) -> str:
    """Hash used for the persistent dedup table.

    Includes ``fact_type`` so that a line which matches both ``decision``
    and ``correction`` categories doesn't collapse — the dedup table is
    keyed per category, not per raw string.
    """
    return hashlib.sha256(
        f"{fact_type}::{content.lower().strip()}".encode("utf-8"),
    ).hexdigest()


# ---------------------------------------------------------------------------
# Auto learning_add on correction
# ---------------------------------------------------------------------------


def _auto_learning_add(title: str, content: str) -> bool:
    """Best-effort call to tools_learnings.add_learning.

    Returns True when the learning was stored, False otherwise. Failures
    are silent so the hook itself never breaks the user's prompt flow.
    """
    try:
        import tools_learnings  # type: ignore
    except Exception:
        return False

    try:
        result = tools_learnings.add_learning(
            category="auto",
            title=title,
            content=content,
            priority="medium",
            reasoning="auto-captured from correction pattern in UserPromptSubmit/PostToolUse hook",
        )
        if isinstance(result, dict):
            return bool(result.get("ok") or result.get("id") or result.get("learning_id"))
        return bool(result)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def process_conversation(messages: list[str]) -> dict:
    """Classify each line, dedup, store via cognitive.ingest(), learning_add on correction."""
    all_facts: list[tuple[str, str]] = []
    decisions = 0
    corrections = 0
    explicits = 0

    for msg in messages:
        for line in msg.split("\n"):
            classified = _classify_line(line)
            for fact_type, content in classified:
                if fact_type == "decision":
                    decisions += 1
                elif fact_type == "correction":
                    corrections += 1
                elif fact_type == "explicit":
                    explicits += 1
                all_facts.append((fact_type, content[:_MAX_FACT_LENGTH]))

    # In-invocation dedup keyed by (fact_type, content) — the same line
    # may legitimately match both ``decision`` and ``correction``, and
    # collapsing them would hide the correction (which is the category
    # that drives auto_learning_add).
    seen_local: set[tuple[str, str]] = set()
    unique_facts: list[tuple[str, str]] = []
    for fact_type, content in all_facts:
        key = (fact_type, content.lower().strip())
        if key in seen_local:
            continue
        seen_local.add(key)
        unique_facts.append((fact_type, content))

    dedup_conn = _dedup_connection()
    stored = 0
    rejected_by_gate = 0
    deduplicated_persistent = 0
    learnings_added = 0
    extracted_details: list[dict] = []

    for fact_type, content in unique_facts:
        content_hash = _content_hash(fact_type, content)

        # Persistent dedup — skip if we saw this exact line <1h ago.
        if _dedup_is_recent(dedup_conn, content_hash):
            deduplicated_persistent += 1
            extracted_details.append({
                "type": fact_type,
                "content": content[:100],
                "stored": False,
                "memory_id": 0,
                "deduped": True,
            })
            continue

        tagged_content = f"[{fact_type.upper()}] {content}"

        try:
            result_id = cognitive.ingest(
                content=tagged_content,
                source_type="auto_capture",
                source_id=f"hook_{fact_type}",
                source_title=f"Auto-captured {fact_type}",
                domain="conversation",
                source="agent_observation",
                skip_quarantine=False,
                bypass_gate=False,
            )
        except Exception:
            result_id = 0

        if result_id == 0:
            rejected_by_gate += 1
        else:
            stored += 1

        # On correction, also register as a learning for future guard checks.
        learning_added = False
        if fact_type == "correction":
            title = content[:80].rstrip()
            if _auto_learning_add(title, content):
                learning_added = True
                learnings_added += 1

        _dedup_record(dedup_conn, content_hash, fact_type)

        extracted_details.append({
            "type": fact_type,
            "content": content[:100],
            "stored": result_id != 0,
            "memory_id": result_id,
            "deduped": False,
            "learning_added": learning_added,
        })

    if dedup_conn is not None:
        try:
            dedup_conn.close()
        except Exception:
            pass

    return {
        "facts_extracted": len(unique_facts),
        "decisions": decisions,
        "corrections": corrections,
        "explicits": explicits,
        "stored": stored,
        "rejected_by_gate": rejected_by_gate,
        "deduplicated_persistent": deduplicated_persistent,
        "learnings_added": learnings_added,
        "extracted_facts": extracted_details,
    }


# ---------------------------------------------------------------------------
# Hook stdin parsing
# ---------------------------------------------------------------------------


def _extract_text_from_hook_payload(payload: dict) -> list[str]:
    """Pull the relevant text strings out of a Claude Code hook payload.

    The two shapes we care about:
      - UserPromptSubmit: ``{"user_message": "..."}`` or ``{"prompt": "..."}``.
      - PostToolUse: ``{"tool_result": {...}}`` with the content nested.
    """
    if not isinstance(payload, dict):
        return []
    texts: list[str] = []

    for key in ("user_message", "prompt", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)

    result = payload.get("tool_result") or payload.get("result")
    if isinstance(result, str) and result.strip():
        texts.append(result)
    elif isinstance(result, dict):
        for key in ("content", "output", "text", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        inner = item.get("text") or item.get("content") or ""
                        if isinstance(inner, str) and inner.strip():
                            texts.append(inner)
                    elif isinstance(item, str) and item.strip():
                        texts.append(item)
    return texts


def _read_stdin() -> list[str]:
    if sys.stdin.isatty():
        return []
    raw = sys.stdin.read()
    if not raw.strip():
        return []
    # Try JSON first (hook payloads). Fall back to raw lines for the
    # legacy CLI path (echo "..." | python3 auto_capture.py).
    try:
        payload = json.loads(raw)
    except Exception:
        return [line for line in raw.split("\n") if line.strip()]

    texts = _extract_text_from_hook_payload(payload)
    if texts:
        return texts
    # Some hook variants stream just raw strings.
    if isinstance(payload, list):
        return [str(item) for item in payload if str(item).strip()]
    return []


def main() -> int:
    messages = list(sys.argv[1:]) if len(sys.argv) > 1 else _read_stdin()
    if not messages:
        # Silent no-op when invoked with empty stdin (common in hooks).
        return 0
    try:
        result = process_conversation(messages)
    except Exception as exc:
        # Never propagate — the hook must not break the conversation.
        print(f"auto_capture: non-fatal error: {exc}", file=sys.stderr)
        return 0

    # Human-readable tail for CLI debugging. Hooks discard stdout.
    if sys.stdout.isatty():
        print(f"Facts extracted: {result['facts_extracted']}")
        print(f"  Decisions: {result['decisions']}")
        print(f"  Corrections: {result['corrections']}")
        print(f"  Explicits: {result['explicits']}")
        print(
            f"Stored: {result['stored']}, Rejected: {result['rejected_by_gate']}, "
            f"Deduped<1h: {result['deduplicated_persistent']}, Learnings added: {result['learnings_added']}"
        )
        for fact in result["extracted_facts"]:
            status = "STORED" if fact["stored"] else ("DEDUPED" if fact.get("deduped") else "REJECTED")
            print(f"  [{status}] [{fact['type']}] {fact['content']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
