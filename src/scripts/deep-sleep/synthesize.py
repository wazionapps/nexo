#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 3: Synthesize extractions into actionable findings.

One Claude call that reads all per-session extractions and produces a
unified synthesis with cross-session patterns, morning agenda, context
packets, and deduplicated actions.

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
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
PROMPT_FILE = Path(__file__).parent / "synthesize-prompt.md"

if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

CLAUDE_TIMEOUT = 21600  # 3h safety net (prevents zombie processes)


def find_claude_cli() -> str:
    """Find the Claude CLI binary."""
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    which = shutil.which("claude")
    if which:
        return which
    return "claude"


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from Claude's response, handling markdown fences."""
    text = text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[1:end]).strip()

    brace_start = text.find("{")
    if brace_start < 0:
        return None

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


def collect_skill_runtime_candidates(target_date: str) -> tuple[Path, dict]:
    """Collect mature skill candidates from DB usage so Deep Sleep can evolve them."""
    output_file = DEEP_SLEEP_DIR / f"{target_date}-skill-runtime-candidates.json"
    payload = {
        "scriptable": [],
        "improvements": [],
    }
    try:
        from db import (
            collect_scriptable_skill_candidates,
            collect_skill_improvement_candidates,
            init_db,
        )

        init_db()
        payload["scriptable"] = collect_scriptable_skill_candidates()
        payload["improvements"] = collect_skill_improvement_candidates()
    except Exception as e:
        payload["error"] = str(e)

    with open(output_file, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return output_file, payload


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    extractions_file = DEEP_SLEEP_DIR / f"{target_date}-extractions.json"
    context_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"

    if not extractions_file.exists():
        print(f"[synthesize] No extractions file for {target_date}. Run extract.py first.")
        sys.exit(1)

    # Check if there are any findings worth synthesizing
    with open(extractions_file) as f:
        extractions = json.load(f)

    total_findings = extractions.get("total_findings", 0)
    runtime_candidates_file, runtime_candidates = collect_skill_runtime_candidates(target_date)
    runtime_candidate_count = len(runtime_candidates.get("scriptable", [])) + len(runtime_candidates.get("improvements", []))

    if total_findings == 0 and runtime_candidate_count == 0:
        print(f"[synthesize] No findings to synthesize for {target_date}.")
        # Write minimal synthesis
        output = {
            "date": target_date,
            "sessions_analyzed": extractions.get("sessions_analyzed", 0),
            "cross_session_patterns": [],
            "morning_agenda": [],
            "context_packets": [],
            "skills": [],
            "skill_evolution_candidates": [],
            "actions": [],
            "summary": f"No significant findings for {target_date}."
        }
        output_file = DEEP_SLEEP_DIR / f"{target_date}-synthesis.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"[synthesize] Output: {output_file}")
        return

    # Build prompt
    prompt_template = PROMPT_FILE.read_text()
    prompt = prompt_template.replace("{{EXTRACTIONS_FILE}}", str(extractions_file))
    prompt = prompt.replace("{{CONTEXT_FILE}}", str(context_file))
    prompt = prompt.replace("{{SKILL_RUNTIME_FILE}}", str(runtime_candidates_file))

    claude_bin = find_claude_cli()
    print(f"[synthesize] Phase 3: Synthesizing {total_findings} findings from {target_date}")
    print(f"[synthesize] Skill runtime candidates: {runtime_candidate_count}")
    print(f"[synthesize] Claude CLI: {claude_bin}")

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
            print(f"[synthesize] Claude CLI error (exit {result.returncode}): {result.stderr[:300]}", file=sys.stderr)
            sys.exit(1)

        # Filter hook contamination
        output_text = "\n".join(
            l for l in result.stdout.strip().splitlines()
            if not l.strip().startswith("Post-mortem")
        )
        parsed = extract_json_from_response(output_text)

        # Fallback: Opus might have written the file directly via Write tool
        if not parsed:
            for candidate in [
                DEEP_SLEEP_DIR / f"{target_date}-analysis.json",
                DEEP_SLEEP_DIR / f"{target_date}-synthesis.json",
            ]:
                if candidate.exists() and candidate.stat().st_size > 100:
                    try:
                        parsed = json.load(open(candidate))
                        print(f"[synthesize] Opus wrote file directly: {candidate}")
                        break
                    except Exception:
                        continue

        if not parsed:
            debug_file = DEEP_SLEEP_DIR / f"debug-synthesize-{target_date}.txt"
            debug_file.write_text(result.stdout[:10000])
            print(f"[synthesize] Failed to parse JSON. Raw output saved to {debug_file}", file=sys.stderr)
            sys.exit(1)

        # Write synthesis output
        output_file = DEEP_SLEEP_DIR / f"{target_date}-synthesis.json"
        with open(output_file, "w") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)

        n_actions = len(parsed.get("actions", []))
        n_patterns = len(parsed.get("cross_session_patterns", []))
        n_agenda = len(parsed.get("morning_agenda", []))
        n_packets = len(parsed.get("context_packets", []))

        print(f"[synthesize] Done.")
        print(f"  Actions: {n_actions}")
        print(f"  Cross-session patterns: {n_patterns}")
        print(f"  Morning agenda items: {n_agenda}")
        print(f"  Context packets: {n_packets}")
        print(f"[synthesize] Output: {output_file}")

    except subprocess.TimeoutExpired:
        print(f"[synthesize] Claude CLI timeout ({CLAUDE_TIMEOUT}s)", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"[synthesize] Claude CLI not found at: {claude_bin}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
