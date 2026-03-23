#!/usr/bin/env python3
"""NEXO Auto-Capture Hook — Extract facts from conversation context.

Inspired by claude-mem's observation handler and transcript processor.
Uses simple heuristics (no LLM) to extract decisions, corrections,
and explicit facts from conversation messages.

Can be called:
- Programmatically via process_conversation()
- From Claude Code hooks via stdin (pipe conversation lines)
- As CLI: python3 auto_capture.py "message1" "message2" ...

Stores extracted facts via cognitive.ingest() with appropriate tags.
"""

import re
import sys
from pathlib import Path

# Add nexo-mcp to path for cognitive imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cognitive


# ---------------------------------------------------------------------------
# Pattern definitions (adapted from claude-mem's transcript processor
# and ShieldCortex's pattern groups approach)
# ---------------------------------------------------------------------------

# Decision patterns — lines indicating a choice was made
_DECISION_PATTERNS = [
    re.compile(r'\b(?:decided|agreed|will do|changed to|switching to|going with|chose|chosen|opted for)\b', re.IGNORECASE),
    re.compile(r'\b(?:let\'?s go with|the plan is|we\'?ll use|moving forward with)\b', re.IGNORECASE),
    re.compile(r'\b(?:approved|confirmed|locked in|finalized)\b', re.IGNORECASE),
    re.compile(r'\b(?:decidido|acordado|vamos con|cambiamos a|elegimos)\b', re.IGNORECASE),  # Spanish
]

# Correction patterns — lines indicating something was wrong
_CORRECTION_PATTERNS = [
    re.compile(r'\b(?:don\'?t|stop|wrong|incorrect|that\'?s not right|fix this)\b', re.IGNORECASE),
    re.compile(r'\b(?:should be|actually|not that|the correct|mistake|error)\b', re.IGNORECASE),
    re.compile(r'\b(?:never do that|wrong approach|that broke|revert)\b', re.IGNORECASE),
    re.compile(r'\b(?:no,\s|nope|mal|otra vez|ya te dije|no es|est[aá] mal)\b', re.IGNORECASE),  # Spanish
]

# Explicit fact patterns — user explicitly asks to remember something
_EXPLICIT_PATTERNS = [
    re.compile(r'\b(?:remember|note that|important:|keep in mind|don\'?t forget)\b', re.IGNORECASE),
    re.compile(r'\b(?:for future reference|take note|key point|rule:)\b', re.IGNORECASE),
    re.compile(r'\b(?:recuerda|importante:|ten en cuenta|no olvides|regla:)\b', re.IGNORECASE),  # Spanish
]

# Minimum line length to consider (skip very short lines)
_MIN_LINE_LENGTH = 15

# Maximum fact content length
_MAX_FACT_LENGTH = 500


def _classify_line(line: str) -> list[tuple[str, str]]:
    """Classify a single line into fact types.

    Returns list of (fact_type, content) tuples. A line can match
    multiple categories.
    """
    line = line.strip()
    if len(line) < _MIN_LINE_LENGTH:
        return []

    facts = []

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


def process_conversation(messages: list[str]) -> dict:
    """Process conversation messages and extract key facts.

    Adapted from claude-mem's TranscriptEventProcessor: scans each message
    line for decision, correction, and explicit fact patterns. Stores
    extracted facts via cognitive.ingest() with source_type='auto_capture'.

    Args:
        messages: List of conversation message strings

    Returns:
        Dict with facts_extracted, decisions, corrections, stored,
        rejected_by_gate counts and extracted_facts details.
    """
    all_facts = []
    decisions = 0
    corrections = 0
    explicits = 0

    for msg in messages:
        # Split message into lines and classify each
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

    # Deduplicate by content (same line might appear in multiple messages)
    seen = set()
    unique_facts = []
    for fact_type, content in all_facts:
        content_key = content.lower().strip()
        if content_key not in seen:
            seen.add(content_key)
            unique_facts.append((fact_type, content))

    # Store via cognitive.ingest()
    stored = 0
    rejected_by_gate = 0
    extracted_details = []

    for fact_type, content in unique_facts:
        # Build tagged content for better retrieval
        tagged_content = f"[{fact_type.upper()}] {content}"

        result_id = cognitive.ingest(
            content=tagged_content,
            source_type="auto_capture",
            source_id=f"hook_{fact_type}",
            source_title=f"Auto-captured {fact_type}",
            domain="conversation",
            source="agent_observation",
            skip_quarantine=False,  # Route through quarantine for safety
            bypass_gate=False,      # Let prediction error gate filter duplicates
        )

        if result_id == 0:
            rejected_by_gate += 1
        else:
            stored += 1

        extracted_details.append({
            "type": fact_type,
            "content": content[:100],
            "stored": result_id != 0,
            "memory_id": result_id,
        })

    return {
        "facts_extracted": len(unique_facts),
        "decisions": decisions,
        "corrections": corrections,
        "explicits": explicits,
        "stored": stored,
        "rejected_by_gate": rejected_by_gate,
        "extracted_facts": extracted_details,
    }


def _read_stdin() -> list[str]:
    """Read conversation lines from stdin (for hook integration)."""
    if sys.stdin.isatty():
        return []
    return [line for line in sys.stdin.read().strip().split("\n") if line.strip()]


def main():
    """CLI entry point — accepts messages as args or from stdin.

    Usage:
        echo "We decided to use PostgreSQL" | python3 auto_capture.py
        python3 auto_capture.py "Remember: always use WAL mode" "That's wrong, fix it"
    """
    messages = list(sys.argv[1:]) if len(sys.argv) > 1 else _read_stdin()

    if not messages:
        print("Usage: python3 auto_capture.py 'message1' 'message2' ...")
        print("   or: echo 'messages' | python3 auto_capture.py")
        sys.exit(1)

    result = process_conversation(messages)
    print(f"Facts extracted: {result['facts_extracted']}")
    print(f"  Decisions: {result['decisions']}")
    print(f"  Corrections: {result['corrections']}")
    print(f"  Explicits: {result['explicits']}")
    print(f"Stored: {result['stored']}, Rejected by gate: {result['rejected_by_gate']}")

    for fact in result["extracted_facts"]:
        status = "STORED" if fact["stored"] else "REJECTED"
        print(f"  [{status}] [{fact['type']}] {fact['content']}")


if __name__ == "__main__":
    main()
