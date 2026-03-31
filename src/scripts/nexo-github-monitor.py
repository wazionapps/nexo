#!/usr/bin/env python3
"""
NEXO GitHub Monitor — Wrapper + CLI pattern.
Python: gh CLI API calls, data collection.
CLI: Generates rich analysis and suggested responses for issues/PRs.

Runs at 08:00 via LaunchAgent.
Results saved to ~/.nexo/github-status.json.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", Path.home() / ".nexo"))
STATUS_FILE = NEXO_HOME / "github-status.json"
LOG_FILE = NEXO_HOME / "logs" / "github-monitor.log"
REPO = "wazionapps/nexo"
CLAUDE_CLI = Path.home() / ".local" / "bin" / "claude"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def gh_api(endpoint: str) -> dict | list | None:
    """Call GitHub API via gh."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def collect_data():
    """Collect all GitHub data — mechanical work."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "repo": REPO,
        "issues": [],
        "prs": [],
        "latest_release": None,
        "unreleased_commits": 0,
    }

    # Issues
    log("Fetching issues...")
    issues = gh_api(f"repos/{REPO}/issues?state=open&per_page=50")
    if issues:
        for issue in issues:
            if "pull_request" in issue:
                continue
            item = {
                "number": issue["number"],
                "title": issue["title"][:80],
                "body": (issue.get("body") or "")[:500],
                "created": issue["created_at"][:10],
                "comments": issue["comments"],
                "labels": [l["name"] for l in issue.get("labels", [])],
                "author": issue.get("user", {}).get("login", ""),
            }
            # Get comment bodies for context
            if issue["comments"] > 0:
                comments = gh_api(f"repos/{REPO}/issues/{issue['number']}/comments?per_page=5")
                if comments:
                    item["comment_bodies"] = [
                        {"author": c.get("user", {}).get("login", ""), "body": c.get("body", "")[:300]}
                        for c in comments[:5]
                    ]
            data["issues"].append(item)

    # PRs
    log("Fetching PRs...")
    prs = gh_api(f"repos/{REPO}/pulls?state=open&per_page=50")
    if prs:
        for pr in prs:
            reviews = gh_api(f"repos/{REPO}/pulls/{pr['number']}/reviews") or []
            item = {
                "number": pr["number"],
                "title": pr["title"][:80],
                "body": (pr.get("body") or "")[:500],
                "author": pr["user"]["login"],
                "created": pr["created_at"][:10],
                "reviews": len(reviews),
                "changed_files": pr.get("changed_files", 0),
            }
            data["prs"].append(item)

    # Releases
    log("Fetching releases...")
    releases = gh_api(f"repos/{REPO}/releases?per_page=1")
    if releases and len(releases) > 0:
        data["latest_release"] = releases[0].get("tag_name", "none")
        tag = releases[0].get("tag_name", "")
        if tag:
            try:
                result = subprocess.run(
                    ["gh", "api", f"repos/{REPO}/compare/{tag}...main"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    compare = json.loads(result.stdout)
                    data["unreleased_commits"] = compare.get("ahead_by", 0)
            except Exception:
                pass

    return data


def analyze_via_cli(data):
    """Pass collected data to CLI for analysis and suggested responses."""
    data_json = json.dumps(data, ensure_ascii=False)

    prompt = f"""Analyze this GitHub repository status for NEXO Brain (wazionapps/nexo).

DATA:
{data_json}

Generate a status report with:
1. SUMMARY: counts of open issues, PRs, unresponded items
2. For each UNRESPONDED ISSUE (comments=0): suggest a response in English (technical, helpful, friendly)
3. For each PR: brief assessment (looks good / needs changes / needs review)
4. RELEASE STATUS: if >10 unreleased commits, recommend a release
5. ALERTS: anything needing immediate attention (stale issues >7d, etc.)

Return as JSON:
{{
  "summary": {{
    "open_issues": N,
    "unresponded_issues": N,
    "stale_issues": N,
    "open_prs": N,
    "unreviewed_prs": N,
    "unreleased_commits": N
  }},
  "issue_responses": [
    {{"number": N, "suggested_response": "text"}},
    ...
  ],
  "pr_assessments": [
    {{"number": N, "assessment": "text"}},
    ...
  ],
  "alerts": ["alert1", ...],
  "release_recommendation": "text or null"
}}"""

    auth_check = subprocess.run(
        [str(CLAUDE_CLI), "-p", "Reply with exactly: ok", "--bare", "--output-format", "text", "--model", "haiku"],
        capture_output=True, text=True, timeout=15
    )
    if auth_check.returncode != 0:
        # CLI not authenticated, skip gracefully
        return ""

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE", None)

    result = subprocess.run(
        [str(CLAUDE_CLI), "-p", prompt,
         "--model", "opus", "--output-format", "text", "--bare",
         "--output-format", "text",
         "--allowedTools", "Read,Write,Edit,Glob,Grep"],
        capture_output=True, text=True, timeout=180, env=env
    )

    if result.returncode != 0:
        log(f"CLI analysis failed: {result.stderr[:200]}")
        return None

    output = result.stdout.strip()
    start = output.find("{")
    end = output.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(output[start:end])
    return None


def main():
    log("=== NEXO GitHub Monitor ===")

    # Step 1: Collect data (mechanical)
    data = collect_data()

    # Step 2: Analyze via CLI (intelligent)
    log("Analyzing via CLI...")
    analysis = analyze_via_cli(data)

    # Build status file
    status = {
        "timestamp": data["timestamp"],
        "repo": REPO,
        "issues": {
            "open": len(data["issues"]),
            "unresponded": sum(1 for i in data["issues"] if i["comments"] == 0),
            "stale": sum(1 for i in data["issues"]
                        if i["comments"] == 0 and i["created"] < (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')),
            "items": [{"number": i["number"], "title": i["title"], "created": i["created"],
                        "comments": i["comments"], "labels": i["labels"]} for i in data["issues"]],
        },
        "prs": {
            "open": len(data["prs"]),
            "unreviewed": sum(1 for p in data["prs"] if p["reviews"] == 0),
            "items": [{"number": p["number"], "title": p["title"], "author": p["author"],
                        "created": p["created"], "reviews": p["reviews"]} for p in data["prs"]],
        },
        "releases": {
            "latest": data["latest_release"] or "none",
            "unreleased_commits": data["unreleased_commits"],
        },
        "alerts": [],
    }

    # Merge CLI analysis
    if analysis:
        status["alerts"] = analysis.get("alerts", [])
        status["issue_responses"] = analysis.get("issue_responses", [])
        status["pr_assessments"] = analysis.get("pr_assessments", [])
        status["release_recommendation"] = analysis.get("release_recommendation")
    else:
        # Fallback alerts without CLI
        if status["issues"]["unresponded"] > 0:
            status["alerts"].append(f"{status['issues']['unresponded']} issues without response")
        if status["issues"]["stale"] > 0:
            status["alerts"].append(f"{status['issues']['stale']} stale issues (>7d)")
        if status["prs"]["unreviewed"] > 0:
            status["alerts"].append(f"{status['prs']['unreviewed']} PRs awaiting review")
        if data["unreleased_commits"] > 10:
            status["alerts"].append(f"{data['unreleased_commits']} unreleased commits")

    # Log summary
    log(f"Issues: {status['issues']['open']} open ({status['issues']['unresponded']} unresponded)")
    log(f"PRs: {status['prs']['open']} open ({status['prs']['unreviewed']} unreviewed)")
    log(f"Latest release: {status['releases']['latest']}")
    if status["alerts"]:
        log(f"ALERTS: {'; '.join(status['alerts'])}")

    # Save
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2))
    log(f"Status saved to {STATUS_FILE}")
    log("=== Done ===")


if __name__ == "__main__":
    main()
