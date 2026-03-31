#!/usr/bin/env python3
"""
Deep Sleep — Step 2: Analyze transcripts with Claude CLI (bare mode).
Sends each session to Claude opus for analysis, then consolidates findings.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

PROMPT_FILE = Path(__file__).parent / "prompt.md"
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
MAX_TRANSCRIPT_CHARS = 150_000


def build_transcript_text(session: dict) -> str:
    """Build a readable transcript from a session."""
    lines = [
        f"## Session: {session['session_file']}",
        f"Modified: {session['modified']}",
        f"Messages: {session['message_count']}, Tool uses: {session['tool_use_count']}",
        "",
        "### Conversation"
    ]
    for msg in session["messages"]:
        role = "USER" if msg["role"] == "user" else "NEXO"
        lines.append(f"\n**{role}:**")
        lines.append(msg["text"])

    if session["tool_uses"]:
        lines.append("\n### Tool Usage Log")
        for tu in session["tool_uses"]:
            file_info = f" [{tu['file'][:80]}]" if tu.get("file") else ""
            lines.append(f"- {tu['tool']}{file_info}")

    return "\n".join(lines)


def find_api_key() -> str | None:
    """Find Anthropic API key from common locations."""
    # Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key

    # Common file locations
    for path in [
        Path.home() / ".claude" / "anthropic-api-key.txt",
        Path.home() / ".anthropic" / "api_key",
        Path.home() / ".config" / "anthropic" / "api_key",
    ]:
        if path.exists():
            return path.read_text().strip()

    return None


def analyze_with_claude(transcript: str, prompt: str) -> dict | None:
    """Send transcript to Claude CLI for analysis."""
    full_prompt = (
        f"{prompt}\n\n---\n\n# TODAY'S TRANSCRIPT\n\n{transcript}\n\n---\n\n"
        "Analyze this transcript and return the JSON output as specified. "
        "Return ONLY the JSON, no markdown code fences."
    )

    api_key = find_api_key()
    env = os.environ.copy()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--model", "opus", "--output-format", "text",
             "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*"],
            capture_output=True, text=True, timeout=300, env=env
        )

        if result.returncode != 0:
            print(f"Claude CLI error: {result.stderr[:500]}", file=sys.stderr)
            return None

        response_text = result.stdout.strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            response_text = response_text.strip()

        # Find JSON object in response
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            response_text = response_text[json_start:json_end]

        return json.loads(response_text)

    except subprocess.TimeoutExpired:
        print("Claude CLI timeout (300s)", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse Claude response: {e}", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code", file=sys.stderr)
        return None


def consolidate_findings(results: list[dict]) -> dict:
    """Merge findings from multiple sessions into one report."""
    consolidated = {
        "uncaptured_corrections": [],
        "uncaptured_ideas": [],
        "missed_commitments": [],
        "protocol_compliance": {
            "guard_check": {"required": 0, "executed": 0},
            "heartbeat_quality": {"total": 0, "with_good_context": 0},
            "trust_adjustments": {"corrections_detected": 0, "adjusted": 0},
            "learning_capture": {"errors_resolved": 0, "captured": 0},
            "change_log": {"production_edits": 0, "logged": 0},
            "feedback_capture": {"corrections": 0, "captured": 0},
        },
        "protocol_violations": [],
        "quality_issues": [],
        "auto_reinforcements": [],
    }

    for r in results:
        if not r:
            continue
        for key in ["uncaptured_corrections", "uncaptured_ideas", "missed_commitments",
                     "protocol_violations", "quality_issues", "auto_reinforcements"]:
            consolidated[key].extend(r.get(key, []))

        pc = r.get("protocol_compliance", {})
        for key in consolidated["protocol_compliance"]:
            if key in pc and isinstance(pc[key], dict):
                for subkey in consolidated["protocol_compliance"][key]:
                    consolidated["protocol_compliance"][key][subkey] += pc[key].get(subkey, 0)

    # Calculate rates
    for key, vals in consolidated["protocol_compliance"].items():
        keys = list(vals.keys())
        if len(keys) == 2:
            denominator = vals[keys[0]]
            numerator = vals[keys[1]]
            vals["rate"] = round(numerator / denominator, 2) if denominator > 0 else 1.0

    rates = [v.get("rate", 1.0) for v in consolidated["protocol_compliance"].values()]
    consolidated["protocol_compliance"]["overall_compliance"] = round(sum(rates) / len(rates), 2) if rates else 1.0
    consolidated["auto_reinforcements"] = list(set(consolidated["auto_reinforcements"]))

    return consolidated


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    transcripts_file = DEEP_SLEEP_DIR / f"{date}-transcripts.json"
    if not transcripts_file.exists():
        print(f"No transcripts found for {date}. Run collect_transcripts.py first.")
        sys.exit(1)

    with open(transcripts_file) as f:
        data = json.load(f)

    sessions = data["sessions"]
    print(f"Analyzing {len(sessions)} sessions from {date}...")

    prompt = PROMPT_FILE.read_text()

    results = []
    for i, session in enumerate(sessions):
        transcript = build_transcript_text(session)

        if len(transcript) < 500:
            print(f"  Session {i+1}/{len(sessions)}: skipped (too short)")
            continue

        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n\n[TRUNCATED]"

        print(f"  Session {i+1}/{len(sessions)}: {session['session_file'][:12]}... ({len(transcript)} chars)")
        result = analyze_with_claude(transcript, prompt)
        if result:
            results.append(result)
            print(f"    → {len(result.get('uncaptured_corrections', []))} corrections, "
                  f"{len(result.get('protocol_violations', []))} violations")
        else:
            print(f"    → Analysis failed")

    consolidated = consolidate_findings(results)
    consolidated["date"] = date
    consolidated["sessions_analyzed"] = len(results)

    n_corrections = len(consolidated["uncaptured_corrections"])
    n_violations = len(consolidated["protocol_violations"])
    compliance = consolidated["protocol_compliance"]["overall_compliance"]
    consolidated["summary"] = (
        f"Analyzed {len(results)} sessions. "
        f"Found {n_corrections} uncaptured corrections, {n_violations} protocol violations. "
        f"Overall compliance: {compliance:.0%}."
    )

    output_file = DEEP_SLEEP_DIR / f"{date}-analysis.json"
    with open(output_file, "w") as f:
        json.dump(consolidated, f, indent=2, ensure_ascii=False)

    print(f"\nResults: {output_file}")
    print(consolidated["summary"])


if __name__ == "__main__":
    main()
