#!/usr/bin/env python3
"""Context checker for NEXO operations - prevents duplicate actions.

Mechanical checks (email sent, file exists, action done) run in Python.
When the 'smart' command is used, passes context to Claude CLI for
intelligent duplicate/conflict detection that goes beyond file checks.
"""

import os
import sys
import json
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"

class ContextChecker:
    def __init__(self):
        self.state_dir = NEXO_HOME / 'state'
        self.state_dir.mkdir(exist_ok=True)
        
    def check_email_sent(self, to_addr, subject, since_hours=72):
        """Check if email was already sent to address with subject."""
        sent_path = Path.home() / 'mail' / '.nexo-sent' / '.Sent'  # Configure for your mail setup
        if not sent_path.exists():
            return False

        subject_lower = subject.lower()
        to_lower = to_addr.lower()
        cutoff = datetime.now().timestamp() - (since_hours * 3600)
        cur_dir = sent_path / 'cur'
        if not cur_dir.exists():
            return False

        for msg_file in cur_dir.iterdir():
            try:
                if msg_file.stat().st_mtime < cutoff:
                    continue
                content = msg_file.read_text(errors='ignore')
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
                '/var/www/vhosts',
                str(NEXO_HOME),
                '/opt'
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
        action_file = self.state_dir / 'actions.json'
        
        # Load existing actions
        actions = {}
        if action_file.exists():
            with open(action_file) as f:
                actions = json.load(f)
                
        # Create action key
        key = hashlib.md5(f"{action_type}:{identifier}".encode()).hexdigest()
        
        # Check if exists and not expired
        if key in actions:
            action_time = datetime.fromisoformat(actions[key]['timestamp'])
            age_days = (datetime.now() - action_time).days
            if age_days < ttl_days:
                return True, actions[key]
                
        return False, None
        
    def mark_action_done(self, action_type, identifier, metadata=None):
        """Mark action as completed."""
        action_file = self.state_dir / 'actions.json'
        
        # Load existing actions
        actions = {}
        if action_file.exists():
            with open(action_file) as f:
                actions = json.load(f)
                
        # Add new action
        key = hashlib.md5(f"{action_type}:{identifier}".encode()).hexdigest()
        actions[key] = {
            'type': action_type,
            'identifier': identifier,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata or {}
        }
        
        # Save
        with open(action_file, 'w') as f:
            json.dump(actions, f, indent=2)
            
        return key

def smart_check(action_description: str, context: str = "") -> dict:
    """Use Claude CLI to intelligently check if an action would be redundant.

    Goes beyond simple file/hash checks — understands intent and context
    to detect semantic duplicates (e.g., "send welcome email" vs
    "email onboarding message" to same person).
    """
    checker = ContextChecker()

    # Gather mechanical context first
    state_file = checker.state_dir / 'actions.json'
    recent_actions = {}
    if state_file.exists():
        try:
            all_actions = json.loads(state_file.read_text())
            cutoff = datetime.now().timestamp() - (7 * 86400)
            for k, v in all_actions.items():
                try:
                    ts = datetime.fromisoformat(v['timestamp']).timestamp()
                    if ts > cutoff:
                        recent_actions[k] = v
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass

    if not CLAUDE_CLI.exists():
        return {"redundant": False, "reason": "CLI unavailable, cannot smart-check"}

    prompt = f"""You are a context deduplication engine for NEXO operations.

PROPOSED ACTION:
{action_description}

ADDITIONAL CONTEXT:
{context or "None"}

RECENT ACTIONS (last 7 days):
{json.dumps(list(recent_actions.values()), indent=1, default=str)}

Respond with ONLY valid JSON (no markdown):
{{
  "redundant": true/false,
  "confidence": 0.0-1.0,
  "reason": "<one line explanation>",
  "matching_action": "<identifier of matching action if redundant, else null>"
}}

Rules:
- Same recipient + same intent within 72h = redundant
- Same file modification with same content = redundant
- Similar but different scope (e.g., different recipients) = NOT redundant
- When in doubt, say not redundant (false negatives are cheaper than false positives)"""

    auth_check = subprocess.run(
        [str(CLAUDE_CLI), "-p", "Reply with exactly: ok", "--bare", "--output-format", "text", "--model", "haiku"],
        capture_output=True, text=True, timeout=15
    )
    if auth_check.returncode != 0:
        # CLI not authenticated, skip gracefully
        return {"redundant": False, "reason": "CLI not authenticated — skipped analysis", "suggestion": "N/A"}

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    try:
        result = subprocess.run(
            [str(CLAUDE_CLI), "-p", prompt, "--model", "opus", "--output-format", "text", "--bare",
             "--allowedTools", "Read,Write,Edit,Glob,Grep"],
            capture_output=True, text=True, timeout=60, env=env
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return json.loads(text.strip())
    except Exception:
        pass

    return {"redundant": False, "reason": "CLI check failed, defaulting to not redundant"}


def main():
    """CLI interface for context checking."""
    if len(sys.argv) < 3:
        print("Usage: check-context.py <command> <args>")
        print("Commands:")
        print("  email <to> <subject>       - Check if email was sent")
        print("  file <pattern>             - Check if file exists")
        print("  action <type> <id>         - Check if action was done")
        print("  smart <description> [ctx]  - Intelligent duplicate check via CLI")
        sys.exit(1)

    checker = ContextChecker()
    command = sys.argv[1]

    if command == 'email':
        if len(sys.argv) < 4:
            print("Usage: check-context.py email <to> <subject>")
            sys.exit(1)
        exists = checker.check_email_sent(sys.argv[2], sys.argv[3])
        print("EXISTS" if exists else "NOT_FOUND")
        sys.exit(0 if not exists else 1)

    elif command == 'file':
        files = checker.check_file_exists(sys.argv[2])
        if files:
            print("\n".join(files))
            sys.exit(1)
        else:
            print("NOT_FOUND")
            sys.exit(0)

    elif command == 'action':
        if len(sys.argv) < 4:
            print("Usage: check-context.py action <type> <id>")
            sys.exit(1)
        done, data = checker.check_action_done(sys.argv[2], sys.argv[3])
        if done:
            print(f"DONE: {data}")
            sys.exit(1)
        else:
            print("NOT_DONE")
            sys.exit(0)

    elif command == 'smart':
        if len(sys.argv) < 3:
            print("Usage: check-context.py smart <description> [context]")
            sys.exit(1)
        description = sys.argv[2]
        context = sys.argv[3] if len(sys.argv) > 3 else ""
        result = smart_check(description, context)
        print(json.dumps(result, indent=2))
        sys.exit(1 if result.get("redundant") else 0)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == '__main__':
    main()
