#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 2: Extract findings from each session using the configured automation backend.

For each session in the context file, sends the extract-prompt.md to Claude
and collects structured findings. Merges all per-session results into
$DATE-extractions.json.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
PROMPT_FILE = Path(__file__).parent / "extract-prompt.md"

if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt

# No timeout -- headless automation can take as long as needed
CLAUDE_TIMEOUT = 21600  # 3h safety net (prevents zombie processes)


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


def _safe_session_slug(session_id: str) -> str:
    return (
        session_id
        .replace(".jsonl", "")
        .replace(":", "-")
        .replace("/", "-")
    )


def find_session_file(session_id: str, date_dir: Path, session_txt_map: dict[str, str] | None = None) -> Path | None:
    """Find the individual .txt file for a session."""
    if session_txt_map:
        mapped = session_txt_map.get(session_id)
        if mapped:
            candidate = date_dir / mapped
            if candidate.exists():
                return candidate
    if date_dir and date_dir.exists():
        sid_short = _safe_session_slug(session_id)[:20]
        for f in sorted(date_dir.glob("session-*.txt")):
            if sid_short in f.name:
                return f
    return None


def analyze_session(
    session_id: str,
    date_dir: Path,
    shared_context_file: Path | None,
    session_txt_map: dict[str, str] | None = None,
) -> dict | None:
    """Send a session to the automation backend for extraction analysis.

    The backend reads the small per-session file + shared context file.
    Prompt is short — the heavy lifting is in the Read tool calls.
    """
    session_file = find_session_file(session_id, date_dir, session_txt_map=session_txt_map)
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
        JSON_SYSTEM_PROMPT = (
            "You are a JSON-only analyst. Your ENTIRE response must be a single valid JSON object. "
            "No text before it. No text after it. No markdown fences. No explanations. "
            "If you want to summarize, put it inside the JSON fields. Start with { and end with }."
        )

        result = run_automation_prompt(
            prompt,
            model="opus",
            timeout=CLAUDE_TIMEOUT,
            output_format="text",
            append_system_prompt=JSON_SYSTEM_PROMPT,
            allowed_tools="Read,Grep,Bash",
        )

        if result.returncode != 0:
            print(f"    Automation backend error (exit {result.returncode}): {result.stderr[:300]}", file=sys.stderr)
            return None

        # Filter out stop hook contamination (e.g. "Post-mortem completo.")
        output = "\n".join(
            line for line in result.stdout.splitlines()
            if not line.strip().startswith("Post-mortem") and line.strip()
        )
        parsed = extract_json_from_response(output)

        # Fallback: if Claude returned text instead of JSON, ask a short conversion call
        if not parsed and len(output.strip()) > 50:
            print(f"    Got text instead of JSON ({len(output)} chars). Converting...")
            convert_prompt = (
                f"Convert the following analysis into the exact JSON schema required. "
                f"Return ONLY the JSON object, nothing else.\n\n"
                f"Analysis:\n{output[:8000]}\n\n"
                f"Required schema: session_id, findings[], emotional_timeline[], "
                f"abandoned_projects[], skill_candidates[], productivity_score, protocol_summary"
            )
            convert_result = run_automation_prompt(
                convert_prompt,
                model="sonnet",
                timeout=120,
                output_format="text",
                append_system_prompt=JSON_SYSTEM_PROMPT,
            )
            if convert_result.returncode == 0:
                parsed = extract_json_from_response(convert_result.stdout)
                if parsed:
                    print(f"    Conversion succeeded")

        if not parsed:
            # Save raw output for debugging
            debug_file = DEEP_SLEEP_DIR / f"debug-extract-{session_id[:20]}.txt"
            debug_file.write_text(result.stdout[:5000])
            print(f"    Failed to parse JSON. Raw output saved to {debug_file}", file=sys.stderr)
            return None

        return parsed

    except AutomationBackendUnavailableError as exc:
        print(f"    Automation backend unavailable: {exc}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"    Automation backend timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr)
        return None


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
        session_txt_map = meta.get("session_txt_map", {})
    else:
        # Fallback: parse context file for session IDs
        print("[extract] No meta file found, scanning context for sessions...")
        session_files = []
        session_txt_map = {}
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

    print(f"[extract] Phase 2: Analyzing {len(session_files)} sessions for {target_date}")
    print("[extract] Automation backend: schedule-configured")

    # Checkpoint directory: one JSON per session, survives crashes
    checkpoint_dir = date_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    all_extractions = []
    total_findings = 0
    skipped = 0
    MAX_RETRIES = 3

    for i, session_id in enumerate(session_files):
        sid_safe = _safe_session_slug(session_id)[:40]
        checkpoint_file = checkpoint_dir / f"{sid_safe}.json"

        # Resume: skip already-processed sessions
        if checkpoint_file.exists():
            try:
                with open(checkpoint_file) as f:
                    cached = json.load(f)
                findings_count = len(cached.get("findings", []))
                total_findings += findings_count
                all_extractions.append(cached)
                skipped += 1
                print(f"[extract] Session {i + 1}/{len(session_files)}: {session_id} (cached, {findings_count} findings)")
                continue
            except (json.JSONDecodeError, KeyError):
                pass  # Corrupted checkpoint, re-process

        print(f"[extract] Session {i + 1}/{len(session_files)}: {session_id}")

        # Retry loop
        result = None
        for attempt in range(1, MAX_RETRIES + 1):
            result = analyze_session(
                session_id,
                date_dir,
                shared_context_file,
                session_txt_map=session_txt_map,
            )
            if result:
                break
            if attempt < MAX_RETRIES:
                print(f"    -> Attempt {attempt}/{MAX_RETRIES} failed, retrying...")

        if result:
            findings_count = len(result.get("findings", []))
            total_findings += findings_count
            all_extractions.append(result)
            # Save checkpoint
            with open(checkpoint_file, "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"    -> {findings_count} findings extracted (checkpointed)")
        else:
            print(f"    -> Failed after {MAX_RETRIES} attempts, marking as failed")
            failed_entry = {
                "session_id": session_id,
                "findings": [],
                "error": f"Extraction failed after {MAX_RETRIES} attempts"
            }
            all_extractions.append(failed_entry)
            # Save failed checkpoint too (so we don't retry forever)
            with open(checkpoint_file, "w") as f:
                json.dump(failed_entry, f, indent=2, ensure_ascii=False)

    # Merge into output
    output = {
        "date": target_date,
        "sessions_analyzed": len(session_files),
        "sessions_succeeded": len([e for e in all_extractions if "error" not in e]),
        "sessions_cached": skipped,
        "total_findings": total_findings,
        "extractions": all_extractions
    }

    output_file = DEEP_SLEEP_DIR / f"{target_date}-extractions.json"
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    if skipped:
        print(f"\n[extract] Done. {total_findings} findings from {len(session_files)} sessions ({skipped} cached, {len(session_files) - skipped} new).")
    else:
        print(f"\n[extract] Done. {total_findings} findings from {len(session_files)} sessions.")
    print(f"[extract] Output: {output_file}")


if __name__ == "__main__":
    main()
