#!/usr/bin/env python3
"""
Deep Sleep v2 -- Phase 2: Extract findings from each session using Claude CLI.

For each session in the context file, sends the extract-prompt.md to Claude
and collects structured findings. Merges all per-session results into
$DATE-extractions.json.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
PROMPT_FILE = Path(__file__).parent / "extract-prompt.md"

# No timeout -- user pays unlimited Claude Code, sessions can take as long as needed
CLAUDE_TIMEOUT = 21600  # 3h safety net (prevents zombie processes)


def find_claude_cli() -> str:
    """Find the Claude CLI binary."""
    # Check common locations
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Try PATH
    which = shutil.which("claude")
    if which:
        return which
    return "claude"  # Fallback, let it fail with a clear error


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from Claude's response, handling markdown fences."""
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[1:end]).strip()

    # Find the outermost JSON object
    brace_start = text.find("{")
    if brace_start < 0:
        return None

    # Find matching closing brace
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:i + 1])
                except json.JSONDecodeError:
                    break
    return None


def find_session_file(session_id: str, date_dir: Path) -> Path | None:
    """Find the individual .txt file for a session."""
    if date_dir and date_dir.exists():
        sid_short = session_id.replace(".jsonl", "")[:20]
        for f in sorted(date_dir.glob("session-*.txt")):
            if sid_short in f.name:
                return f
    return None


def analyze_session(session_id: str, date_dir: Path, shared_context_file: Path | None, claude_bin: str) -> dict | None:
    """Send a session to Claude CLI for extraction analysis.

    Claude CLI reads the small per-session file + shared context file.
    Prompt is short — the heavy lifting is in the Read tool calls.
    """
    session_file = find_session_file(session_id, date_dir)
    if not session_file:
        print(f"    No session file found for {session_id}", file=sys.stderr)
        return None

    print(f"    File: {session_file.name} ({session_file.stat().st_size / 1024:.0f} KB)")

    # Build a short prompt — Claude reads the files itself
    shared_ctx_instruction = ""
    if shared_context_file and shared_context_file.exists():
        shared_ctx_instruction = f"\n\nAlso read the shared context (followups, learnings, DB state) at: {shared_context_file}"

    prompt_template = PROMPT_FILE.read_text()
    prompt = prompt_template.replace("{{CONTEXT_FILE}}", str(session_file))
    prompt = prompt.replace("{{SESSION_ID}}", session_id)
    prompt += shared_ctx_instruction

    try:
        env = os.environ.copy()
        env["NEXO_HEADLESS"] = "1"  # Skip stop hook post-mortem
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE", None)

        result = subprocess.run(
            [
                claude_bin,
                "-p", prompt,
                "--model", "opus",
                "--output-format", "text",
                "--allowedTools",
                "Read,Grep,Bash"
            ],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            env=env
        )

        if result.returncode != 0:
            print(f"    Claude CLI error (exit {result.returncode}): {result.stderr[:300]}", file=sys.stderr)
            return None

        # Filter out stop hook contamination (e.g. "Post-mortem completo.")
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if not line.strip().startswith("Post-mortem") and line.strip()
        )
        parsed = extract_json_from_response(output)
        if not parsed:
            # Save raw output for debugging
            debug_file = DEEP_SLEEP_DIR / f"debug-extract-{session_id[:20]}.txt"
            debug_file.write_text(result.stdout[:5000])
            print(f"    Failed to parse JSON. Raw output saved to {debug_file}", file=sys.stderr)
            return None

        return parsed

    except subprocess.TimeoutExpired:
        print(f"    Claude CLI timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"    Claude CLI not found at: {claude_bin}", file=sys.stderr)
        print("    Install: npm install -g @anthropic-ai/claude-code", file=sys.stderr)
        sys.exit(1)


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    context_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"
    meta_file = DEEP_SLEEP_DIR / f"{target_date}-meta.json"
    date_dir = DEEP_SLEEP_DIR / target_date

    if not context_file.exists() and not date_dir.exists():
        print(f"[extract] No context for {target_date}. Run collect.py first.")
        sys.exit(1)

    # Read metadata to get session list
    if meta_file.exists():
        with open(meta_file) as f:
            meta = json.load(f)
        session_files = meta.get("session_files", [])
    else:
        # Fallback: parse context file for session IDs
        print("[extract] No meta file found, scanning context for sessions...")
        session_files = []
        if context_file.exists():
            for line in context_file.read_text().splitlines():
                if line.startswith("SESSION ") and ":" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        sid = parts[1].strip()
                        if sid.endswith(".jsonl"):
                            session_files.append(sid)

    if not session_files:
        print(f"[extract] No sessions to analyze for {target_date}.")
        output = {"date": target_date, "sessions_analyzed": 0, "extractions": []}
        output_file = DEEP_SLEEP_DIR / f"{target_date}-extractions.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[extract] Output: {output_file}")
        return

    # Shared context file (followups, learnings, DB state)
    shared_context_file = date_dir / "shared-context.txt" if date_dir.exists() else None
    if shared_context_file and shared_context_file.exists():
        print(f"[extract] Shared context: {shared_context_file} ({shared_context_file.stat().st_size / 1024:.0f} KB)")
    else:
        shared_context_file = None
        print("[extract] No shared context file")

    claude_bin = find_claude_cli()
    print(f"[extract] Phase 2: Analyzing {len(session_files)} sessions for {target_date}")
    print(f"[extract] Claude CLI: {claude_bin}")

    all_extractions = []
    total_findings = 0

    for i, session_id in enumerate(session_files):
        print(f"[extract] Session {i + 1}/{len(session_files)}: {session_id}")

        result = analyze_session(session_id, date_dir, shared_context_file, claude_bin)

        if result:
            findings_count = len(result.get("findings", []))
            total_findings += findings_count
            all_extractions.append(result)
            print(f"    -> {findings_count} findings extracted")
        else:
            print(f"    -> Extraction failed, continuing with next session")
            all_extractions.append({
                "session_id": session_id,
                "findings": [],
                "error": "Extraction failed"
            })

    # Merge into output
    output = {
        "date": target_date,
        "sessions_analyzed": len(session_files),
        "sessions_succeeded": len([e for e in all_extractions if "error" not in e]),
        "total_findings": total_findings,
        "extractions": all_extractions
    }

    output_file = DEEP_SLEEP_DIR / f"{target_date}-extractions.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[extract] Done. {total_findings} total findings from {len(session_files)} sessions.")
    print(f"[extract] Output: {output_file}")


if __name__ == "__main__":
    main()
