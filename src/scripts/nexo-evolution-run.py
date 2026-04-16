#!/usr/bin/env python3
"""
NEXO Evolution — Standalone weekly runner with real execution.
Cron: 0 3 * * 0  (Sundays 3:00 AM)

Runs independently of Cortex. Calls the configured NEXO automation backend
to analyze the past week and generate improvement proposals.

AUTO proposals are executed: snapshot → apply → validate → commit/rollback.
PROPOSE proposals are logged for the user's review.
"""

import json
import os
import py_compile
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path


try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))

# ── Paths ────────────────────────────────────────────────────────────────
CLAUDE_DIR = NEXO_HOME
NEXO_DB = CLAUDE_DIR / "data" / "nexo.db"
LOG_DIR = CLAUDE_DIR / "logs"
SNAPSHOTS_DIR = CLAUDE_DIR / "snapshots"
SANDBOX_DIR = CLAUDE_DIR / "sandbox" / "workspace"
MAX_CONSECUTIVE_FAILURES = 3
MAX_SNAPSHOTS = 8

# ── Immutable files — split by risk tier ────────────────────────────────
# These remain locked even in managed mode because they can break bootstrap,
# persistence, or the evolution engine itself.
GLOBAL_IMMUTABLE_FILES = {
    "db.py",
    "server.py",
    "plugin_loader.py",
    "nexo-watchdog.sh",
    "cortex-wrapper.py",
    "CLAUDE.md",
    "AGENTS.md",
    "personality.md",
    "user-profile.md",
    "evolution_cycle.py",
    "storage_router.py",
}

# Managed mode may autoevolve behavior/tooling modules, but auto/review keep
# these guarded to stay conservative for public installs.
STANDARD_MODE_IMMUTABLE_FILES = {
    "cognitive.py",
    "knowledge_graph.py",
    "tools_sessions.py",
    "tools_coordination.py",
    "tools_reminders.py",
    "tools_reminders_crud.py",
    "tools_learnings.py",
    "tools_credentials.py",
    "tools_task_history.py",
    "tools_menu.py",
}


def _repo_root() -> Path | None:
    candidate = NEXO_CODE.parent
    if (candidate / "package.json").exists():
        return candidate
    return None


def _public_safe_prefixes() -> list[str]:
    return [
        str(CLAUDE_DIR / "scripts") + "/",
        str(CLAUDE_DIR / "plugins") + "/",
        str(CLAUDE_DIR / "skills") + "/",
        str(CLAUDE_DIR / "skills-runtime") + "/",
    ]


def _managed_safe_prefixes() -> list[str]:
    prefixes = [
        str(CLAUDE_DIR / "scripts") + "/",
        str(CLAUDE_DIR / "plugins") + "/",
        str(CLAUDE_DIR / "brain") + "/",
        str(CLAUDE_DIR / "coordination") + "/",
        str(CLAUDE_DIR / "logs") + "/",
        str(CLAUDE_DIR / "skills") + "/",
        str(CLAUDE_DIR / "skills-core") + "/",
        str(CLAUDE_DIR / "skills-runtime") + "/",
        str(NEXO_CODE) + "/",
    ]
    repo_root = _repo_root()
    if repo_root:
        for rel in ("bin", "docs", "templates", "tests"):
            prefixes.append(str(repo_root / rel) + "/")
    return prefixes


def _normalize_mode(mode: str) -> str:
    value = str(mode or "auto").strip().lower()
    aliases = {
        "owner": "managed",
        "core": "managed",
        "hybrid": "managed",
        "manual": "review",
        "public": "public_core",
        "contributor": "public_core",
        "draft_prs": "public_core",
    }
    return aliases.get(value, value if value in {"auto", "review", "managed", "public_core"} else "auto")


def _immutable_files_for_mode(mode: str) -> set[str]:
    normalized = _normalize_mode(mode)
    if normalized == "managed":
        return set(GLOBAL_IMMUTABLE_FILES)
    return set(GLOBAL_IMMUTABLE_FILES) | set(STANDARD_MODE_IMMUTABLE_FILES)

# ── Automation backend pathing ───────────────────────────────────────────
def _resolve_claude_cli() -> Path:
    """Find claude CLI: saved path > PATH > common locations."""
    import shutil as _shutil
    saved = NEXO_HOME / "config" / "claude-cli-path"
    if saved.exists():
        p = Path(saved.read_text().strip())
        if p.exists():
            return p
    found = _shutil.which("claude")
    if found:
        return Path(found)
    for candidate in [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]:
        if candidate.exists():
            return candidate
    return Path.home() / ".local" / "bin" / "claude"

CLAUDE_CLI = _resolve_claude_cli()
PUBLIC_ALLOWED_PREFIXES = (
    "src/",
    "bin/",
    "tests/",
    "templates/",
    "hooks/",
    "migrations/",
    ".claude-plugin/",
)

# ── Logging ──────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "evolution.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Import from evolution_cycle.py (lives in NEXO_CODE, i.e. src/) ──────
sys.path.insert(0, str(NEXO_CODE))
from agent_runner import probe_automation_backend, run_automation_prompt
from constants import AUTOMATION_SUBPROCESS_TIMEOUT
from evolution_cycle import (
    load_objective, save_objective, get_week_data, build_evolution_prompt,
    dry_run_restore_test, max_auto_changes, create_snapshot,
    build_public_contribution_prompt, build_public_pr_review_prompt,
)
from public_contribution import (
    CONTRIB_ARTIFACTS_DIR,
    CONTRIB_REPO_DIR,
    CONTRIB_WORKTREES_DIR,
    UPSTREAM_REPO,
    can_run_public_contribution,
    load_public_contribution_config,
    mark_active_pr,
    mark_public_contribution_result,
    STATUS_PAUSED_OPEN_PR,
)
from public_evolution_queue import (
    list_pending_public_port_candidates,
    update_public_port_candidate,
)


# ── Consecutive failure tracking ─────────────────────────────────────────
def get_consecutive_failures() -> int:
    obj = load_objective()
    return obj.get("consecutive_failures", 0)


def set_consecutive_failures(count: int):
    obj = load_objective()
    obj["consecutive_failures"] = count
    save_objective(obj)


# ── Automation backend call ──────────────────────────────────────────────
CLI_TIMEOUT = AUTOMATION_SUBPROCESS_TIMEOUT


def verify_claude_cli() -> bool:
    """Check the configured automation backend is available and authenticated."""
    return bool(probe_automation_backend(timeout=30).get("ok"))


def call_claude_cli(prompt: str) -> str:
    """Call the configured automation backend for the managed evolution prompt."""
    result = run_automation_prompt(
        prompt,
        model=_USER_MODEL,
        timeout=CLI_TIMEOUT,
        output_format="text",
        allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


def call_public_claude_cli(prompt: str, *, cwd: Path) -> str:
    """Run the configured automation backend in an isolated public repo checkout."""
    result = run_automation_prompt(
        prompt,
        cwd=cwd,
        env={"NEXO_PUBLIC_CONTRIBUTION": "1"},
        model=_USER_MODEL,
        timeout=CLI_TIMEOUT,
        output_format="text",
        allowed_tools="Read,Write,Edit,Glob,Grep,Bash",
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout


def _git(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _gh(*args: str, cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _branch_slug(text: str) -> str:
    raw = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-")
    return raw[:48] or "proposal"


def _ensure_public_repo_cache(config: dict) -> None:
    CONTRIB_REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not (CONTRIB_REPO_DIR / ".git").exists():
        clone = _git(CONTRIB_REPO_DIR.parent, "clone", f"https://github.com/{config['upstream_repo']}.git", str(CONTRIB_REPO_DIR), timeout=180)
        if clone.returncode != 0:
            raise RuntimeError(clone.stderr.strip() or clone.stdout.strip() or "git clone failed")
    fetch = _git(CONTRIB_REPO_DIR, "fetch", "origin", timeout=120)
    if fetch.returncode != 0:
        raise RuntimeError(fetch.stderr.strip() or fetch.stdout.strip() or "git fetch failed")

    remote_url = f"https://github.com/{config['fork_repo']}.git"
    current = _git(CONTRIB_REPO_DIR, "remote", "get-url", "fork", timeout=10)
    if current.returncode != 0:
        add = _git(CONTRIB_REPO_DIR, "remote", "add", "fork", remote_url, timeout=10)
        if add.returncode != 0:
            raise RuntimeError(add.stderr.strip() or add.stdout.strip() or "git remote add fork failed")
    elif current.stdout.strip() != remote_url:
        set_url = _git(CONTRIB_REPO_DIR, "remote", "set-url", "fork", remote_url, timeout=10)
        if set_url.returncode != 0:
            raise RuntimeError(set_url.stderr.strip() or set_url.stdout.strip() or "git remote set-url failed")


def _prepare_public_worktree(config: dict, title_hint: str = "evolution") -> tuple[Path, str]:
    _ensure_public_repo_cache(config)
    CONTRIB_WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    branch_name = f"contrib/{config['machine_id']}/{timestamp}-{_branch_slug(title_hint)}"
    worktree_dir = CONTRIB_WORKTREES_DIR / f"{timestamp}-{_branch_slug(title_hint)}"
    add = _git(CONTRIB_REPO_DIR, "worktree", "add", "--detach", str(worktree_dir), "origin/main", timeout=120)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or add.stdout.strip() or "git worktree add failed")
    return worktree_dir, branch_name


def _prime_public_git_identity(worktree_dir: Path, config: dict) -> None:
    github_user = str(config.get("github_user") or "nexo-public-evolution").strip() or "nexo-public-evolution"
    email = f"{github_user}@users.noreply.github.com"
    name = f"{github_user} via NEXO Public Evolution"
    for key, value in (("user.name", name), ("user.email", email)):
        result = _git(worktree_dir, "config", key, value, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git config {key} failed")


def _remove_public_worktree(worktree_dir: Path) -> None:
    if not worktree_dir.exists():
        return
    _git(CONTRIB_REPO_DIR, "worktree", "remove", str(worktree_dir), "--force", timeout=60)


def _parse_summary_json(text: str) -> dict:
    payload = text.strip()
    if "```json" in payload:
        payload = payload.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in payload:
        payload = payload.split("```", 1)[1].split("```", 1)[0]
    try:
        summary = json.loads(payload.strip())
        if isinstance(summary, dict):
            return summary
    except Exception:
        pass
    return {}


def _changed_public_files(worktree_dir: Path) -> list[str]:
    result = _git(worktree_dir, "status", "--porcelain", timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    changed: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        if path_text:
            changed.append(path_text)
    return changed


def _is_allowed_public_path(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in PUBLIC_ALLOWED_PREFIXES)


def _sanitize_public_diff(worktree_dir: Path, changed_files: list[str]) -> tuple[bool, str]:
    if not changed_files:
        return False, "No repository changes were produced."
    for rel_path in changed_files:
        if not _is_allowed_public_path(rel_path):
            return False, f"Changed path is not allowed for public contribution: {rel_path}"

    diff = _git(worktree_dir, "diff", "--no-ext-diff", "--", *changed_files, timeout=60)
    if diff.returncode != 0:
        return False, diff.stderr.strip() or diff.stdout.strip() or "git diff failed"
    diff_text = diff.stdout
    private_markers = [
        str(Path.home()),
        str(NEXO_HOME),
        "CLAUDE.md",
        "AGENTS.md",
        ".nexo/",
        ".codex/",
    ]
    for marker in private_markers:
        if marker and marker in diff_text:
            return False, f"Sanitization blocked private marker in diff: {marker}"
    private_path_patterns = [
        re.compile(r"/Users/[^/\s\"']+/"),
        re.compile(r"/home/[^/\s\"']+/"),
    ]
    for pattern in private_path_patterns:
        match = pattern.search(diff_text)
        if match:
            return False, f"Sanitization blocked private path in diff: {match.group(0)}"
    return True, ""


def _run_public_validation(worktree_dir: Path, changed_files: list[str]) -> list[str]:
    validations: list[str] = []
    py_files = [str(worktree_dir / rel_path) for rel_path in changed_files if rel_path.endswith(".py")]
    if py_files:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", *py_files],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "py_compile failed")
        validations.append("python3 -m py_compile " + " ".join(changed_files))

    js_files = [str(worktree_dir / rel_path) for rel_path in changed_files if rel_path.endswith(".js")]
    for js_file in js_files:
        result = subprocess.run(
            ["node", "--check", js_file],
            cwd=str(worktree_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"node --check failed for {js_file}")
    if js_files:
        validations.append("node --check " + " ".join(changed_files))

    tests = subprocess.run(
        ["pytest", "-q", "tests"],
        cwd=str(worktree_dir),
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    if tests.returncode != 0:
        raise RuntimeError(tests.stderr.strip() or tests.stdout.strip() or "pytest failed")
    validations.append("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests")
    return validations


def _write_public_artifacts(worktree_dir: Path, branch_name: str, summary: dict) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_dir = CONTRIB_ARTIFACTS_DIR / timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    diff = _git(worktree_dir, "diff", "--no-ext-diff", "origin/main...HEAD", timeout=60)
    patch_text = diff.stdout if diff.returncode == 0 else ""
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    (artifact_dir / "branch.txt").write_text(branch_name + "\n")
    (artifact_dir / "diff.patch").write_text(patch_text)
    return artifact_dir


def _review_state(review: dict) -> str:
    return str(review.get("state") or review.get("reviewState") or "").strip().upper()


def _review_author(review: dict) -> str:
    author = review.get("author") or {}
    if isinstance(author, dict):
        return str(author.get("login") or "").strip().lower()
    return ""


def _is_public_evolution_pr(details: dict) -> bool:
    body = str(details.get("body") or "")
    return "Source: automated public core evolution from an opt-in machine." in body


def _review_already_left_by_user(details: dict, login: str) -> bool:
    login = str(login or "").strip().lower()
    if not login:
        return False
    for review in details.get("reviews") or []:
        if _review_author(review) == login and _review_state(review) in {"APPROVED", "COMMENTED", "CHANGES_REQUESTED"}:
            return True
    return False


def _candidate_paths(details: dict) -> list[str]:
    paths = []
    for item in details.get("files") or []:
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("name") or "").strip()
            if path:
                paths.append(path)
    return paths


def _list_reviewable_public_prs(config: dict, limit: int = 3) -> list[dict]:
    result = _gh(
        "pr",
        "list",
        "--repo",
        config["upstream_repo"],
        "--state",
        "open",
        "--json",
        "number,title,url,isDraft,author",
        "--limit",
        str(max(1, limit * 4)),
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr list failed")

    github_user = str(config.get("github_user") or "").strip().lower()
    active_pr_number = config.get("active_pr_number")
    candidates: list[dict] = []
    for item in json.loads(result.stdout or "[]"):
        if not item.get("isDraft", False):
            continue
        number = int(item.get("number") or 0)
        if not number or number == active_pr_number:
            continue
        author = item.get("author") or {}
        author_login = str(author.get("login") or "").strip().lower()
        if github_user and author_login == github_user:
            continue

        details_result = _gh(
            "pr",
            "view",
            str(number),
            "--repo",
            config["upstream_repo"],
            "--json",
            "number,title,body,url,isDraft,author,reviews,files",
            timeout=30,
        )
        if details_result.returncode != 0:
            continue
        details = json.loads(details_result.stdout or "{}")
        if not details.get("isDraft", False):
            continue
        if not _is_public_evolution_pr(details):
            continue
        if _review_already_left_by_user(details, github_user):
            continue
        paths = _candidate_paths(details)
        if not paths or any(not _is_allowed_public_path(path) for path in paths):
            continue

        diff_result = _gh(
            "pr",
            "diff",
            str(number),
            "--repo",
            config["upstream_repo"],
            timeout=60,
        )
        if diff_result.returncode != 0:
            continue
        details["files_changed"] = paths
        details["diff_text"] = diff_result.stdout or ""
        candidates.append(details)
        if len(candidates) >= limit:
            break
    return candidates


_DEDUP_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "after", "before", "public",
    "core", "nexo", "fix", "feat", "chore", "docs", "tests", "runtime", "system",
}


def _proposal_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) >= 3 and token not in _DEDUP_STOPWORDS
    }


def _public_pr_duplicate_candidate(config: dict, *, title: str, changed_files: list[str]) -> dict | None:
    try:
        candidates = _list_reviewable_public_prs(config, limit=12)
    except Exception:
        return None
    wanted_files = {str(path).strip().lower() for path in (changed_files or []) if str(path).strip()}
    wanted_tokens = _proposal_tokens(title)
    best_match = None
    best_score = 0.0
    for candidate in candidates:
        candidate_files = {
            str(path).strip().lower() for path in (candidate.get("files_changed") or []) if str(path).strip()
        }
        shared_files = wanted_files & candidate_files
        candidate_tokens = _proposal_tokens(str(candidate.get("title") or ""))
        shared_tokens = wanted_tokens & candidate_tokens
        token_score = 0.0
        if wanted_tokens and candidate_tokens:
            token_score = len(shared_tokens) / max(1, min(len(wanted_tokens), len(candidate_tokens)))
        score = 0.0
        if shared_files and token_score >= 0.34:
            score = 1.0
        elif shared_files:
            score = 0.75
        elif token_score >= 0.8:
            score = 0.7
        if score > best_score:
            best_score = score
            best_match = {
                "number": candidate.get("number"),
                "title": candidate.get("title"),
                "url": candidate.get("url"),
                "score": round(score, 2),
                "shared_files": sorted(shared_files),
                "shared_tokens": sorted(shared_tokens),
            }
    return best_match if best_score >= 0.75 else None


def _parse_public_review_json(text: str) -> dict:
    payload = text.strip()
    if "```json" in payload:
        payload = payload.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in payload:
        payload = payload.split("```", 1)[1].split("```", 1)[0]
    try:
        data = json.loads(payload.strip())
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _submit_public_pr_review(config: dict, pr_number: int, decision: str, body: str) -> str:
    clean_decision = str(decision or "").strip().lower()
    clean_body = str(body or "").strip()
    if clean_decision == "approve":
        result = _gh(
            "pr",
            "review",
            str(pr_number),
            "--repo",
            config["upstream_repo"],
            "--approve",
            "--body",
            clean_body or "Scoped public-core change looks correct from automated peer review.",
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr review --approve failed")
        return "approved_review"
    if clean_decision == "comment":
        result = _gh(
            "pr",
            "review",
            str(pr_number),
            "--repo",
            config["upstream_repo"],
            "--comment",
            "--body",
            clean_body or "Automated peer review left a note but did not approve.",
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr review --comment failed")
        return "commented_review"
    return "review_skipped"


def _write_public_review_artifacts(pr_number: int, candidate: dict, review: dict) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_dir = CONTRIB_ARTIFACTS_DIR / f"review-{timestamp}-pr{pr_number}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "candidate.json").write_text(json.dumps(candidate, indent=2, ensure_ascii=False) + "\n")
    (artifact_dir / "review.json").write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    (artifact_dir / "diff.patch").write_text(str(candidate.get("diff_text") or ""))
    return artifact_dir


def run_public_pr_validation_cycle(*, objective: dict, cycle_num: int, config: dict | None = None) -> int:
    config = config or load_public_contribution_config()
    if not verify_claude_cli():
        log("Automation backend not available or not authenticated. Skipping peer PR validation.")
        mark_public_contribution_result(result="skipped:peer_review_cli_unavailable", config=config)
        return 0

    _ensure_public_repo_cache(config)
    candidates = _list_reviewable_public_prs(config, limit=3)
    if not candidates:
        log("No reviewable peer public-evolution PRs found.")
        mark_public_contribution_result(result="skipped:no_peer_prs", config=config)
        return 0

    repo_root = str(CONTRIB_REPO_DIR if CONTRIB_REPO_DIR.exists() else Path.cwd())
    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    reviewed = 0
    try:
        for candidate in candidates:
            pr_number = int(candidate.get("number") or 0)
            prompt = build_public_pr_review_prompt(
                pr_number=pr_number,
                title=str(candidate.get("title") or "").strip(),
                author=str((candidate.get("author") or {}).get("login") or "").strip(),
                url=str(candidate.get("url") or "").strip(),
                body=str(candidate.get("body") or ""),
                files=candidate.get("files_changed") or [],
                diff_text=str(candidate.get("diff_text") or ""),
            )
            raw_review = call_public_claude_cli(prompt, cwd=Path(repo_root))
            review = _parse_public_review_json(raw_review)
            decision = str(review.get("decision") or "skip").strip().lower()
            review_status = _submit_public_pr_review(config, pr_number, decision, str(review.get("body") or ""))
            artifact_dir = _write_public_review_artifacts(pr_number, candidate, review)
            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, reasoning, status, files_changed, test_result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle_num,
                    "public_core",
                    f"Review PR #{pr_number}: {str(candidate.get('title') or '').strip()}",
                    "public_review",
                    str(review.get("summary") or "Peer PR validation").strip(),
                    review_status,
                    json.dumps(candidate.get("files_changed") or []),
                    json.dumps(
                        {
                            "pr_url": candidate.get("url"),
                            "decision": decision,
                            "artifact_dir": str(artifact_dir),
                        }
                    ),
                ),
            )
            conn.commit()
            reviewed += 1

        if reviewed:
            objective["last_evolution"] = str(date.today())
            objective["total_evolutions"] = cycle_num
            objective.setdefault("history", []).insert(0, {
                "cycle": cycle_num,
                "date": str(date.today()),
                "mode": "public_core_review",
                "proposals": 0,
                "auto_count": 0,
                "auto_applied": 0,
                "analysis": f"Reviewed {reviewed} peer public-evolution PR(s).",
            })
            objective["history"] = objective["history"][:12]
            save_objective(objective)
            mark_public_contribution_result(result=f"peer_reviewed:{reviewed}", config=config)
        return reviewed
    finally:
        conn.close()


def _create_draft_pr(worktree_dir: Path, config: dict, branch_name: str, summary: dict) -> tuple[str, int | None]:
    title = str(summary.get("title") or "chore: public evolution contribution").strip()
    body_lines = [
        summary.get("problem", "Problem: see diff."),
        "",
        "Summary:",
        str(summary.get("summary") or "See diff."),
        "",
        "Tests:",
    ]
    tests = summary.get("tests") or []
    if isinstance(tests, list) and tests:
        body_lines.extend(f"- {item}" for item in tests)
    else:
        body_lines.append("- See CI / local validation")
    risks = summary.get("risks") or []
    if isinstance(risks, list) and risks:
        body_lines.extend(["", "Risks:"])
        body_lines.extend(f"- {item}" for item in risks)
    body_lines.extend(["", "Source: automated public core evolution from an opt-in machine."])
    body_file = worktree_dir / ".nexo-public-pr-body.md"
    body_file.write_text("\n".join(body_lines) + "\n")
    head = f"{config['github_user']}:{branch_name}"
    result = _gh(
        "pr",
        "create",
        "--repo",
        config["upstream_repo"],
        "--head",
        head,
        "--base",
        "main",
        "--title",
        title,
        "--body-file",
        str(body_file),
        "--draft",
        cwd=worktree_dir,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr create failed")
    pr_url = (result.stdout or "").strip().splitlines()[-1].strip()
    match = re.search(r"/pull/(\d+)", pr_url)
    pr_number = int(match.group(1)) if match else None
    return pr_url, pr_number


def run_public_contribution_cycle(*, objective: dict, cycle_num: int) -> None:
    config = load_public_contribution_config()
    ready, reason, config = can_run_public_contribution(config)
    if not ready:
        if config.get("status") == STATUS_PAUSED_OPEN_PR:
            log(f"Public core contribution paused: {reason}. Switching to peer PR validation.")
            reviewed = run_public_pr_validation_cycle(objective=objective, cycle_num=cycle_num, config=config)
            if reviewed:
                log(f"Peer public PR validation complete: reviewed {reviewed} PR(s).")
            return
        log(f"Public core contribution paused: {reason}")
        mark_public_contribution_result(result=f"skipped:{reason}", config=config)
        return

    if not verify_claude_cli():
        log("Automation backend not available or not authenticated. Skipping public contribution run.")
        mark_public_contribution_result(result="skipped:claude_cli_unavailable", config=config)
        return

    worktree_dir: Path | None = None
    branch_name = ""
    summary: dict = {}
    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    queued_candidate: dict | None = None
    try:
        pending_candidates = list_pending_public_port_candidates(conn, limit=1)
        if pending_candidates:
            queued_candidate = pending_candidates[0]
        worktree_dir, branch_name = _prepare_public_worktree(config, title_hint="public-core")
        _prime_public_git_identity(worktree_dir, config)
        prompt = build_public_contribution_prompt(
            repo_root=str(worktree_dir),
            cycle_number=cycle_num,
            queued_candidate=queued_candidate,
        )
        raw_response = call_public_claude_cli(prompt, cwd=worktree_dir)
        summary = _parse_summary_json(raw_response)
        changed_files = _changed_public_files(worktree_dir)
        ok, reason = _sanitize_public_diff(worktree_dir, changed_files)
        if not ok:
            raise RuntimeError(reason)

        tests_run = _run_public_validation(worktree_dir, changed_files)
        existing_tests = summary.get("tests")
        summary["tests"] = existing_tests if isinstance(existing_tests, list) and existing_tests else tests_run
        commit_title = str(summary.get("title") or "chore: public evolution contribution").strip()
        duplicate = _public_pr_duplicate_candidate(config, title=commit_title, changed_files=changed_files)
        if duplicate:
            artifact_dir = _write_public_artifacts(
                worktree_dir,
                branch_name,
                {
                    **summary,
                    "duplicate_of": duplicate,
                    "tests": summary.get("tests", []),
                    "changed_files": changed_files,
                },
            )
            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, reasoning, status, files_changed, test_result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle_num,
                    "public_core",
                    commit_title,
                    "draft_pr_dedup",
                    f"Duplicate of open opt-in public PR #{duplicate.get('number')}: {duplicate.get('title')}",
                    "skipped_duplicate_existing_pr",
                    json.dumps(changed_files),
                    json.dumps({"duplicate_of": duplicate, "artifact_dir": str(artifact_dir)}),
                ),
            )
            conn.commit()
            if queued_candidate:
                update_public_port_candidate(
                    conn,
                    queued_candidate["id"],
                    status="skipped_duplicate_existing_pr",
                    metadata_patch={"duplicate_of": duplicate},
                )
                conn.commit()
            mark_public_contribution_result(
                result=f"skipped:duplicate_pr:{duplicate.get('number')}",
                config=config,
            )
            log(
                "Public core contribution deduplicated against existing opt-in PR "
                f"#{duplicate.get('number')} ({duplicate.get('url')})."
            )
            return

        add = _git(worktree_dir, "add", "--", *changed_files, timeout=60)
        if add.returncode != 0:
            raise RuntimeError(add.stderr.strip() or add.stdout.strip() or "git add failed")
        commit = _git(worktree_dir, "commit", "-m", commit_title, timeout=120)
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
        push = _git(worktree_dir, "push", "fork", f"HEAD:refs/heads/{branch_name}", "--force-with-lease", timeout=180)
        if push.returncode != 0:
            raise RuntimeError(push.stderr.strip() or push.stdout.strip() or "git push failed")

        pr_url, pr_number = _create_draft_pr(worktree_dir, config, branch_name, summary)
        artifact_dir = _write_public_artifacts(worktree_dir, branch_name, summary)
        config = mark_active_pr(pr_url=pr_url, pr_number=pr_number, branch=branch_name, config=config)

        conn.execute(
            "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, reasoning, status, files_changed, test_result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cycle_num,
                "public_core",
                commit_title,
                "draft_pr",
                summary.get("problem", "Public core contribution"),
                "draft_pr_created",
                json.dumps(changed_files),
                json.dumps({"tests": summary.get("tests", []), "pr_url": pr_url, "artifact_dir": str(artifact_dir)}),
            ),
        )
        conn.commit()
        if queued_candidate:
            update_public_port_candidate(
                conn,
                queued_candidate["id"],
                status="draft_pr_created",
                metadata_patch={
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "branch": branch_name,
                    "ported_via_cycle": cycle_num,
                },
            )
            conn.commit()

        objective["last_evolution"] = str(date.today())
        objective["total_evolutions"] = cycle_num
        objective["total_proposals_made"] = objective.get("total_proposals_made", 0) + 1
        objective.setdefault("history", []).insert(0, {
            "cycle": cycle_num,
            "date": str(date.today()),
            "mode": "public_core",
            "proposals": 1,
            "auto_count": 0,
            "auto_applied": 0,
            "analysis": (summary.get("summary") or commit_title)[:200],
            "pr_url": pr_url,
        })
        objective["history"] = objective["history"][:12]
        save_objective(objective)
        mark_public_contribution_result(result=f"draft_pr_created:{pr_url}", config=config)
        log(f"Public core contribution complete: Draft PR created at {pr_url}")
    except Exception as exc:
        mark_public_contribution_result(result=f"failed:{exc}", config=config)
        raise
    finally:
        conn.close()
        if worktree_dir is not None:
            _remove_public_worktree(worktree_dir)


# ── File safety validation ───────────────────────────────────────────────
def is_safe_path(filepath: str, mode: str = "auto") -> bool:
    """Check if a file path is within safe zones and not immutable.
    mode='auto' (public): restricted to personal automation surfaces.
    mode='managed' (owner): broader repo/core surfaces with rollback.
    mode='review': broader zones for proposal validation, but no execution.
    """
    expanded = str(Path(filepath).expanduser().resolve())
    filename = Path(expanded).name
    mode = _normalize_mode(mode)

    if filename in _immutable_files_for_mode(mode):
        return False

    prefixes = _managed_safe_prefixes() if mode in {"managed", "review"} else _public_safe_prefixes()
    for prefix in prefixes:
        resolved_prefix = str(Path(prefix).expanduser().resolve())
        if expanded.startswith(resolved_prefix):
            return True

    return False


def validate_syntax(filepath: str) -> tuple[bool, str]:
    """Basic syntax validation for known file types."""
    path = Path(filepath)
    ext = path.suffix

    if ext == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
            return True, "Python syntax OK"
        except Exception as e:
            return False, f"Validation error: {e}"

    elif ext == ".sh":
        try:
            result = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True, "Bash syntax OK"
            return False, f"Bash syntax error: {result.stderr[:200]}"
        except Exception as e:
            return False, f"Validation error: {e}"

    elif ext == ".json":
        try:
            json.loads(Path(filepath).read_text())
            return True, "JSON valid"
        except Exception as e:
            return False, f"JSON error: {e}"

    elif ext == ".md":
        return True, "Markdown (no validation needed)"

    return True, f"No validator for {ext} (accepted)"


# ── Apply a single change operation ──────────────────────────────────────
def apply_change(change: dict, mode: str = "auto") -> tuple[bool, str]:
    """Apply a single file change operation. Returns (success, message)."""
    filepath = str(Path(change["file"]).expanduser())
    operation = change.get("operation", "")
    content = change.get("content", "")

    if not is_safe_path(filepath, mode=mode):
        return False, f"BLOCKED: {filepath} is outside safe zones or immutable"

    try:
        if operation == "create":
            if Path(filepath).exists():
                return False, f"BLOCKED: {filepath} already exists (create requires new file)"
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_text(content)
            # Make scripts executable
            if filepath.endswith(".sh") or filepath.endswith(".py"):
                os.chmod(filepath, 0o755)
            return True, f"Created {filepath}"

        elif operation == "replace":
            search = change.get("search", "")
            if not search:
                return False, "BLOCKED: replace operation requires 'search' field"
            if not Path(filepath).exists():
                return False, f"BLOCKED: {filepath} does not exist"
            original = Path(filepath).read_text()
            count = original.count(search)
            if count == 0:
                return False, f"BLOCKED: search text not found in {filepath}"
            if count > 1:
                return False, f"BLOCKED: search text matches {count} times (must be unique)"
            new_content = original.replace(search, content, 1)
            Path(filepath).write_text(new_content)
            return True, f"Replaced in {filepath}"

        elif operation == "append":
            if not Path(filepath).exists():
                return False, f"BLOCKED: {filepath} does not exist"
            with open(filepath, "a") as f:
                f.write(content)
            return True, f"Appended to {filepath}"

        else:
            return False, f"BLOCKED: unknown operation '{operation}'"

    except Exception as e:
        return False, f"ERROR: {e}"


# ── Execute AUTO proposals ───────────────────────────────────────────────
def execute_auto_proposal(proposal: dict, cycle_num: int, conn: sqlite3.Connection, mode: str = "auto") -> dict:
    """Execute an AUTO proposal with snapshot/apply/validate/rollback."""
    changes = proposal.get("changes", [])
    if not changes:
        return {"status": "skipped", "reason": "No changes array in proposal"}

    # Validate all paths first
    for change in changes:
        filepath = str(Path(change["file"]).expanduser())
        if not is_safe_path(filepath, mode=mode):
            return {"status": "blocked", "reason": f"Unsafe path: {filepath}"}

    # Collect files to snapshot (existing files only)
    files_to_backup = []
    for change in changes:
        filepath = str(Path(change["file"]).expanduser())
        if Path(filepath).exists():
            files_to_backup.append(filepath)

    # Create snapshot
    snapshot_ref = None
    if files_to_backup:
        snapshot_ref = create_snapshot(files_to_backup)
        log(f"  Snapshot created: {snapshot_ref}")

    # Apply changes
    applied_files = []
    all_results = []
    try:
        for change in changes:
            success, msg = apply_change(change, mode=mode)
            all_results.append(msg)
            log(f"    {msg}")
            if not success:
                raise RuntimeError(f"Change failed: {msg}")
            filepath = str(Path(change["file"]).expanduser())
            applied_files.append(filepath)

        # Validate all modified/created files
        for filepath in applied_files:
            valid, vmsg = validate_syntax(filepath)
            all_results.append(vmsg)
            log(f"    Validate: {vmsg}")
            if not valid:
                raise RuntimeError(f"Validation failed: {vmsg}")

        return {
            "status": "applied",
            "snapshot_ref": snapshot_ref,
            "files_changed": applied_files,
            "test_result": "; ".join(all_results),
        }

    except RuntimeError as e:
        # Rollback
        log(f"  ROLLBACK: {e}")
        if snapshot_ref:
            try:
                restore_script = CLAUDE_DIR / "scripts" / "nexo-snapshot-restore.sh"
                subprocess.run(
                    [str(restore_script), snapshot_ref],
                    capture_output=True, timeout=15, check=True
                )
                log(f"  Restored from snapshot {snapshot_ref}")
            except Exception as re:
                log(f"  CRITICAL: Restore failed: {re}")
        else:
            # Remove created files that didn't exist before
            for filepath in applied_files:
                if filepath not in files_to_backup:
                    Path(filepath).unlink(missing_ok=True)
                    log(f"  Removed created file: {filepath}")

        return {
            "status": "rolled_back",
            "snapshot_ref": snapshot_ref,
            "files_changed": [],
            "test_result": f"ROLLBACK: {e}; " + "; ".join(all_results),
        }


# ── Followups for managed/review modes ──────────────────────────────────
def _insert_followup(conn: sqlite3.Connection, followup_id: str, description: str,
                     verification: str, due_date: str | None = None):
    now_epoch = datetime.now().timestamp()
    conn.execute(
        "INSERT OR REPLACE INTO followups (id, description, date, status, verification, created_at, updated_at) "
        "VALUES (?, ?, ?, 'PENDING', ?, ?, ?)",
        (followup_id, description, due_date, verification, now_epoch, now_epoch)
    )
    conn.commit()


def _create_cycle_followup(conn: sqlite3.Connection, cycle_num: int,
                           items: list[dict], analysis: str, mode: str):
    """Create a followup summarizing pending proposals or owner review items."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    followup_id = f"NF-EVO-C{cycle_num}"

    public_items = [i for i in items if i.get("scope") == "public"]
    local_items = [i for i in items if i.get("scope") != "public"]

    title = "proposals to review" if mode == "review" else "items needing attention"
    lines = [f"Evolution Cycle #{cycle_num} — {len(items)} {title}."]
    lines.append(f"Analysis: {analysis[:200]}")
    lines.append("")

    if public_items:
        lines.append(f"FOR EVERYONE ({len(public_items)}):")
        for i, item in enumerate(public_items, 1):
            status = item.get("status", "proposed").upper()
            lines.append(f"  {i}. [{status}] [{item['dimension']}] {item['action'][:120]}")
            lines.append(f"     Why: {item['reasoning'][:100]}")
            if item.get("detail"):
                lines.append(f"     Detail: {item['detail'][:160]}")
        lines.append("")

    if local_items:
        lines.append(f"FOR YOU ONLY ({len(local_items)}):")
        for i, item in enumerate(local_items, 1):
            status = item.get("status", "proposed").upper()
            lines.append(f"  {i}. [{status}] [{item['dimension']}] {item['action'][:120]}")
            lines.append(f"     Why: {item['reasoning'][:100]}")
            if item.get("detail"):
                lines.append(f"     Detail: {item['detail'][:160]}")

    description = "\n".join(lines)

    try:
        _insert_followup(
            conn,
            followup_id,
            description,
            f"SELECT * FROM evolution_log WHERE cycle_number={cycle_num}",
            due_date=tomorrow,
        )
        log(f"  Followup {followup_id} created for {tomorrow}")
    except Exception as e:
        log(f"  WARN: Failed to create followup: {e}")


def _create_failure_followup(conn: sqlite3.Connection, cycle_num: int, log_id: int,
                             proposal: dict, result: dict):
    """Create an incident-style followup for a failed or blocked AUTO proposal."""
    followup_id = f"NF-EVO-L{log_id}"
    lines = [
        f"Evolution AUTO proposal failed in cycle #{cycle_num}.",
        f"Action: {proposal.get('action', '')[:200]}",
        f"Dimension: {proposal.get('dimension', 'other')}",
        f"Status: {result.get('status', 'failed')}",
        f"Reason: {(result.get('reason') or result.get('test_result') or 'unknown')[:400]}",
    ]
    snapshot_ref = result.get("snapshot_ref")
    if snapshot_ref:
        lines.append(f"Snapshot: {snapshot_ref}")
    description = "\n".join(lines)

    try:
        _insert_followup(
            conn,
            followup_id,
            description,
            f"SELECT * FROM evolution_log WHERE id={log_id}",
            due_date=(date.today() + timedelta(days=1)).isoformat(),
        )
        log(f"  Failure followup {followup_id} created")
    except Exception as e:
        log(f"  WARN: Failed to create failure followup: {e}")


# ── Apply user-approved proposals from prior cycles ─────────────────────
def _apply_accepted_proposals(
    conn: sqlite3.Connection,
    cycle_num: int,
    max_to_apply: int,
    evolution_mode: str,
) -> dict:
    """Apply evolution_log rows that the user marked as `accepted`.

    Reads up to `max_to_apply` rows where `status = 'accepted'` and
    `proposal_payload IS NOT NULL`, deserializes the original proposal dict,
    and runs each one through `execute_auto_proposal()` (same path as live
    AUTO proposals: snapshot, apply, validate, rollback on failure).

    Updates each row's status to one of: 'applied', 'rolled_back', 'blocked',
    'skipped'. Failed rows get an `NF-EVO-L<id>` followup so they remain
    visible after the cycle. The cycle continues even if individual rows
    fail — one bad proposal does not block the queue.

    Pre-m38 rows have NULL proposal_payload and are intentionally skipped:
    we cannot reconstruct their `changes` array.

    Returns: dict with attempted/applied/rolled_back/blocked/skipped/failed counts.
    """
    rows = conn.execute(
        "SELECT id, dimension, proposal, reasoning, proposal_payload "
        "FROM evolution_log WHERE status = 'accepted' AND proposal_payload IS NOT NULL "
        "ORDER BY id ASC LIMIT ?",
        (max(1, int(max_to_apply)),),
    ).fetchall()

    stats = {
        "attempted": 0,
        "applied": 0,
        "rolled_back": 0,
        "blocked": 0,
        "skipped": 0,
        "failed": 0,
    }

    for row in rows:
        log_id = row["id"]
        raw_payload = row["proposal_payload"]
        try:
            payload = json.loads(raw_payload)
        except Exception as e:
            log(f"  ACCEPTED #{log_id} skipped: invalid payload ({e})")
            conn.execute(
                "UPDATE evolution_log SET status = ?, test_result = ? WHERE id = ?",
                ("skipped", f"Invalid proposal_payload JSON: {e}", log_id),
            )
            stats["skipped"] += 1
            continue

        if not isinstance(payload, dict) or not payload.get("changes"):
            log(f"  ACCEPTED #{log_id} skipped: payload missing changes array")
            conn.execute(
                "UPDATE evolution_log SET status = ?, test_result = ? WHERE id = ?",
                ("skipped", "Payload missing or empty changes array", log_id),
            )
            stats["skipped"] += 1
            continue

        action = (payload.get("action") or row["proposal"] or "")[:80]
        log(f"  ACCEPTED #{log_id} applying: {action}")
        stats["attempted"] += 1

        try:
            result = execute_auto_proposal(payload, cycle_num, conn, mode=evolution_mode)
        except Exception as e:
            log(f"    FAILED execute_auto_proposal: {e}")
            conn.execute(
                "UPDATE evolution_log SET status = ?, test_result = ? WHERE id = ?",
                ("blocked", f"execute_auto_proposal raised: {e}", log_id),
            )
            stats["failed"] += 1
            try:
                _create_failure_followup(
                    conn, cycle_num, log_id, payload, {"status": "failed", "reason": str(e)}
                )
            except Exception:
                pass
            continue

        status = str(result.get("status") or "failed")
        update_sets = ["status = ?"]
        update_vals: list[object] = [status]
        if "test_result" in result:
            update_sets.append("test_result = ?")
            update_vals.append(str(result.get("test_result", ""))[:2000])
        if result.get("snapshot_ref"):
            update_sets.append("snapshot_ref = ?")
            update_vals.append(result["snapshot_ref"])
        if result.get("files_changed"):
            update_sets.append("files_changed = ?")
            update_vals.append(json.dumps(result["files_changed"]))
        update_vals.append(log_id)
        conn.execute(
            f"UPDATE evolution_log SET {', '.join(update_sets)} WHERE id = ?",
            update_vals,
        )

        if status == "applied":
            stats["applied"] += 1
            log(f"    APPLIED")
        elif status == "rolled_back":
            stats["rolled_back"] += 1
            log(f"    ROLLED BACK: {str(result.get('test_result', ''))[:100]}")
            try:
                _create_failure_followup(conn, cycle_num, log_id, payload, result)
            except Exception:
                pass
        elif status == "blocked":
            stats["blocked"] += 1
            log(f"    BLOCKED: {str(result.get('reason') or result.get('test_result', ''))[:100]}")
            try:
                _create_failure_followup(conn, cycle_num, log_id, payload, result)
            except Exception:
                pass
        elif status == "skipped":
            stats["skipped"] += 1
            log(f"    SKIPPED: {result.get('reason', '')}")
        else:
            stats["failed"] += 1
            log(f"    UNKNOWN STATUS: {status}")

    conn.commit()
    return stats


# ── Main run ─────────────────────────────────────────────────────────────
def run():
    log("=" * 60)
    log("NEXO Evolution cycle starting (standalone, v2 — real execution)")

    # Check objective
    objective = load_objective()
    if not objective:
        log("ERROR: No evolution-objective.json found")
        sys.exit(1)
    if not objective.get("evolution_enabled", True):
        log(f"Evolution DISABLED: {objective.get('disabled_reason', 'unknown')}")
        return

    # Circuit breaker: consecutive failures
    failures = get_consecutive_failures()
    if failures >= MAX_CONSECUTIVE_FAILURES:
        log(f"CIRCUIT BREAKER: {failures} consecutive failures. Disabling evolution.")
        objective["evolution_enabled"] = False
        objective["disabled_reason"] = f"Circuit breaker: {failures} consecutive failures at {datetime.now().isoformat()}"
        save_objective(objective)
        return

    public_config = load_public_contribution_config()
    if str(public_config.get("mode") or "").strip().lower() in {"draft_prs", "pending_auth"}:
        cycle_num = objective.get("total_evolutions", 0) + 1
        try:
            run_public_contribution_cycle(objective=objective, cycle_num=cycle_num)
            set_consecutive_failures(0)
        except Exception as e:
            log(f"Public core contribution failed: {e}")
            set_consecutive_failures(failures + 1)
        return

    # Dry-run restore test
    log("Running restore dry-run test...")
    if not dry_run_restore_test():
        log("CRITICAL: Restore test failed — aborting")
        set_consecutive_failures(failures + 1)
        sys.exit(1)
    log("Restore test PASSED")

    # Apply user-approved proposals from prior cycles BEFORE generating new ones.
    # nexo_evolution_approve marks proposals as 'accepted' but until m38 there
    # was no consumer for that status. This step closes the loop so the user's
    # explicit approvals actually run on the next cycle, with the same sandbox
    # / snapshot / rollback safety as live AUTO proposals.
    log("Checking for user-approved proposals to apply...")
    cycle_num_for_apply = objective.get("total_evolutions", 0) + 1
    evolution_mode_for_apply = _normalize_mode(objective.get("evolution_mode", "auto"))
    max_to_apply = max_auto_changes(objective.get("total_evolutions", 0))
    apply_conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    apply_conn.row_factory = sqlite3.Row
    apply_conn.execute("PRAGMA busy_timeout=5000")
    try:
        apply_stats = _apply_accepted_proposals(
            apply_conn,
            cycle_num_for_apply,
            max_to_apply,
            evolution_mode_for_apply,
        )
        if apply_stats["attempted"]:
            log(
                f"  Applied {apply_stats['applied']}/{apply_stats['attempted']} accepted proposals "
                f"({apply_stats['rolled_back']} rolled back, "
                f"{apply_stats['blocked']} blocked, "
                f"{apply_stats['skipped']} skipped, "
                f"{apply_stats['failed']} failed)"
            )
        else:
            log("  No user-approved proposals pending")
    except Exception as e:
        log(f"  WARN: apply_accepted_proposals raised: {e}")
    finally:
        apply_conn.close()

    # Gather data
    log("Gathering week data from nexo.db...")
    week_data = get_week_data(str(NEXO_DB))
    log(f"  Learnings: {len(week_data.get('learnings', []))}")
    log(f"  Decisions: {len(week_data.get('decisions', []))}")
    log(f"  Changes: {len(week_data.get('changes', []))}")
    log(f"  Diaries: {len(week_data.get('diaries', []))}")

    # Build prompt
    prompt = build_evolution_prompt(week_data, objective)
    log(f"Prompt built: {len(prompt)} chars")

    # Verify the configured automation backend is available before calling
    if not verify_claude_cli():
        log("Automation backend not available or not authenticated. Skipping evolution run.")
        return

    # Call the configured automation backend with the legacy opus task profile
    log("Calling automation backend with the opus task profile...")
    try:
        raw_response = call_claude_cli(prompt)
    except Exception as e:
        log(f"Automation backend call failed: {e}")
        set_consecutive_failures(failures + 1)
        return

    log(f"Response received: {len(raw_response)} chars")

    # Parse JSON
    try:
        text = raw_response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        response = json.loads(text.strip())
    except Exception as e:
        log(f"JSON parse failed: {e}")
        log(f"Raw (first 500): {raw_response[:500]}")
        set_consecutive_failures(failures + 1)
        return

    # Reset consecutive failures on successful parse
    set_consecutive_failures(0)

    log(f"Analysis: {response.get('analysis', 'N/A')[:200]}")

    # Log patterns
    for p in response.get("patterns", []):
        log(f"  Pattern [{p.get('type', '?')}]: {p.get('description', '')[:100]} (freq: {p.get('frequency', '?')})")

    # Process proposals
    proposals = response.get("proposals", [])
    cycle_num = objective.get("total_evolutions", 0) + 1
    max_auto = max_auto_changes(objective.get("total_evolutions", 0))
    auto_count = 0
    auto_applied = 0
    evolution_mode = _normalize_mode(objective.get("evolution_mode", "auto"))

    conn = sqlite3.connect(str(NEXO_DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")

    followup_items = []

    for p in proposals:
        classification = p.get("classification", "propose")
        dimension = p.get("dimension", "other")
        action = p.get("action", "")
        reasoning = p.get("reasoning", "")
        scope = p.get("scope", "local")  # "public" or "local"

        if evolution_mode == "review":
            log(f"  QUEUED [{scope}]: {action[:80]}")
            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status, proposal_payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, classification, reasoning, "pending_review",
                 json.dumps(p, ensure_ascii=False))
            )
            followup_items.append({
                "dimension": dimension,
                "action": action,
                "reasoning": reasoning,
                "scope": scope,
                "classification": classification,
                "status": "pending_review",
            })

        elif classification == "auto" and auto_count < max_auto:
            auto_count += 1
            log(f"  AUTO #{auto_count}/{max_auto}: {action[:80]}")

            result = execute_auto_proposal(p, cycle_num, conn, mode=evolution_mode)
            status = result["status"]

            cur = conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status, files_changed, snapshot_ref, test_result, proposal_payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, "auto", reasoning, status,
                 json.dumps(result.get("files_changed", [])),
                 result.get("snapshot_ref", ""),
                 result.get("test_result", ""),
                 json.dumps(p, ensure_ascii=False))
            )
            log_id = cur.lastrowid

            if status == "applied":
                auto_applied += 1
                log(f"    APPLIED successfully")
            elif status == "blocked":
                detail = result.get("reason") or result.get("test_result", "")
                log(f"    BLOCKED: {detail[:100]}")
                _create_failure_followup(conn, cycle_num, log_id, p, result)
            elif status == "skipped":
                log(f"    SKIPPED: {result.get('reason', '')}")
            else:
                log(f"    ROLLED BACK: {result.get('test_result', '')[:100]}")
                _create_failure_followup(conn, cycle_num, log_id, p, result)

        else:
            # PROPOSE or over auto limit
            if classification == "auto" and auto_count >= max_auto:
                log(f"  AUTO→PROPOSE (over limit {max_auto}): {action[:80]}")
                classification = "propose"
            else:
                log(f"  PROPOSE: {action[:80]}")

            conn.execute(
                "INSERT INTO evolution_log (cycle_number, dimension, proposal, classification, "
                "reasoning, status, proposal_payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cycle_num, dimension, action, classification, reasoning, "proposed",
                 json.dumps(p, ensure_ascii=False))
            )
            if evolution_mode in {"review", "managed"}:
                followup_items.append({
                    "dimension": dimension,
                    "action": action,
                    "reasoning": reasoning,
                    "scope": scope,
                    "classification": classification,
                    "status": "proposed",
                })

    conn.commit()

    if evolution_mode in {"review", "managed"} and followup_items:
        _create_cycle_followup(conn, cycle_num, followup_items, response.get("analysis", ""), evolution_mode)

    # Update metrics
    scores = response.get("dimension_scores", {})
    evidence = response.get("score_evidence", {})
    current = week_data.get("current_metrics", {})

    for dim, score in scores.items():
        if isinstance(score, (int, float)) and 0 <= score <= 100:
            prev = current.get(dim, {}).get("score", 0)
            delta = int(score) - prev
            conn.execute(
                "INSERT INTO evolution_metrics (dimension, score, evidence, delta) VALUES (?, ?, ?, ?)",
                (dim, int(score), json.dumps(evidence.get(dim, "")), delta)
            )

    conn.commit()
    conn.close()

    # Update objective
    objective["last_evolution"] = str(date.today())
    objective["total_evolutions"] = cycle_num
    objective["total_proposals_made"] = objective.get("total_proposals_made", 0) + len(proposals)
    objective["total_auto_applied"] = objective.get("total_auto_applied", 0) + auto_applied
    for dim, score in scores.items():
        if dim in objective.get("dimensions", {}) and isinstance(score, (int, float)):
            objective["dimensions"][dim]["current"] = int(score)

    objective.setdefault("history", []).insert(0, {
        "cycle": cycle_num,
        "date": str(date.today()),
        "mode": evolution_mode,
        "proposals": len(proposals),
        "auto_count": auto_count,
        "auto_applied": auto_applied,
        "analysis": response.get("analysis", "")[:200]
    })
    objective["history"] = objective["history"][:12]

    save_objective(objective)

    log(f"Evolution cycle #{cycle_num} COMPLETE: {len(proposals)} proposals "
        f"({auto_count} auto, {auto_applied} applied, "
        f"{len(proposals) - auto_count} propose)")
    log("=" * 60)


def _update_catchup_state():
    """Register successful run for catch-up."""
    try:
        import json as _json
        from pathlib import Path as _Path

        _state_file = NEXO_HOME / "operations" / ".catchup-state.json"
        _state = _json.loads(_state_file.read_text()) if _state_file.exists() else {}
        _state["evolution"] = datetime.now().isoformat()
        _state_file.write_text(_json.dumps(_state, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    try:
        run()
        _update_catchup_state()
    except Exception as e:
        log(f"FATAL: {e}")
        import traceback
        log(traceback.format_exc())
        sys.exit(1)
