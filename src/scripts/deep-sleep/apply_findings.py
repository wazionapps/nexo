#!/usr/bin/env python3
"""
Deep Sleep — Step 3: Apply findings.
Takes the analysis output and writes feedback memories + trust adjustments.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEEP_SLEEP_DIR = Path.home() / "claude" / "operations" / "deep-sleep"
NEXO_DB = Path.home() / "claude" / "nexo-mcp" / "nexo.db"


def find_memory_dir() -> Path:
    """Find the Claude Code auto-memory directory."""
    claude_dir = Path.home() / ".claude" / "projects"
    for d in claude_dir.iterdir():
        if d.is_dir():
            mem_dir = d / "memory"
            if mem_dir.exists():
                return mem_dir
    # Fallback: create under first project dir
    for d in claude_dir.iterdir():
        if d.is_dir():
            mem_dir = d / "memory"
            mem_dir.mkdir(exist_ok=True)
            return mem_dir
    return claude_dir / "memory"


def write_feedback_memory(memory_dir: Path, filename: str, name: str, description: str, content: str):
    """Write a feedback memory file."""
    filepath = memory_dir / filename
    feedback = f"""---
name: {name}
description: {description}
type: feedback
---

{content}
"""
    filepath.write_text(feedback)


def update_memory_index(memory_dir: Path, new_entries: list[dict]):
    """Append new entries to MEMORY.md index."""
    index_file = memory_dir / "MEMORY.md"
    if not index_file.exists() or not new_entries:
        return

    current = index_file.read_text()
    lines_to_add = []
    for entry in new_entries:
        line = f"- **{entry['title']}:** `{entry['filename']}` --- {entry['summary']}"
        if line not in current:
            lines_to_add.append(line)

    if lines_to_add:
        current += "\n" + "\n".join(lines_to_add) + "\n"
        index_file.write_text(current)


def adjust_trust(points: int, context: str):
    """Record trust adjustment in cognitive.db if available."""
    cog_db = Path.home() / "claude" / "nexo-mcp" / "cognitive.db"
    if not cog_db.exists():
        return
    try:
        conn = sqlite3.connect(str(cog_db))
        conn.execute(
            "INSERT INTO trust_events (event, context, points, created_at) VALUES (?, ?, ?, ?)",
            ("deep_sleep_violations", context, points, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def apply(analysis: dict):
    """Apply all findings from deep sleep analysis."""
    memory_dir = find_memory_dir()
    actions_taken = []
    memory_entries = []
    date = analysis["date"]

    print(f"\nApplying findings for {date}...")

    # 1. Uncaptured corrections → feedback memories (high/critical only)
    for i, correction in enumerate(analysis.get("uncaptured_corrections", [])):
        severity = correction.get("severity", "medium")
        if severity not in ("high", "critical"):
            continue

        category = correction.get("category", "process")
        content = correction.get("what_nexo_should_have_saved", "")
        quote = correction.get("quote", "")

        safe_name = category.replace(" ", "_").lower()
        filename = f"ds_{date}_{safe_name}_{i}.md"
        write_feedback_memory(
            memory_dir, filename,
            name=content[:60],
            description=f"Deep sleep detected uncaptured correction ({severity})",
            content=f"{content}\n\n**Why:** User said: \"{quote}\"\nContext: {correction.get('context', '')}\n\n**How to apply:** {content}"
        )
        memory_entries.append({
            "title": content[:40],
            "filename": filename,
            "summary": f"Deep sleep {date}, severity {severity}"
        })
        actions_taken.append(f"feedback_write: {filename}")

    # 2. Trust adjustments for critical violations
    critical_violations = [v for v in analysis.get("protocol_violations", []) if v.get("severity") == "critical"]
    if critical_violations:
        points = -3 * len(critical_violations)
        adjust_trust(points, f"{len(critical_violations)} critical violations on {date}")
        actions_taken.append(f"trust: {points} points ({len(critical_violations)} critical violations)")

    # 3. Update MEMORY.md index
    update_memory_index(memory_dir, memory_entries)
    if memory_entries:
        actions_taken.append(f"memory_index: {len(memory_entries)} entries added")

    # 4. Save applied actions log
    applied_log = {
        "date": date,
        "applied_at": datetime.now().isoformat(),
        "actions_taken": actions_taken,
        "corrections_processed": len(analysis.get("uncaptured_corrections", [])),
        "compliance": analysis.get("protocol_compliance", {}).get("overall_compliance", 0)
    }

    applied_file = DEEP_SLEEP_DIR / f"{date}-applied.json"
    with open(applied_file, "w") as f:
        json.dump(applied_log, f, indent=2, ensure_ascii=False)

    print(f"Applied {len(actions_taken)} actions:")
    for a in actions_taken:
        print(f"  ✓ {a}")

    return applied_log


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    analysis_file = DEEP_SLEEP_DIR / f"{date}-analysis.json"
    if not analysis_file.exists():
        print(f"No analysis found for {date}. Run analyze_session.py first.")
        sys.exit(1)

    with open(analysis_file) as f:
        analysis = json.load(f)

    result = apply(analysis)

    compliance = analysis.get("protocol_compliance", {}).get("overall_compliance", 0)
    print(f"\nDeep Sleep {date} — {result['corrections_processed']} corrections, "
          f"{compliance:.0%} compliance, {len(result['actions_taken'])} actions applied")


if __name__ == "__main__":
    main()
