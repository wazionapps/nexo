#!/usr/bin/env python3
"""
Deep Sleep — Step 3: Apply findings.
Takes the analysis output and writes feedback memories + trust adjustments.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"


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
    cog_db = NEXO_HOME / "data" / "cognitive.db"
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


def add_learning(category: str, title: str, content: str) -> bool:
    """Add a learning to nexo.db using real schema."""
    if not NEXO_DB.exists():
        return False
    try:
        now = datetime.now().timestamp()
        conn = sqlite3.connect(str(NEXO_DB))
        conn.execute(
            "INSERT INTO learnings (category, title, content, created_at, updated_at, reasoning) VALUES (?, ?, ?, ?, ?, ?)",
            (category, title, content, now, now, "Deep Sleep overnight analysis")
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  Error adding learning: {e}", file=sys.stderr)
        return False


def add_followup(followup_id: str, description: str, date: str = None) -> bool:
    """Add a followup to nexo.db using real schema."""
    if not NEXO_DB.exists():
        return False
    try:
        now = datetime.now().timestamp()
        conn = sqlite3.connect(str(NEXO_DB))
        conn.execute(
            "INSERT OR IGNORE INTO followups (id, description, date, status, created_at, updated_at, reasoning) VALUES (?, ?, ?, 'PENDING', ?, ?, ?)",
            (followup_id, description, date or "", now, now, "Deep Sleep overnight analysis")
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  Error adding followup: {e}", file=sys.stderr)
        return False


def apply(analysis: dict):
    """Apply all findings from deep sleep analysis."""
    memory_dir = find_memory_dir()
    actions_taken = []
    memory_entries = []
    date = analysis["date"]

    print(f"\nApplying findings for {date}...")

    # 1. Uncaptured corrections → learnings + feedback memories
    for i, correction in enumerate(analysis.get("uncaptured_corrections", [])):
        severity = correction.get("severity", "medium")
        category = correction.get("category", "process")
        content = correction.get("what_nexo_should_have_saved", "")
        quote = correction.get("quote", "")

        # All corrections → learnings
        learning_title = f"[Deep Sleep] {content[:80]}"
        learning_content = f"User said: \"{quote}\"\nContext: {correction.get('context', '')}\nRepeated: {correction.get('times_repeated', 1)} times"
        if add_learning(category, learning_title, learning_content):
            actions_taken.append(f"learning_add: {learning_title[:50]}")

        # High/critical → also feedback memories
        if severity in ("high", "critical"):
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

    # 2. Missed commitments → followups
    for i, commitment in enumerate(analysis.get("missed_commitments", [])):
        fid = f"NF-DS-{date}-{i}"
        desc = f"[Deep Sleep] {commitment.get('commitment', '')[:100]}"
        if add_followup(fid, desc, commitment.get("due_date")):
            actions_taken.append(f"followup: {desc[:50]}")

    # 3. Trust adjustments for critical violations
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
