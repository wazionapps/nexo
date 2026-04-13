#!/usr/bin/env python3
"""Context checker for NEXO operations - prevents duplicate actions.

Mechanical checks (email sent, file exists, action done) run in Python.
When the 'smart' command is used, NEXO asks the configured automation backend
for semantic duplicate/conflict detection that goes beyond file checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[1])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""



class ContextChecker:
    def __init__(self):
        self.state_dir = NEXO_HOME / "state"
        self.state_dir.mkdir(exist_ok=True)

    def check_email_sent(self, to_addr, subject, since_hours=72):
        """Check if email was already sent to address with subject."""
        sent_path = Path.home() / "mail" / ".nexo-sent" / ".Sent"
        if not sent_path.exists():
            return False

        subject_lower = subject.lower()
        to_lower = to_addr.lower()
        cutoff = datetime.now().timestamp() - (since_hours * 3600)
        cur_dir = sent_path / "cur"
        if not cur_dir.exists():
            return False

        for msg_file in cur_dir.iterdir():
            try:
                if msg_file.stat().st_mtime < cutoff:
                    continue
                content = msg_file.read_text(errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            content_lower = content.lower()
            if f"to:{to_lower}" in content_lower or f"to: {to_lower}" in content_lower:
                if subject_lower in content_lower:
                    return True
        return False

    def check_file_exists(self, pattern, search_dirs=None):
        """Check if file matching pattern exists in common locations."""
        if search_dirs is None:
            search_dirs = [
                "/var/www/vhosts",
                str(NEXO_HOME),
                "/opt",
            ]

        for base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue
            matches = []
            try:
                for root, _, files in os.walk(base_dir):
                    for filename in files:
                        if pattern in filename:
                            matches.append(str(Path(root) / filename))
                            if len(matches) >= 5:
                                return matches
            except OSError:
                continue
        return []

    def check_action_done(self, action_type, identifier, ttl_days=7):
        """Check if action was already performed recently."""
        action_file = self.state_dir / "actions.json"
        actions = {}
        if action_file.exists():
            with open(action_file) as fh:
                actions = json.load(fh)

        key = hashlib.md5(f"{action_type}:{identifier}".encode(), usedforsecurity=False).hexdigest()
        if key in actions:
            action_time = datetime.fromisoformat(actions[key]["timestamp"])
            age_days = (datetime.now() - action_time).days
            if age_days < ttl_days:
                return True, actions[key]
        return False, None

    def mark_action_done(self, action_type, identifier, metadata=None):
        """Mark action as completed."""
        action_file = self.state_dir / "actions.json"
        actions = {}
        if action_file.exists():
            with open(action_file) as fh:
                actions = json.load(fh)

        key = hashlib.md5(f"{action_type}:{identifier}".encode(), usedforsecurity=False).hexdigest()
        actions[key] = {
            "type": action_type,
            "identifier": identifier,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        with open(action_file, "w") as fh:
            json.dump(actions, fh, indent=2)
        return key


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        end = len(lines)
        for idx in range(len(lines) - 1, 0, -1):
            if lines[idx].strip() == "```":
                end = idx
                break
        text = "\n".join(lines[1:end]).strip()
    brace_start = text.find("{")
    if brace_start < 0:
        return None
    depth = 0
    for idx in range(brace_start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


def smart_check(action_description: str, context: str = "") -> dict:
    """Use the automation backend to check if an action would be redundant."""
    checker = ContextChecker()

    state_file = checker.state_dir / "actions.json"
    recent_actions = {}
    if state_file.exists():
        try:
            all_actions = json.loads(state_file.read_text())
            cutoff = datetime.now().timestamp() - (7 * 86400)
            for key, value in all_actions.items():
                try:
                    ts = datetime.fromisoformat(value["timestamp"]).timestamp()
                    if ts > cutoff:
                        recent_actions[key] = value
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass

    prompt = f"""You are a context deduplication engine for NEXO operations.

PROPOSED ACTION:
{action_description}

ADDITIONAL CONTEXT:
{context or "None"}

RECENT ACTIONS (last 7 days):
{json.dumps(list(recent_actions.values()), indent=1, default=str, ensure_ascii=False)}

Respond with ONLY valid JSON:
{{
  "redundant": true/false,
  "confidence": 0.0-1.0,
  "reason": "<one line explanation>",
  "matching_action": "<identifier of matching action if redundant, else null>"
}}

Rules:
- Same recipient + same intent within 72h = redundant
- Same file modification with same content = redundant
- Similar but different scope (e.g. different recipients) = NOT redundant
- When in doubt, say not redundant (false negatives are cheaper than false positives)"""

    try:
        result = run_automation_prompt(
            prompt,
            model=_USER_MODEL or "opus",
            timeout=300,
            output_format="text",
            append_system_prompt="Return exactly one valid JSON object.",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )
        if result.returncode == 0:
            parsed = _extract_json(result.stdout)
            if parsed:
                return parsed
    except AutomationBackendUnavailableError as exc:
        return {"redundant": False, "reason": f"Automation backend unavailable — {exc}"}
    except Exception:
        pass

    return {"redundant": False, "reason": "Automation check failed, defaulting to not redundant"}


def main():
    """CLI interface for context checking."""
    if len(sys.argv) < 3:
        print("Usage: check-context.py <command> <args>")
        print("Commands:")
        print("  email <to> <subject>       - Check if email was sent")
        print("  file <pattern>             - Check if file exists")
        print("  action <type> <id>         - Check if action was done")
        print("  smart <description> [ctx]  - Intelligent duplicate check via automation backend")
        sys.exit(1)

    checker = ContextChecker()
    command = sys.argv[1]

    if command == "email":
        if len(sys.argv) < 4:
            print("Usage: check-context.py email <to> <subject>")
            sys.exit(1)
        exists = checker.check_email_sent(sys.argv[2], sys.argv[3])
        print("EXISTS" if exists else "NOT_FOUND")
        sys.exit(0 if not exists else 1)

    if command == "file":
        files = checker.check_file_exists(sys.argv[2])
        if files:
            print("\n".join(files))
            sys.exit(1)
        print("NOT_FOUND")
        sys.exit(0)

    if command == "action":
        if len(sys.argv) < 4:
            print("Usage: check-context.py action <type> <id>")
            sys.exit(1)
        done, data = checker.check_action_done(sys.argv[2], sys.argv[3])
        if done:
            print(f"DONE: {data}")
            sys.exit(1)
        print("NOT_DONE")
        sys.exit(0)

    if command == "smart":
        description = sys.argv[2]
        context = sys.argv[3] if len(sys.argv) > 3 else ""
        result = smart_check(description, context)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1 if result.get("redundant") else 0)

    print(f"Unknown command: {command}")
    sys.exit(1)


if __name__ == "__main__":
    main()
