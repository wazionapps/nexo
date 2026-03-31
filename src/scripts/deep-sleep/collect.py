#!/usr/bin/env python3
"""
Deep Sleep v2 -- Phase 1: Collect all context for overnight analysis.

Gathers transcripts, DB data, logs, and discovered files into a single
plain-text context file that subsequent phases read via Claude's Read tool.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
  NEXO_CODE  -- path to the NEXO source repo (optional, for self-analysis)
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", ""))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
COGNITIVE_DB = NEXO_HOME / "data" / "cognitive.db"

MIN_USER_MESSAGES = 3  # Skip trivial sessions

# ── Transcript collection (kept from collect_transcripts.py) ──────────────


def find_session_dirs() -> list[Path]:
    """Find all Claude Code project directories that contain .jsonl files."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []
    dirs = set()
    for jsonl in claude_dir.rglob("*.jsonl"):
        dirs.add(jsonl.parent)
    return list(dirs)


def extract_session(jsonl_path: Path) -> dict | None:
    """Extract clean transcript from a session JSONL file."""
    messages = []
    tool_uses = []
    user_msg_count = 0

    try:
        with open(jsonl_path, "r") as f:
            for line_no, line in enumerate(f, 1):
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
                            "index": line_no,
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
                                tool_input = block.get("input", {})
                                tool_uses.append({
                                    "tool": block.get("name", ""),
                                    "input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
                                    "file": (
                                        tool_input.get("file_path", "")
                                        or str(tool_input.get("command", ""))[:100]
                                    ) if isinstance(tool_input, dict) else ""
                                })
                    if text_parts:
                        combined = "\n".join(text_parts)[:5000]
                        messages.append({
                            "role": "assistant",
                            "index": line_no,
                            "text": combined
                        })

    except Exception as e:
        print(f"  [collect] Error reading {jsonl_path}: {e}", file=sys.stderr)
        return None

    if user_msg_count < MIN_USER_MESSAGES:
        return None

    return {
        "session_file": jsonl_path.name,
        "session_path": str(jsonl_path),
        "message_count": len(messages),
        "user_message_count": user_msg_count,
        "tool_use_count": len(tool_uses),
        "messages": messages,
        "tool_uses": tool_uses
    }


def collect_transcripts(target_date: str) -> list[dict]:
    """Collect all sessions modified on the target date."""
    sessions = []
    for sdir in find_session_dirs():
        for f in sdir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
            if mtime.strftime("%Y-%m-%d") == target_date:
                session = extract_session(f)
                if session:
                    session["modified"] = mtime.isoformat()
                    sessions.append(session)
    sessions.sort(key=lambda s: s["modified"])
    return sessions


# ── Database queries ──────────────────────────────────────────────────────


def safe_query(db_path: Path, query: str, params: tuple = ()) -> list[dict]:
    """Run a query and return rows as dicts. Returns [] on any error."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"  [collect] DB query error ({db_path.name}): {e}", file=sys.stderr)
        return []


def collect_followups() -> list[dict]:
    """Active followups from nexo.db."""
    return safe_query(
        NEXO_DB,
        "SELECT * FROM followups WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY date ASC"
    )


def collect_learnings() -> list[dict]:
    """Active learnings from nexo.db."""
    return safe_query(NEXO_DB, "SELECT * FROM learnings ORDER BY updated_at DESC LIMIT 200")


def collect_diaries(target_date: str) -> list[dict]:
    """Today's session diaries."""
    # Diaries store created_at as unix timestamp or ISO string -- handle both
    start_ts = datetime.strptime(target_date, "%Y-%m-%d").timestamp()
    end_ts = start_ts + 86400
    rows = safe_query(
        NEXO_DB,
        "SELECT * FROM session_diary WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
        (start_ts, end_ts)
    )
    if not rows:
        # Try ISO format
        rows = safe_query(
            NEXO_DB,
            "SELECT * FROM session_diary WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
            (target_date + "T00:00:00", target_date + "T23:59:59")
        )
    return rows


def collect_trust_score() -> list[dict]:
    """Current trust score and 7-day history from cognitive.db."""
    return safe_query(
        COGNITIVE_DB,
        "SELECT * FROM trust_score ORDER BY rowid DESC LIMIT 1"
    )


# ── Discovery: scan NEXO_HOME for non-core content ───────────────────────

CORE_DIRS = {"data", "operations", "logs", "coordination", "brain"}
CORE_FILES = {"config.json", "nexo.db", "cognitive.db"}


def discover_extras() -> list[dict]:
    """Scan NEXO_HOME for non-core directories and files."""
    extras = []
    if not NEXO_HOME.exists():
        return extras

    for item in sorted(NEXO_HOME.iterdir()):
        name = item.name
        if name.startswith("."):
            continue
        if name in CORE_DIRS or name in CORE_FILES:
            continue

        entry = {"name": name, "path": str(item), "type": "dir" if item.is_dir() else "file"}

        if item.is_dir():
            # Count contents and list interesting files
            files = list(item.rglob("*"))
            entry["file_count"] = len([f for f in files if f.is_file()])
            entry["notable_files"] = [
                str(f.relative_to(item))
                for f in files
                if f.is_file() and f.suffix in (".py", ".sh", ".json", ".db", ".log", ".sqlite")
            ][:20]
        elif item.is_file():
            entry["size"] = item.stat().st_size

        extras.append(entry)

    return extras


# ── LaunchAgent logs ──────────────────────────────────────────────────────


def collect_error_logs(target_date: str) -> list[dict]:
    """Scan NEXO_HOME/logs/ for lines containing errors from today."""
    log_dir = NEXO_HOME / "logs"
    if not log_dir.exists():
        return []

    errors = []
    for log_file in sorted(log_dir.glob("*.log")):
        try:
            lines = log_file.read_text(errors="replace").splitlines()
        except Exception:
            continue

        file_errors = []
        for i, line in enumerate(lines):
            # Match lines from today that contain error indicators
            if target_date in line and any(
                kw in line.lower() for kw in ("error", "exception", "traceback", "failed", "fatal", "critical")
            ):
                # Include surrounding context (1 line before, 2 after)
                start = max(0, i - 1)
                end = min(len(lines), i + 3)
                file_errors.append({
                    "line": i + 1,
                    "context": "\n".join(lines[start:end])
                })

        if file_errors:
            errors.append({
                "file": log_file.name,
                "path": str(log_file),
                "errors": file_errors[:50]  # Cap per file
            })

    return errors


# ── Format output as plain text ───────────────────────────────────────────


def format_section(title: str, data, indent: int = 0) -> str:
    """Format a data section as readable plain text."""
    prefix = "  " * indent
    lines = [f"\n{'=' * 70}", f"{title}", f"{'=' * 70}"]

    if isinstance(data, list):
        if not data:
            lines.append(f"{prefix}(none)")
        else:
            for i, item in enumerate(data):
                lines.append(f"\n{prefix}--- [{i + 1}] ---")
                if isinstance(item, dict):
                    for k, v in item.items():
                        val_str = str(v)
                        if len(val_str) > 500:
                            val_str = val_str[:500] + "..."
                        lines.append(f"{prefix}  {k}: {val_str}")
                else:
                    lines.append(f"{prefix}  {item}")
    elif isinstance(data, dict):
        for k, v in data.items():
            val_str = str(v)
            if len(val_str) > 500:
                val_str = val_str[:500] + "..."
            lines.append(f"{prefix}{k}: {val_str}")
    elif isinstance(data, str):
        lines.append(data)
    else:
        lines.append(str(data))

    return "\n".join(lines)


def format_transcripts(sessions: list[dict]) -> str:
    """Format transcripts in a readable way for Claude to analyze."""
    lines = [f"\n{'=' * 70}", "SESSION TRANSCRIPTS", f"{'=' * 70}"]
    lines.append(f"Total sessions: {len(sessions)}")

    for i, session in enumerate(sessions):
        lines.append(f"\n{'─' * 60}")
        lines.append(f"SESSION {i + 1}: {session['session_file']}")
        lines.append(f"Modified: {session['modified']}")
        lines.append(f"Messages: {session['message_count']}, Tool uses: {session['tool_use_count']}")
        lines.append(f"{'─' * 60}")

        for msg in session["messages"]:
            role = "USER" if msg["role"] == "user" else "AGENT"
            idx = msg.get("index", "?")
            lines.append(f"\n[{role} @{idx}]")
            lines.append(msg["text"])

        if session["tool_uses"]:
            lines.append(f"\n  -- Tool usage log --")
            for tu in session["tool_uses"]:
                file_info = f" [{tu['file'][:80]}]" if tu.get("file") else ""
                lines.append(f"  - {tu['tool']}{file_info}")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    DEEP_SLEEP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[collect] Phase 1: Collecting context for {target_date}")

    # 1. Transcripts
    print("[collect] Gathering transcripts...")
    sessions = collect_transcripts(target_date)
    print(f"  Found {len(sessions)} sessions")

    if not sessions:
        print(f"[collect] No sessions found for {target_date}. Writing minimal context file.")
        output_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"
        output_file.write_text(
            f"Deep Sleep Context for {target_date}\n\nNo sessions found for this date.\n"
        )
        print(f"[collect] Output: {output_file}")
        return

    # 2. Core DB data
    print("[collect] Querying databases...")
    followups = collect_followups()
    print(f"  Active followups: {len(followups)}")

    learnings = collect_learnings()
    print(f"  Learnings: {len(learnings)}")

    diaries = collect_diaries(target_date)
    print(f"  Diaries today: {len(diaries)}")

    trust_history = collect_trust_score()
    print(f"  Trust events (7d): {len(trust_history)}")

    # 3. Discovery
    print("[collect] Scanning for non-core content...")
    extras = discover_extras()
    print(f"  Discovered {len(extras)} extra items")

    # 4. Error logs
    print("[collect] Checking error logs...")
    error_logs = collect_error_logs(target_date)
    print(f"  Log files with errors: {len(error_logs)}")

    # 5. Build context file
    print("[collect] Writing context file...")

    parts = [
        f"Deep Sleep Context -- {target_date}",
        f"Generated at: {datetime.now().isoformat()}",
        f"NEXO_HOME: {NEXO_HOME}",
        f"Sessions: {len(sessions)}",
    ]

    parts.append(format_transcripts(sessions))
    parts.append(format_section("ACTIVE FOLLOWUPS", followups))
    parts.append(format_section("LEARNINGS (recent 200)", learnings))
    parts.append(format_section("SESSION DIARIES TODAY", diaries))
    parts.append(format_section("TRUST SCORE HISTORY (7d)", trust_history))
    parts.append(format_section("DISCOVERED NON-CORE CONTENT", extras))
    parts.append(format_section("ERROR LOGS", error_logs))

    context_text = "\n".join(parts)

    output_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"
    output_file.write_text(context_text, encoding="utf-8")

    # Also write a small metadata JSON for other scripts to reference
    meta = {
        "date": target_date,
        "sessions_found": len(sessions),
        "session_files": [s["session_file"] for s in sessions],
        "total_messages": sum(s["message_count"] for s in sessions),
        "total_tool_uses": sum(s["tool_use_count"] for s in sessions),
        "followups_active": len(followups),
        "learnings_count": len(learnings),
        "diaries_today": len(diaries),
        "error_log_files": len(error_logs),
        "context_file": str(output_file),
        "context_size_bytes": len(context_text.encode("utf-8")),
    }
    meta_file = DEEP_SLEEP_DIR / f"{target_date}-meta.json"
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    size_kb = len(context_text.encode("utf-8")) / 1024
    print(f"[collect] Done. Context: {output_file} ({size_kb:.0f} KB)")
    print(f"[collect] Meta: {meta_file}")


if __name__ == "__main__":
    main()
