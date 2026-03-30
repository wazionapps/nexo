#!/usr/bin/env python3
"""
Deep Sleep — Step 1: Collect today's session transcripts.
Reads Claude Code .jsonl files, extracts clean conversation text + tool usage.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

MIN_USER_MESSAGES = 3  # Skip trivial sessions


def find_sessions_dir() -> Path:
    """Find the Claude Code sessions directory dynamically."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return claude_dir

    # Find the project directory (usually named after the home path)
    for d in claude_dir.iterdir():
        if d.is_dir() and list(d.glob("*.jsonl")):
            return d

    # Fallback: look for any .jsonl in the projects dir tree
    for jsonl in claude_dir.rglob("*.jsonl"):
        return jsonl.parent

    return claude_dir


def extract_session(jsonl_path: str) -> dict | None:
    """Extract clean transcript from a session JSONL file."""
    messages = []
    tool_uses = []
    user_msg_count = 0

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = d.get("type")

                # User messages
                if msg_type == "user":
                    content = d.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        if content.startswith("<system-reminder>"):
                            continue
                        messages.append({
                            "role": "user",
                            "text": content[:5000],
                            "uuid": d.get("uuid", "")
                        })
                        user_msg_count += 1

                # Assistant messages
                elif msg_type in ("message", "assistant"):
                    msg = d.get("message", {})
                    content_blocks = msg.get("content", [])
                    text_parts = []
                    for block in content_blocks:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                tool_uses.append({
                                    "tool": block.get("name", ""),
                                    "input_keys": list(block.get("input", {}).keys()) if isinstance(block.get("input"), dict) else [],
                                    "file": block.get("input", {}).get("file_path", "") or block.get("input", {}).get("command", "")[:100] if isinstance(block.get("input"), dict) else ""
                                })
                    if text_parts:
                        combined = "\n".join(text_parts)[:5000]
                        messages.append({
                            "role": "assistant",
                            "text": combined
                        })

    except Exception as e:
        print(f"Error reading {jsonl_path}: {e}", file=sys.stderr)
        return None

    if user_msg_count < MIN_USER_MESSAGES:
        return None

    return {
        "session_file": os.path.basename(jsonl_path),
        "message_count": len(messages),
        "user_message_count": user_msg_count,
        "tool_use_count": len(tool_uses),
        "messages": messages,
        "tool_uses": tool_uses
    }


def collect_date(target_date: str, sessions_dir: Path) -> list[dict]:
    """Collect all sessions modified on a given date."""
    sessions = []
    for f in sessions_dir.glob("*.jsonl"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime.strftime("%Y-%m-%d") == target_date:
            session = extract_session(str(f))
            if session:
                session["modified"] = mtime.isoformat()
                sessions.append(session)
    sessions.sort(key=lambda s: s["modified"])
    return sessions


def main():
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    sessions_dir = find_sessions_dir()

    sessions = collect_date(date_arg, sessions_dir)

    output = {
        "date": date_arg,
        "sessions_found": len(sessions),
        "total_messages": sum(s["message_count"] for s in sessions),
        "total_tool_uses": sum(s["tool_use_count"] for s in sessions),
        "sessions": sessions
    }

    output_dir = NEXO_HOME / "operations" / "deep-sleep"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{output['date']}-transcripts.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Collected {len(sessions)} sessions, {output['total_messages']} messages, {output['total_tool_uses']} tool uses")
    print(f"Output: {output_file}")


if __name__ == "__main__":
    main()
