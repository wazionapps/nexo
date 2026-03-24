#!/usr/bin/env python3
"""
NEXO Reflection Engine — Processes session_buffer.jsonl entries.

Triggered by the stop hook when >=3 sessions have accumulated and
the last reflection was >4 hours ago.

What it does:
1. Reads all entries from session_buffer.jsonl
2. Extracts patterns: recurring tasks, common errors, user mood trends
3. Updates user_model.json with observed patterns
4. Writes a reflection summary to reflection-log.json
5. Clears processed entries from the buffer

Runs as a standalone Python script (no LLM needed).
"""

import json
import os
import sys
from datetime import datetime
from collections import Counter

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
BUFFER_PATH = os.path.join(NEXO_HOME, "brain", "session_buffer.jsonl")
USER_MODEL_PATH = os.path.join(NEXO_HOME, "brain", "user_model.json")
REFLECTION_LOG_PATH = os.path.join(NEXO_HOME, "coordination", "reflection-log.json")


def load_buffer():
    """Load all entries from session buffer."""
    entries = []
    if not os.path.exists(BUFFER_PATH):
        return entries
    with open(BUFFER_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def load_user_model():
    """Load existing user model or create empty one."""
    if os.path.exists(USER_MODEL_PATH):
        try:
            return json.load(open(USER_MODEL_PATH))
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "created": datetime.now().strftime("%Y-%m-%d"),
        "traits": {},
        "work_patterns": {},
        "mood_history": [],
        "common_tasks": [],
        "error_patterns": [],
        "reflections_count": 0,
    }


def load_reflection_log():
    """Load existing reflection log."""
    if os.path.exists(REFLECTION_LOG_PATH):
        try:
            return json.load(open(REFLECTION_LOG_PATH))
        except (json.JSONDecodeError, IOError):
            pass
    return []


def analyze_entries(entries):
    """Extract patterns from buffer entries."""
    tasks = []
    decisions = []
    errors = []
    moods = Counter()
    user_patterns = []
    files_modified = []
    self_critiques = []

    for entry in entries:
        source = entry.get("source", "")
        # Skip hook-fallback entries (they have no real data)
        if source == "hook-fallback":
            continue

        # Collect tasks
        for t in entry.get("tasks", []):
            if t and t != "session ended":
                tasks.append(t)

        # Collect decisions
        for d in entry.get("decisions", []):
            if d:
                decisions.append(d)

        # Collect errors
        for e in entry.get("errors_resolved", []):
            if e:
                errors.append(e)

        # Count moods
        mood = entry.get("mood", "unknown")
        if mood and mood != "unknown":
            moods[mood] += 1

        # User patterns
        for p in entry.get("user_patterns", []):
            if p:
                user_patterns.append(p)

        # Files
        for f in entry.get("files_modified", []):
            if f:
                files_modified.append(f)

        # Self-critiques
        critique = entry.get("self_critique", "")
        if critique and "hook-fallback" not in critique:
            self_critiques.append(critique)

    return {
        "tasks": tasks,
        "decisions": decisions,
        "errors": errors,
        "moods": dict(moods),
        "user_patterns": user_patterns,
        "files_modified": files_modified,
        "self_critiques": self_critiques,
        "entry_count": len(entries),
        "claude_entries": sum(1 for e in entries if e.get("source") == "claude"),
    }


def update_user_model(model, analysis):
    """Update user model with new patterns."""
    # Update mood history (keep last 50)
    if analysis["moods"]:
        model["mood_history"].append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "moods": analysis["moods"],
        })
        model["mood_history"] = model["mood_history"][-50:]

    # Update common tasks (top 20)
    task_counter = Counter(model.get("common_tasks", []))
    task_counter.update(analysis["tasks"])
    model["common_tasks"] = [t for t, _ in task_counter.most_common(20)]

    # Update error patterns (keep last 30)
    existing_errors = model.get("error_patterns", [])
    existing_errors.extend(analysis["errors"])
    model["error_patterns"] = existing_errors[-30:]

    # Update work patterns
    if analysis["files_modified"]:
        file_counter = Counter(model.get("work_patterns", {}).get("frequent_files", []))
        file_counter.update(analysis["files_modified"])
        if "work_patterns" not in model:
            model["work_patterns"] = {}
        model["work_patterns"]["frequent_files"] = [
            f for f, _ in file_counter.most_common(20)
        ]

    # Derive traits from mood patterns
    total_moods = sum(analysis["moods"].values())
    if total_moods >= 3:
        dominant_mood = max(analysis["moods"], key=analysis["moods"].get)
        model["traits"]["recent_dominant_mood"] = dominant_mood
        model["traits"]["mood_updated"] = datetime.now().strftime("%Y-%m-%d")

    model["reflections_count"] = model.get("reflections_count", 0) + 1
    model["last_reflection"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    return model


def main():
    entries = load_buffer()
    if not entries:
        print("No entries in buffer.")
        return

    # Filter out pure hook entries (tool captures)
    session_entries = [
        e for e in entries
        if e.get("source") in ("claude", "hook-fallback")
        or "tasks" in e
    ]

    if not session_entries:
        print("No session entries to process.")
        return

    analysis = analyze_entries(session_entries)
    model = load_user_model()
    model = update_user_model(model, analysis)

    # Save updated model
    os.makedirs(os.path.dirname(USER_MODEL_PATH), exist_ok=True)
    with open(USER_MODEL_PATH, "w") as f:
        json.dump(model, f, indent=2)

    # Append to reflection log
    log = load_reflection_log()
    reflection = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "entries_processed": analysis["entry_count"],
        "claude_entries": analysis["claude_entries"],
        "tasks_seen": len(analysis["tasks"]),
        "decisions_made": len(analysis["decisions"]),
        "errors_resolved": len(analysis["errors"]),
        "mood_distribution": analysis["moods"],
        "self_critiques": analysis["self_critiques"][:5],
        "user_patterns_observed": analysis["user_patterns"][:5],
    }
    log.append(reflection)
    # Keep last 100 reflections
    log = log[-100:]

    os.makedirs(os.path.dirname(REFLECTION_LOG_PATH), exist_ok=True)
    with open(REFLECTION_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

    # Clear buffer (processed)
    with open(BUFFER_PATH, "w") as f:
        f.write("")

    print(f"Reflection complete: {analysis['entry_count']} entries processed, "
          f"{analysis['claude_entries']} from Claude, "
          f"{len(analysis['errors'])} errors, "
          f"{len(analysis['self_critiques'])} critiques.")


if __name__ == "__main__":
    main()
