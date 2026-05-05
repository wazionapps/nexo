from __future__ import annotations
import sqlite3

"""Post-tool guardrails for conditioned file learnings."""

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
import paths

from core_prompts import render_core_prompt
from db import create_protocol_debt, create_protocol_task, get_db, get_last_heartbeat_ts
from operator_language import append_operator_language_contract
from plugins.guard import _load_conditioned_learnings, _normalize_path_token
from protocol_settings import get_protocol_strictness
from product_mode import core_writes_allowed, is_protected_runtime_core_path

READ_LIKE_TOOLS = {"Read"}
WRITE_LIKE_TOOLS = {"Edit", "MultiEdit", "Write"}
DELETE_LIKE_TOOLS = {"Delete"}
NON_TRIVIAL_PROTOCOL_TOOLS = {"Read", "Bash", "Grep", "Glob", "Edit", "MultiEdit", "Write", "Delete"}
PROTOCOL_SKIP_TOOLS = {
    "nexo_startup",
    "nexo_smart_startup",
    "nexo_stop",
    "nexo_heartbeat",
    "nexo_task_open",
    "nexo_task_close",
    "nexo_workflow_open",
    "nexo_workflow_update",
    "nexo_guard_check",
    "nexo_guard_file_check",
    "nexo_rules_check",
}
ACTION_TASK_TYPES = {"edit", "execute", "delegate"}
NEXO_CODE_ROOT = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent))).expanduser().resolve()
LIVE_REPO_ROOT = NEXO_CODE_ROOT.parent if NEXO_CODE_ROOT.name == "src" else NEXO_CODE_ROOT
PUBLIC_REPO_DIRS = {
    ".claude-plugin",
    ".github",
    "bin",
    "clawhub-skill",
    "community",
    "docs",
    "hooks",
    "openclaw-plugin",
    "src",
    "templates",
    "tests",
}
PUBLIC_REPO_FILES = {
    ".mcp.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "docker-compose.yml",
    "package-lock.json",
    "package.json",
}
SHELL_DELETE_BASES = {"rm", "unlink", "rmdir"}
SHELL_WRITE_BASES = {
    "mv",
    "cp",
    "touch",
    "install",
    "mkdir",
    "ln",
    "chmod",
    "chown",
    "setfacl",
    "tee",
    "rsync",
}
SHELL_REDIRECT_TOKENS = {">", ">>", "1>", "1>>", "2>", "2>>"}
INLINE_INTERPRETER_BASES = {
    "python",
    "python3",
    "python3.11",
    "python3.12",
    "python3.13",
    "node",
    "php",
    "ruby",
    "perl",
}
INLINE_DELETE_RE = re.compile(
    r"(?:"
    r"\.unlink\s*\(|"
    r"os\.remove\s*\(|"
    r"shutil\.rmtree\s*\(|"
    r"unlink(?:Sync)?\s*\(|"
    r"rm(?:Sync)?\s*\(|"
    r"rmdir(?:Sync)?\s*\(|"
    r"remove(?:Sync)?\s*\(|"
    r"file_delete\s*\("
    r")",
    re.IGNORECASE,
)
INLINE_WRITE_RE = re.compile(
    r"(?:"
    r"write_text\s*\(|"
    r"write_bytes\s*\(|"
    r"appendFile(?:Sync)?\s*\(|"
    r"writeFile(?:Sync)?\s*\(|"
    r"copyFile(?:Sync)?\s*\(|"
    r"rename(?:Sync)?\s*\(|"
    r"mkdir(?:Sync)?\s*\(|"
    r"symlink(?:Sync)?\s*\(|"
    r"chmod(?:Sync)?\s*\(|"
    r"chown(?:Sync)?\s*\(|"
    r"file_put_contents\s*\(|"
    r"open\([^\n]*?,\s*['\"][wax+][^'\"]*['\"]"
    r")",
    re.IGNORECASE,
)
EMBEDDED_PATH_RE = re.compile(r"(~\/[^'\"\s,);]+|\/[^'\"\s,);]+)")


# Block K G3: destructive commands whose blast radius warrants a cortex
# decision before they execute. The list is deliberately tight — only
# the patterns that historically cause irrecoverable damage. Each regex
# is tested against the full Bash command string (post shlex split
# rebuild) so distinct spacings and option orderings do not slip past.
DESTRUCTIVE_COMMAND_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # Matches both combined ``rm -rf`` and split ``rm -r -f`` forms. The
    # character class ``[rfRF]*`` after the dash tolerates any flag
    # ordering while both ``r`` and ``f`` (case-insensitive) must be
    # present for the match to fire.
    ("rm_rf", re.compile(r"\brm\s+-[rfRF]*(?:[rR][rfRF]*[fF]|[fF][rfRF]*[rR])[rfRF]*\b", re.IGNORECASE)),
    ("git_push_force", re.compile(r"\bgit\s+push\s+(?:.*\s)?(?:--force|-f)\b(?!.*-with-lease)", re.IGNORECASE)),
    ("drop_table", re.compile(r"\bdrop\s+(?:table|database|schema)\b", re.IGNORECASE)),
    ("truncate_table", re.compile(r"\btruncate\s+(?:table\s+)?\w+", re.IGNORECASE)),
    ("curl_pipe_bash", re.compile(r"curl[^|]*\|\s*(?:sudo\s+)?(?:bash|sh|zsh)\b", re.IGNORECASE)),
    ("wget_pipe_bash", re.compile(r"wget[^|]*\|\s*(?:sudo\s+)?(?:bash|sh|zsh)\b", re.IGNORECASE)),
    # ``dd if=... of=/dev/sda`` — match any ordering of args; we flag the
    # presence of ``of=/dev/...`` which points at a raw block/device.
    ("dd_of_root", re.compile(r"\bdd\s+.*?\bof=/dev/\S+", re.IGNORECASE)),
    ("chmod_777_recursive", re.compile(r"\bchmod\s+-R\s+(?:777|666|a\+rw)\b", re.IGNORECASE)),
)


def _classify_destructive_intent(command: str) -> str | None:
    """Return the matching pattern name if ``command`` looks destructive.

    None when none of the DESTRUCTIVE_COMMAND_PATTERNS match. Intentionally
    strict: we would rather miss a novel attack shape and rely on the
    existing ``strict``/``write_without_file_guard_check`` gates than
    inject false positives on routine Bash usage.
    """
    cmd = str(command or "")
    for name, pattern in DESTRUCTIVE_COMMAND_PATTERNS:
        if pattern.search(cmd):
            return name
    return None


# Block K G3 (SSH wrapper): remote-write patterns the local destructive
# gate never sees because they run inside ``ssh host '...'`` / rsync /
# scp / sftp. Matched against the raw Bash command string.
# Each regex aims to catch a well-known write primitive *inside* a remote
# invocation. ``ssh host 'ls'`` must not match — only write-verbs do.
_SSH_REMOTE_SHELL_RE = re.compile(
    r"\bssh\b[^'\"`]*?(?:['\"`])(?P<remote>[^'\"`]+)(?:['\"`])",
    re.IGNORECASE,
)
_SSH_REMOTE_WRITE_VERBS = (
    re.compile(r"^\s*cat\s*>\s*\S", re.IGNORECASE),                  # cat > file
    re.compile(r"^\s*cat\s*>>\s*\S", re.IGNORECASE),                 # cat >> file
    re.compile(r"\btee\s+(?:-\S+\s+)*[^\s|&;]+", re.IGNORECASE),      # tee [-a] file
    re.compile(r"^\s*(?:echo|printf)\s+.*\s+>>?\s*\S", re.IGNORECASE),  # echo ... > file
    re.compile(r"\bsed\s+-i\b", re.IGNORECASE),                      # sed -i ...
    re.compile(r"(?:^|\s)>\s*\S", re.IGNORECASE),                    # bare > file
    re.compile(r"(?:^|\s)>>\s*\S", re.IGNORECASE),                   # bare >> file
    re.compile(r"\brm\s+-\S*[rRfF]", re.IGNORECASE),                 # remote rm -rf
    re.compile(r"\bmv\s+\S+\s+\S+", re.IGNORECASE),                  # remote mv
    re.compile(r"\bcp\s+\S+\s+\S+", re.IGNORECASE),                  # remote cp
)
_SCP_WRITE_RE = re.compile(
    r"\bscp\b[^|&;]*?\s\S+\s+[^:\s]+:[^\s]+",
    re.IGNORECASE,
)
_RSYNC_WRITE_RE = re.compile(
    r"\brsync\b[^|&;]*?\s\S+\s+[^:\s]+:[^\s]+",
    re.IGNORECASE,
)
_SFTP_BATCH_RE = re.compile(
    r"\bsftp\b(?:[^|&;]*\s)?-b\s+\S+",
    re.IGNORECASE,
)
_SSH_REMOTE_PIPE_RE = re.compile(
    r"\|\s*ssh\b",
    re.IGNORECASE,
)
_SSH_REMOTE_STDIN_RE = re.compile(
    r"\bssh\b[^\n|&;]*(?:<\s*\S+|<<-?\s*(?:['\"]?[A-Za-z0-9_]+['\"]?))",
    re.IGNORECASE,
)


def _classify_ssh_remote_write(command: str) -> str | None:
    """Return the matching pattern name when ``command`` writes to a remote host.

    Covers four shapes:
        1. ``ssh host '<remote-shell-that-writes>'`` with or without
           ``-o`` flags, using single/double/backtick quoting.
        2. ``scp LOCAL_PATH host:REMOTE_PATH`` (upload direction).
        3. ``rsync [opts] LOCAL_PATH host:REMOTE_PATH`` (upload direction).
        4. ``sftp -b batchfile host`` (any -b invocation is considered a
           write candidate because the batch may mutate).

    Ignores read-only invocations such as ``ssh host 'ls /etc'`` or
    ``scp host:REMOTE /local/`` (download), which is the common case.
    """
    cmd = str(command or "")

    if _SCP_WRITE_RE.search(cmd):
        # Disambiguate download (host:remote local) vs upload (local host:remote).
        # Simple rule: if the FIRST ``host:path`` argument is preceded by a
        # local-looking arg, treat as upload.
        download = re.search(r"\bscp\b[^|&;]*?\s[^:\s]+:\S+\s+\S+", cmd)
        if not download:
            return "scp_remote_write"
    if _RSYNC_WRITE_RE.search(cmd):
        download = re.search(r"\brsync\b[^|&;]*?\s[^:\s]+:\S+\s+\S+", cmd)
        if not download:
            return "rsync_remote_write"
    if _SFTP_BATCH_RE.search(cmd):
        return "sftp_batch_remote_write"

    for match in _SSH_REMOTE_SHELL_RE.finditer(cmd):
        remote = match.group("remote") or ""
        # Strip leading "sudo ", "env VAR=x ", "cd dir &&" — they never mean
        # a write by themselves, and may hide real writes behind them.
        trimmed = re.sub(r"^\s*(?:sudo\s+|env\s+\S+=\S+\s+|cd\s+\S+\s*&&\s*)+", "", remote)
        for pattern in _SSH_REMOTE_WRITE_VERBS:
            if pattern.search(trimmed):
                return "ssh_remote_shell_write"
    if _SSH_REMOTE_PIPE_RE.search(cmd):
        return "ssh_remote_shell_write"
    if _SSH_REMOTE_STDIN_RE.search(cmd):
        return "ssh_remote_shell_write"
    return None


def _operation_kind(tool_name: str) -> str:
    if tool_name in READ_LIKE_TOOLS:
        return "read"
    if tool_name in WRITE_LIKE_TOOLS:
        return "write"
    if tool_name in DELETE_LIKE_TOOLS:
        return "delete"
    return "other"


def _short_tool_name(tool_name: str) -> str:
    clean = str(tool_name or "").strip()
    return clean.rsplit("__", 1)[-1] if "__" in clean else clean


def _canonical_hook_tool_name(tool_name: str) -> str:
    clean = _short_tool_name(tool_name)
    lowered = clean.strip().lower()
    if lowered in {"bash", "shell", "shell_command", "exec_command", "local_shell"}:
        return "Bash"
    return clean


def _normalize_file_path(path: str) -> str:
    return _normalize_path_token(str(Path(path)))


# Tokens that look like absolute paths but never refer to real files. They
# typically come from shell heredocs, JSON keys (``/DTEND``), regex/glob
# fragments, or numeric/dictionary substrings the bash extractor lifted out
# of a quoted argument. Without this filter the hook keeps emitting
# unack-eable g4_guard_check_required entries (self-audit 2026-04-24 C2).
_PATH_ARTIFACT_RE = re.compile(
    r"""
    [\$\`]                # unresolved shell substitution / backtick boundary
    | [\*\?]              # glob metacharacter
    | [\[\]\{\}]          # bracket/range/heredoc markers
    | [\|\=\;]            # regex fragments / shell assignment / command separators
    | \s                  # embedded whitespace (most likely truncation)
    """,
    re.VERBOSE,
)
_DATE_LIKE_PATH_RE = re.compile(r"^/\d{1,4}/\d{1,4}(?:/\d{1,4})?$")
_STRICT_WRITE_HEARTBEAT_WINDOW_SECONDS = 300
_G3_CORTEX_AUTH_WINDOW_SECONDS = max(
    60,
    int(os.environ.get("NEXO_G3_CORTEX_AUTH_WINDOW_SECONDS", "900")),
)
_CORTEX_NEGATIVE_TOKENS = (
    "abort",
    "avoid",
    "block",
    "cancel",
    "decline",
    "defer",
    "deny",
    "do_not",
    "dont",
    "no_",
    "not_now",
    "reject",
    "skip",
    "wait",
)
_G3_CORTEX_GENERIC_APPROVAL_TOKENS = (
    "allow",
    "apply",
    "approve",
    "continue",
    "deploy",
    "execute",
    "go_ahead",
    "proceed",
    "publish",
    "retry",
    "run",
)
_G3_CORTEX_FAMILY_TOKENS = {
    "destructive": (
        "chmod",
        "cleanup",
        "delete",
        "drop",
        "force",
        "git_push",
        "purge",
        "remove",
        "rm",
        "truncate",
        "wipe",
    ),
    "ssh": (
        "deploy",
        "remote",
        "rsync",
        "scp",
        "sftp",
        "ssh",
        "sync",
        "upload",
    ),
}

# Single-segment ``/word`` candidates that match a small dictionary block-list
# of confirmed false positives observed in the live debt log.
_PATH_DICTIONARY_BLOCKLIST = frozenset(
    {
        "/diary",
        "/stdout",
        "/stderr",
        "/estancada",
        "/confirmacion",
        "/confirmación",
        "/window",
        "/restaurar",
        "/dtend",
        "/dtstart",
        "/summary",
    }
)


def _looks_like_real_path(path: str) -> bool:
    """Return True only when ``path`` plausibly refers to a real file.

    The protocol-pretool guardrail uses this filter to suppress noise
    coming from shell heredocs, glob fragments, and dictionary words that
    the bash extractor sometimes mistakes for absolute paths. Without it
    every false positive becomes a permanent ``g4_guard_check_required``
    debt row that nobody can ack.
    """

    raw = str(path or "").strip()
    if not raw:
        return False
    if not raw.startswith("/"):
        return False
    if _PATH_ARTIFACT_RE.search(raw):
        return False
    if _DATE_LIKE_PATH_RE.fullmatch(raw):
        return False
    # Pure numeric segments (``/166``, ``/487``, ``/1000``) are almost
    # always status codes or counters lifted out of a log line.
    stripped = raw.lstrip("/")
    if stripped and re.fullmatch(r"\d+", stripped):
        return False
    if raw.lower() in _PATH_DICTIONARY_BLOCKLIST:
        return False
    # Reject single-segment ``/word`` candidates that do not exist on the
    # filesystem and have no extension. Real edits target nested paths or
    # well-known top-level files (``/etc/hosts`` etc.) that already pass
    # the dictionary check above. Globs hitting ``/etc`` etc. are rare
    # and acceptable to over-filter compared with the noise we suppress.
    if "/" not in stripped and "." not in stripped:
        try:
            if not Path(raw).exists():
                return False
        except OSError:
            return False
    parts = [segment for segment in stripped.split("/") if segment]
    if len(parts) > 1 and "." not in parts[-1]:
        try:
            if not Path(raw).exists():
                return False
        except OSError:
            return False
    return True


def _strict_write_without_task_severity(session_id: str) -> str:
    """Downgrade missing-task debt when the session is clearly alive.

    A recent heartbeat shows the session is connected to a real ongoing
    conversation even if the operator skipped `nexo_task_open`. We still
    block strict writes, but store the debt as warn so dashboards separate
    protocol drift from completely untracked edits.
    """

    if not session_id:
        return "error"
    try:
        last_hb = get_last_heartbeat_ts(session_id)
    except Exception:
        return "error"
    if last_hb is None:
        return "error"
    if time.time() - float(last_hb) <= _STRICT_WRITE_HEARTBEAT_WINDOW_SECONDS:
        return "warn"
    return "error"


def _auto_task_open_enabled() -> bool:
    return os.environ.get("NEXO_AUTO_TASK_OPEN", "1").strip().lower() not in {"0", "false", "no", "off"}


def _auto_open_protocol_task_for_write(*, sid: str, tool_name: str, operation: str, files: list[str]) -> dict | None:
    if not sid or not _auto_task_open_enabled():
        return None
    task_type = "edit" if operation == "write" else "execute"
    clean_files = [item for item in files if str(item or "").strip()]
    target = ", ".join(clean_files[:3]) if clean_files else "unknown target"
    if len(clean_files) > 3:
        target += f", +{len(clean_files) - 3} more"
    try:
        return create_protocol_task(
            sid,
            f"Auto-opened {task_type} task for {tool_name} on {target}",
            task_type=task_type,
            area="auto",
            context_hint="PreToolUse auto-task_open: write/delete attempted without a matching open task.",
            files=clean_files,
            plan=[
                "Auto-opened because the agent attempted a write/delete before explicit task_open.",
                "Verify the edit and close with evidence.",
            ],
            constraints=[
                "Do not treat this auto-open as success evidence.",
                "Close as done only after verification; otherwise close partial/failed.",
            ],
            verification_step="Run the relevant test or inspection and close with evidence.",
            must_verify=True,
            must_change_log=True,
        )
    except Exception:
        return None


def _resolve_runtime_path(path: str) -> Path:
    candidate = Path(str(path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _automation_live_repo_guard_enabled() -> bool:
    return (
        os.environ.get("NEXO_AUTOMATION", "").strip() == "1"
        and os.environ.get("NEXO_PUBLIC_CONTRIBUTION", "").strip() != "1"
    )


def _has_git_marker(root: Path) -> bool:
    return (root / ".git").exists()


def _is_public_repo_surface(candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(LIVE_REPO_ROOT)
    except ValueError:
        return False

    parts = relative.parts
    if not parts:
        return False
    if parts[0] in PUBLIC_REPO_DIRS:
        return True
    return len(parts) == 1 and parts[0] in PUBLIC_REPO_FILES


def _is_live_repo_path(path: str) -> bool:
    if not str(path or "").strip():
        return False
    try:
        if not _has_git_marker(LIVE_REPO_ROOT):
            return False
        return _is_public_repo_surface(_resolve_runtime_path(path))
    except Exception:
        return False


def _extract_touched_files(tool_input) -> list[str]:
    files: list[str] = []
    if not isinstance(tool_input, dict):
        return files

    def add(candidate) -> None:
        if isinstance(candidate, str) and candidate.strip():
            files.append(candidate.strip())

    add(tool_input.get("file_path"))
    add(tool_input.get("path"))

    for key in ("paths", "file_paths", "files"):
        value = tool_input.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    add(item.get("file_path"))
                    add(item.get("path"))

    unique: list[str] = []
    seen = set()
    for item in files:
        if not _looks_like_real_path(item):
            continue
        normalized = _normalize_file_path(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(item)
    return unique


def _extract_bash_command(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _shell_tokens(command: str) -> list[str]:
    if not str(command or "").strip():
        return []
    try:
        return shlex.split(command)
    except Exception:
        return str(command).split()


def _resolve_shell_candidate_path(token: str, cwd: str) -> str:
    raw = os.path.expandvars(str(token or "").strip())
    if not raw:
        return ""
    if raw.startswith("~"):
        raw = str(Path(raw).expanduser())
    path = Path(raw)
    if not path.is_absolute():
        if not str(cwd or "").strip():
            return ""
        path = Path(cwd).expanduser() / path
    return str(path.resolve(strict=False))


def _classify_bash_operation(command: str) -> str:
    tokens = _shell_tokens(command)
    if not tokens:
        return "other"
    if any(token in SHELL_REDIRECT_TOKENS for token in tokens):
        return "write"
    base = Path(tokens[0]).name.lower()
    if base in SHELL_DELETE_BASES:
        return "delete"
    if base in SHELL_WRITE_BASES:
        return "write"
    if base == "sed" and "-i" in tokens:
        return "write"
    if base == "perl" and any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
        return "write"
    if base in INLINE_INTERPRETER_BASES:
        if INLINE_DELETE_RE.search(command):
            return "delete"
        if INLINE_WRITE_RE.search(command):
            return "write"
    return "other"


def _extract_bash_touched_files(tool_input) -> list[str]:
    command = _extract_bash_command(tool_input)
    tokens = _shell_tokens(command)
    if not tokens:
        return []
    cwd = ""
    if isinstance(tool_input, dict):
        cwd = str(tool_input.get("cwd") or "").strip()

    candidates: list[str] = []
    seen: set[str] = set()
    suffixes = {
        ".py", ".md", ".json", ".jsonl", ".sh", ".txt", ".toml", ".yaml", ".yml",
        ".js", ".ts", ".tsx", ".jsx", ".php", ".sql", ".rs", ".go", ".c", ".cpp",
        ".h", ".css", ".html",
        # ``.plist`` is needed so that ``_collect_launchagent_write_blocks``
        # can see managed LaunchAgent plists inside Bash commands such as
        # ``chmod 755 ~/Library/LaunchAgents/com.nexo.runner-health-check.plist``.
        ".plist",
    }

    def add(candidate: str) -> None:
        resolved = _resolve_shell_candidate_path(candidate, cwd)
        if not resolved or not _looks_like_real_path(resolved):
            return
        normalized = _normalize_file_path(resolved) if resolved else ""
        if resolved and normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(resolved)

    for index, token in enumerate(tokens):
        if token in SHELL_REDIRECT_TOKENS:
            if index + 1 < len(tokens):
                add(tokens[index + 1])
            continue
        if token.startswith("-"):
            continue
        if (
            token.startswith(("/", "~", ".", "$"))
            or "/" in token
            or Path(token).suffix.lower() in suffixes
        ):
            add(token)
    for match in EMBEDDED_PATH_RE.findall(command):
        add(match)
    return candidates


def _resolve_nexo_sid(conn, external_session_id: str) -> str:
    """Resolve a Claude Code UUID to the NEXO session SID it belongs to.

    Resolution order:

    1. ``session_claude_aliases`` (added in migration v43) — a 1-to-N
       mapping from NEXO sid to every ``claude_session_id`` that has
       ever been registered against it. Supports NEXO Desktop's
       multi-conversation workflow where each spawn has a distinct UUID.
    2. Legacy ``sessions.external_session_id / claude_session_id`` —
       kept for backward compatibility with rows created before v43.
    3. Single-session fallback (v6.0.7) — exactly one session with a
       fresh heartbeat (last 5 min) still triggers the implicit bind;
       this closes the compaction-rotated-UUID edge case.
    """
    clean_external = external_session_id.strip()
    if clean_external:
        # 1. Aliases table — supports N claude_sids per nexo sid.
        try:
            alias_row = conn.execute(
                """SELECT sid
                   FROM session_claude_aliases
                   WHERE claude_session_id = ?
                   ORDER BY last_seen DESC
                   LIMIT 1""",
                (clean_external,),
            ).fetchone()
            if alias_row:
                return str(alias_row["sid"])
        except sqlite3.OperationalError as exc:
            # Narrow-catch per audit MEDIUM: only swallow the specific
            # "no such table" error that indicates the v43 migration
            # has not yet been applied. Any other SQLite failure
            # (schema corruption, lock contention, column drift)
            # surfaces through the logger so operators see it.
            msg = str(exc).lower()
            if "no such table" not in msg:
                import logging as _log
                _log.getLogger("nexo.hooks").warning(
                    "session_claude_aliases probe failed: %s; falling back to legacy lookup",
                    exc,
                )
            # Either way: fall through to legacy path.
        # 2. Legacy columns (pre-v43 rows).
        row = conn.execute(
            """SELECT sid
               FROM sessions
               WHERE external_session_id = ? OR claude_session_id = ?
               ORDER BY last_update_epoch DESC
               LIMIT 1""",
            (clean_external, clean_external),
        ).fetchone()
        if row:
            return str(row["sid"])

    # 3. Fallback: exactly one session heartbeated in the last 5 minutes.
    # We prefer this narrow window so we never silently attribute work to
    # a stale session. If the caller has zero or multiple active sessions,
    # fail closed (return "") and let the caller raise missing_startup.
    import time as _time
    cutoff_epoch = _time.time() - 300.0
    rows = conn.execute(
        """SELECT sid
           FROM sessions
           WHERE last_update_epoch >= ?
           ORDER BY last_update_epoch DESC""",
        (cutoff_epoch,),
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["sid"])
    return ""


def register_claude_session_alias(conn, sid: str, claude_session_id: str) -> bool:
    """Register a ``(sid, claude_session_id)`` alias so PreToolUse hook
    lookups on this UUID resolve to the NEXO sid.

    Idempotent — re-registering the same pair bumps ``last_seen`` only.
    Returns True when the alias table accepted the write, False when it
    is unavailable (pre-v43 schema) so callers know to fall back to the
    legacy single-column write.
    """
    sid = (sid or "").strip()
    claude_session_id = (claude_session_id or "").strip()
    if not sid or not claude_session_id:
        return False
    import time as _time
    now = _time.time()
    try:
        conn.execute(
            """INSERT INTO session_claude_aliases
                 (sid, claude_session_id, first_seen, last_seen)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(sid, claude_session_id) DO UPDATE SET
                   last_seen = excluded.last_seen""",
            (sid, claude_session_id, now, now),
        )
        conn.commit()
        return True
    except Exception:
        return False


def _find_open_task_for_file(conn, sid: str, filepath: str) -> dict | None:
    target = _normalize_file_path(filepath)
    rows = conn.execute(
        """SELECT task_id, files, guard_has_blocking, guard_acknowledged, task_type, plan, unknowns,
                  opened_at,
                  verification_step, opened_with_guard, must_change_log, must_verify
           FROM protocol_tasks
           WHERE session_id = ? AND status = 'open'
           ORDER BY opened_at DESC""",
        (sid,),
    ).fetchall()
    for row in rows:
        try:
            files = json.loads(row["files"] or "[]")
        except Exception:
            files = []
        for item in files if isinstance(files, list) else []:
            if _normalize_file_path(str(item)) == target:
                return dict(row)
    return None


def _find_any_open_task(conn, sid: str) -> dict | None:
    row = conn.execute(
        """SELECT task_id, files, guard_has_blocking, guard_acknowledged, task_type, plan, unknowns,
                  opened_at,
                  verification_step, opened_with_guard, must_change_log, must_verify
           FROM protocol_tasks
           WHERE session_id = ? AND status = 'open'
           ORDER BY opened_at DESC
           LIMIT 1""",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def _normalize_cortex_tokens(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _text_has_any_token(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normalize_cortex_tokens(value)
    if not normalized:
        return False
    return any(token in normalized for token in tokens)


def _cortex_choice_is_negative(value: str) -> bool:
    return _text_has_any_token(value, _CORTEX_NEGATIVE_TOKENS)


def _find_recent_cortex_authorization(
    conn,
    *,
    sid: str,
    task: dict | None,
    gate_family: str,
    pattern_name: str = "",
) -> dict | None:
    if not sid or not task:
        return None
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return None
    params: list[object] = [
        sid,
        task_id,
        f"-{_G3_CORTEX_AUTH_WINDOW_SECONDS} seconds",
    ]
    sql = (
        """SELECT id, task_id, recommended_choice, selected_choice,
                  recommended_reasoning, selection_reason, context_hint, created_at
           FROM cortex_evaluations
           WHERE session_id = ?
             AND task_id = ?
             AND created_at >= datetime('now', ?)"""
    )
    opened_at = str(task.get("opened_at") or "").strip()
    if opened_at:
        sql += " AND created_at >= ?"
        params.append(opened_at)
    sql += " ORDER BY created_at DESC, id DESC LIMIT 5"
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return None
    family_tokens = _G3_CORTEX_FAMILY_TOKENS.get(gate_family, ())
    pattern_tokens = tuple(
        token for token in _normalize_cortex_tokens(pattern_name).split("_") if token
    )
    fallback_candidates: list[dict] = []
    for row in rows:
        item = dict(row)
        choice = str(item.get("selected_choice") or item.get("recommended_choice") or "").strip()
        if not choice or _cortex_choice_is_negative(choice):
            continue
        combined = " ".join(
            [
                choice,
                str(item.get("selection_reason") or ""),
                str(item.get("recommended_reasoning") or ""),
                str(item.get("context_hint") or ""),
            ]
        )
        if (
            _text_has_any_token(choice, _G3_CORTEX_GENERIC_APPROVAL_TOKENS)
            or _text_has_any_token(combined, family_tokens)
            or _text_has_any_token(combined, pattern_tokens)
        ):
            return item
        fallback_candidates.append(item)
    if len(fallback_candidates) == 1:
        return fallback_candidates[0]
    return None


def _find_any_open_workflow(conn, sid: str) -> dict | None:
    row = conn.execute(
        """SELECT run_id, protocol_task_id, current_step_key
           FROM workflow_runs
           WHERE session_id = ? AND status IN ('open', 'running', 'blocked', 'waiting_approval')
           ORDER BY updated_at DESC, run_id DESC
           LIMIT 1""",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def _session_has_guard_check(conn, sid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM guard_checks WHERE session_id = ? LIMIT 1",
        (sid,),
    ).fetchone()
    return bool(row)


def _session_has_guard_for_file(conn, sid: str, filepath: str) -> bool:
    """Check if guard_check was called for a specific file in this session."""
    if not filepath:
        return False
    normalized = _normalize_file_path(filepath)
    basename = os.path.basename(filepath)
    # guard_checks.files is a comma-separated or JSON list of paths/areas
    row = conn.execute(
        """SELECT 1 FROM guard_checks
           WHERE session_id = ?
             AND (files LIKE ? OR files LIKE ? OR files LIKE ?)
           LIMIT 1""",
        (sid, f"%{normalized}%", f"%{basename}%", f"%{filepath}%"),
    ).fetchone()
    return bool(row)


def _find_open_debt(conn, *, session_id: str, task_id: str, debt_type: str, file_token: str) -> dict | None:
    row = conn.execute(
        """SELECT *
           FROM protocol_debt
           WHERE status = 'open'
             AND session_id = ?
             AND task_id = ?
             AND debt_type = ?
             AND INSTR(evidence, ?) > 0
           ORDER BY id DESC
           LIMIT 1""",
        (session_id, task_id, debt_type, file_token),
    ).fetchone()
    return dict(row) if row else None


def _task_requires_guard_ack(task: dict | None) -> bool:
    if not task:
        return False
    if not bool(task.get("guard_has_blocking")):
        return False
    return not bool(task.get("guard_acknowledged"))


def _ensure_unacknowledged_guard_blocking_debt(
    conn,
    *,
    session_id: str,
    task_id: str,
    filepath: str,
    tool_name: str,
) -> dict:
    return _ensure_protocol_debt(
        conn,
        session_id=session_id,
        task_id=task_id,
        debt_type="unacknowledged_guard_blocking",
        severity="error",
        evidence=(
            f"{tool_name} attempted on {filepath} before acknowledging blocking guard rules "
            f"for task {task_id}."
        ),
        file_token=filepath,
    )


def _ensure_protocol_debt(
    conn,
    *,
    session_id: str,
    task_id: str,
    debt_type: str,
    severity: str,
    evidence: str,
    file_token: str,
) -> dict:
    existing = _find_open_debt(
        conn,
        session_id=session_id,
        task_id=task_id,
        debt_type=debt_type,
        file_token=file_token,
    )
    if existing:
        return existing
    return create_protocol_debt(
        session_id,
        debt_type,
        severity=severity,
        task_id=task_id,
        evidence=evidence,
    )


def _task_list_field(task: dict | None, key: str) -> list:
    if not task:
        return []
    try:
        parsed = json.loads(task.get(key) or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _task_needs_workflow(task: dict | None) -> bool:
    if not task:
        return False
    if str(task.get("task_type") or "").strip() not in ACTION_TASK_TYPES:
        return False
    if len(_task_list_field(task, "plan")) > 1:
        return True
    if len(_task_list_field(task, "unknowns")) > 0:
        return True
    if len(_task_list_field(task, "files")) > 1:
        return True
    return bool(str(task.get("verification_step") or "").strip())


def _append_protocol_warning(warnings: list[dict], message: str) -> None:
    clean = append_operator_language_contract(message)
    if not clean:
        return
    if any((item.get("message") or "").strip() == clean for item in warnings):
        return
    warnings.append({"message": clean})


def _collect_protocol_warnings(conn, *, sid: str, tool_name: str) -> list[dict]:
    short_name = _short_tool_name(tool_name)
    if short_name in PROTOCOL_SKIP_TOOLS or short_name not in NON_TRIVIAL_PROTOCOL_TOOLS:
        return []

    warnings: list[dict] = []
    if not sid:
        _append_protocol_warning(
            warnings,
            render_core_prompt("hook-protocol-warning-startup-required"),
        )
        return warnings

    task = _find_any_open_task(conn, sid)
    has_guard = _session_has_guard_check(conn, sid)
    if not task:
        guard_note = (
            render_core_prompt("hook-protocol-warning-task-open-guard-note")
            if short_name in {"Read", "Bash", "Grep", "Glob"} and not has_guard
            else ""
        )
        _append_protocol_warning(
            warnings,
            render_core_prompt(
                "hook-protocol-warning-task-open-required",
                guard_note=guard_note,
            ),
        )
        _append_protocol_warning(
            warnings,
            render_core_prompt("hook-protocol-warning-heartbeat-close-evidence"),
        )
        return warnings

    task_id = str(task.get("task_id") or "").strip()
    if str(task.get("task_type") or "").strip() in ACTION_TASK_TYPES and not (task.get("opened_with_guard") or has_guard):
        _append_protocol_warning(
            warnings,
            render_core_prompt(
                "hook-protocol-warning-guard-required",
                task_id=task_id,
            ),
        )

    workflow = _find_any_open_workflow(conn, sid)
    if _task_needs_workflow(task) and not workflow:
        _append_protocol_warning(
            warnings,
            render_core_prompt(
                "hook-protocol-warning-workflow-required",
                task_id=task_id,
            ),
        )

    if str(task.get("task_type") or "").strip() in ACTION_TASK_TYPES and short_name in {"Bash", "Edit", "MultiEdit", "Write", "Delete"}:
        change_note = (
            " Si editas de verdad y no vas a usar `nexo_task_close(...)` inmediatamente, captura `nexo_change_log(...)`."
            if task.get("must_change_log")
            else ""
        )
        closeout_note = (
            " If this edit wave came from a user correction or you are leaving a blocker unresolved, "
            "include `correction_happened=true` with a reusable learning, or `followup_needed=true`, "
            "when you call `nexo_task_close(...)`."
        )
        _append_protocol_warning(
            warnings,
            render_core_prompt(
                "hook-protocol-warning-task-close-evidence",
                task_id=task_id,
                change_note=change_note,
                closeout_note=closeout_note,
            ),
        )

    return warnings


def _collect_automation_live_repo_blocks(
    conn,
    *,
    sid: str,
    tool_name: str,
    files: list[str],
) -> list[dict]:
    if not _automation_live_repo_guard_enabled():
        return []
    blocks: list[dict] = []
    for filepath in files:
        if not _is_live_repo_path(filepath):
            continue
        debt = _ensure_protocol_debt(
            conn,
            session_id=sid,
            task_id="",
            debt_type="automation_live_repo_write_blocked",
            severity="error",
            evidence=(
                f"{tool_name} attempted on {filepath} from an automation session against the live NEXO repo. "
                "Use an isolated checkout/worktree or the public contribution Draft PR flow instead."
            ),
            file_token=filepath,
        )
        blocks.append(
            {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "automation_live_repo_write_blocked",
                "reason_code": "automation_live_repo",
            }
        )
    return blocks


def _collect_runtime_core_write_blocks(
    conn,
    *,
    sid: str,
    tool_name: str,
    files: list[str],
) -> list[dict]:
    if core_writes_allowed():
        return []
    blocks: list[dict] = []
    for filepath in files:
        if not is_protected_runtime_core_path(filepath):
            continue
        debt = _ensure_protocol_debt(
            conn,
            session_id=sid,
            task_id="",
            debt_type="runtime_core_write_blocked",
            severity="error",
            evidence=(
                f"{tool_name} attempted on protected runtime core path {filepath}. "
                "Install-time core files must be changed through the source repo/release flow, "
                "not by editing ~/.nexo/core in place."
            ),
            file_token=filepath,
        )
        blocks.append(
            {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "runtime_core_write_blocked",
                "reason_code": "runtime_core_protected",
            }
        )
    return blocks


# ``_normalize_path_token`` lower-cases the path, so the regex and substring
# checks here must also be lower-case. We intentionally omit the leading
# ``/`` so that both ``/Users/.../Library/...`` and ``~/Library/...`` shapes
# match — ``_normalize_file_path`` does not expand the user home.
_LAUNCHAGENT_PLIST_RE = re.compile(
    r"library/launchagents/com\.nexo\.[^/]+\.plist$"
)
_LAUNCHAGENT_PLIST_TOKEN_RE = re.compile(
    r"library/launchagents/com\.nexo\.[^/\s]+\.plist(?:\*|$)"
)
_LAUNCHAGENT_SERVICE_RE = re.compile(r"\bcom\.nexo\.[A-Za-z0-9_.-]+\b")
_LAUNCHAGENT_3_LAYER_FLOW = (
    "Use the 3-layer schedule removal flow: launchctl unload/bootout the service, "
    "remove `# nexo: schedule_required=true` and `# nexo: cron_id=...` markers from "
    "the source script, then verify with `nexo scripts reconcile --dry-run`."
)


def _is_protected_launchagent_path(filepath: str) -> bool:
    """True when ``filepath`` resolves to a NEXO-managed LaunchAgent plist.

    Matches any absolute or tilde-prefixed path that ends with
    ``Library/LaunchAgents/com.nexo.<name>.plist``. Other plists in the same
    directory (e.g. third-party agents) are left untouched.
    """
    if not filepath:
        return False
    normalized = _normalize_file_path(filepath)
    if "library/launchagents/com.nexo." not in normalized:
        return False
    return _LAUNCHAGENT_PLIST_RE.search(normalized) is not None


def _is_protected_launchagent_token(value: str) -> bool:
    normalized = _normalize_file_path(value)
    return bool(_LAUNCHAGENT_PLIST_TOKEN_RE.search(normalized))


def _launchagent_operation_kind(command: str) -> str:
    tokens = _shell_tokens(command)
    if not tokens:
        return ""
    base = Path(tokens[0]).name.lower()
    command_text = str(command or "")
    if base == "launchctl":
        if any(token in {"unload", "bootout"} for token in tokens[1:]):
            if any(_is_protected_launchagent_token(token) for token in tokens[1:]):
                return "launchctl_plist"
            if _LAUNCHAGENT_SERVICE_RE.search(command_text):
                return "launchctl_service"
    if base in {"rm", "unlink", "mv"}:
        if any(_is_protected_launchagent_token(token) for token in tokens[1:]):
            return base
    return ""


def _collect_launchagent_operation_warnings(
    conn,
    *,
    sid: str,
    tool_name: str,
    command: str,
) -> list[dict]:
    if core_writes_allowed():
        return []
    kind = _launchagent_operation_kind(command)
    if not kind:
        return []
    debt = _ensure_protocol_debt(
        conn,
        session_id=sid,
        task_id="",
        debt_type="launchagent_plist_protected_operation",
        severity="warn",
        evidence=(
            f"{tool_name} requested {kind} on a NEXO-managed LaunchAgent. "
            f"{_LAUNCHAGENT_3_LAYER_FLOW} Command head: {str(command or '')[:180]}"
        ),
        file_token="launchagent_plist_protected",
    )
    return [
        {
            "file": "com.nexo.*.plist",
            "task_id": "",
            "debt_id": debt.get("id"),
            "debt_type": "launchagent_plist_protected_operation",
            "reason_code": "launchagent_plist_protected",
            "severity": "warn",
            "message": _LAUNCHAGENT_3_LAYER_FLOW,
            "operation_kind": kind,
        }
    ]


def _is_scheduled_personal_script(filepath: str) -> bool:
    normalized = _normalize_file_path(filepath)
    if "/.nexo/personal/scripts/" not in normalized or not normalized.endswith(".py"):
        return False
    try:
        path = Path(filepath).expanduser()
        if not path.exists() or not path.is_file():
            return False
        head = "".join(path.read_text(errors="ignore").splitlines(keepends=True)[:40])
    except Exception:
        return False
    return "# nexo: schedule_required=true" in head


def _collect_scheduled_personal_script_warnings(conn, *, sid: str, tool_name: str, files: list[str]) -> list[dict]:
    warnings: list[dict] = []
    for filepath in files:
        if not _is_scheduled_personal_script(filepath):
            continue
        debt = _ensure_protocol_debt(
            conn,
            session_id=sid,
            task_id="",
            debt_type="scheduled_personal_script_conditioned",
            severity="warn",
            evidence=(
                f"{tool_name} touched scheduled personal script {filepath}. "
                "Run nexo_guard_check and keep LaunchAgent metadata in sync before editing schedule markers."
            ),
            file_token=filepath,
        )
        warnings.append(
            {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "scheduled_personal_script_conditioned",
                "reason_code": "scheduled_personal_script_conditioned",
                "severity": "warn",
                "message": "Scheduled personal script: run nexo_guard_check and keep schedule metadata/plist in sync.",
            }
        )
    return warnings


def _collect_launchagent_write_blocks(
    conn,
    *,
    sid: str,
    tool_name: str,
    files: list[str],
) -> list[dict]:
    """Block agent-driven writes to NEXO-managed LaunchAgent plists.

    Core flows (``auto_update.py`` re-generating plists, ``nexo_migrate``,
    product controllers) set ``NEXO_CORE_WRITES_ALLOWED=1`` via
    ``product_mode.core_writes_allowed()``, which bypasses this gate. Agentic
    edits (an operator prompting Claude Code to "fix this LaunchAgent"
    manually) go through the check and are rejected with a pointer to the
    canonical surfaces: ``nexo scripts ensure-schedules``,
    ``nexo core-schedules``, or the source repo release flow.
    """
    if core_writes_allowed():
        return []
    blocks: list[dict] = []
    for filepath in files:
        if not _is_protected_launchagent_path(filepath):
            continue
        debt = _ensure_protocol_debt(
            conn,
            session_id=sid,
            task_id="",
            debt_type="launchagent_plist_write_blocked",
            severity="error",
            evidence=(
                f"{tool_name} attempted on managed LaunchAgent plist {filepath}. "
                "NEXO-managed plists must be regenerated through "
                "`nexo scripts ensure-schedules`, `nexo core-schedules`, or the "
                "source repo release flow, not edited in place."
            ),
            file_token=filepath,
        )
        blocks.append(
            {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "launchagent_plist_write_blocked",
                "reason_code": "launchagent_plist_protected",
            }
        )
    return blocks


def _read_claude_session_id_from_coordination() -> str:
    """Fallback claude_session_id when Claude Code's PreToolUse payload omits it.

    SessionStart hook writes the active Claude Code session UUID to
    ``<NEXO_HOME>/coordination/.claude-session-id``. When the PreToolUse
    payload omits ``session_id`` (observed across several Claude Code
    versions), the pre-tool guardrail would lose correlation with the open
    NEXO session and block every write with "unknown target" (learning
    #411). Reading the coordination file restores the correlation without
    relaxing fail-closed semantics: if the file is missing or empty the
    caller still blocks.
    """
    candidates = []
    nexo_home = os.environ.get("NEXO_HOME", "").strip()
    if nexo_home:
        candidates.append(Path(nexo_home).expanduser() / "coordination" / ".claude-session-id")
    candidates.append(paths.coordination_dir() / ".claude-session-id")
    candidates.append(Path.home() / ".nexo" / "coordination" / ".claude-session-id")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            value = path.read_text().strip()
        except (FileNotFoundError, OSError):
            continue
        if value:
            return value
    return ""


def process_pre_tool_event(payload: dict) -> dict:
    tool_name = _canonical_hook_tool_name(str(payload.get("tool_name", "")).strip())
    tool_input = payload.get("tool_input")
    op = _operation_kind(tool_name)
    shell_files: list[str] = []
    if tool_name == "Bash":
        shell_command = _extract_bash_command(tool_input)
        shell_op = _classify_bash_operation(shell_command)
        if shell_op in {"write", "delete"}:
            op = shell_op
            shell_files = _extract_bash_touched_files(tool_input)
    # Block K G3 prescreen: destructive Bash patterns (``git push
    # --force``, ``drop table``, ``curl | bash``, ``rm -rf``, ``dd
    # of=/dev/…``, ``chmod -R 777``) must go through the pre-tool gate
    # even when ``_classify_bash_operation`` labels them ``other``.
    # Without this prescreen the ``if op not in {'write', 'delete'}``
    # early-return below would let them slip past — exactly the failure
    # mode Francisco flagged in G3.
    if tool_name == "Bash" and op not in {"write", "delete"}:
        _g3_mode_prescreen = os.environ.get("NEXO_G3_ENFORCE_DESTRUCTIVE", "shadow").strip().lower()
        if _g3_mode_prescreen in {"shadow", "hard"}:
            _shell_cmd = _extract_bash_command(tool_input)
            if _classify_destructive_intent(_shell_cmd):
                op = "delete"  # force the main gate to keep evaluating
    # Block K G3 SSH prescreen: SSH remote-write patterns never map to
    # ``write``/``delete`` via ``_classify_bash_operation`` (they look like
    # ``other`` at shell level because the mutation happens on the remote
    # end). Without this prescreen the ``if op not in {'write', 'delete'}``
    # early-return below lets them sail past the main G3-SSH gate — the
    # exact failure mode uncovered on v7.2.0: ``ssh host "cat > file"``
    # in hard mode never emitted the deny response.
    if tool_name == "Bash" and op not in {"write", "delete"}:
        try:
            from guardian_runtime_config import resolve_guardian_flag as _resolve_ssh
            _g3_ssh_mode_prescreen = _resolve_ssh(
                "G3_SSH_ENFORCE_REMOTE_WRITE", default="shadow"
            )
        except Exception:
            _g3_ssh_mode_prescreen = os.environ.get(
                "NEXO_G3_SSH_ENFORCE_REMOTE_WRITE", "shadow"
            ).strip().lower()
        if _g3_ssh_mode_prescreen in {"shadow", "hard"}:
            _shell_cmd_ssh = _extract_bash_command(tool_input)
            if _classify_ssh_remote_write(_shell_cmd_ssh):
                op = "write"  # force the main gate to keep evaluating
    if tool_name == "Bash" and op not in {"write", "delete"}:
        _shell_cmd_launchagent = _extract_bash_command(tool_input)
        if _launchagent_operation_kind(_shell_cmd_launchagent):
            op = "delete"
    if op not in {"write", "delete"}:
        return {"ok": True, "skipped": True, "reason": "operation not blocked", "strictness": get_protocol_strictness()}

    # Plan Consolidado F0.0.4 — skip hook-level strict blocking while a
    # structure migration is in flight. NEXO_MIGRATING=1 is set by
    # nexo_migrate.run_structure_migration while it moves files and
    # re-paths the runtime. Without this bypass a legitimate migration
    # cannot edit anything without having opened task_open for each
    # individual moved file, which defeats the whole migration flow.
    if os.environ.get("NEXO_MIGRATING") == "1":
        return {
            "ok": True,
            "skipped": True,
            "reason": "structure migration in progress (NEXO_MIGRATING=1)",
            "strictness": get_protocol_strictness(),
        }

    files = _extract_touched_files(tool_input)
    if shell_files:
        existing_norms = {_normalize_file_path(item) for item in files}
        for item in shell_files:
            normalized = _normalize_file_path(item)
            if normalized and normalized not in existing_norms:
                files.append(item)
                existing_norms.add(normalized)
    strictness = get_protocol_strictness()
    conn = get_db()
    claude_sid = str(payload.get("session_id", "") or "").strip()
    if not claude_sid:
        claude_sid = _read_claude_session_id_from_coordination()
    sid = _resolve_nexo_sid(conn, claude_sid)
    open_task = _find_any_open_task(conn, sid) if sid else None
    warnings: list[dict] = []
    if tool_name == "Bash":
        launchagent_operation_warnings = _collect_launchagent_operation_warnings(
            conn,
            sid=sid,
            tool_name=tool_name,
            command=_extract_bash_command(tool_input),
        )
        if launchagent_operation_warnings:
            return {
                "ok": True,
                "session_id": sid,
                "tool_name": tool_name,
                "operation": op,
                "strictness": strictness,
                "warnings": launchagent_operation_warnings,
                "status": "warn",
            }
    warnings.extend(
        _collect_scheduled_personal_script_warnings(
            conn,
            sid=sid,
            tool_name=tool_name,
            files=files,
        )
    )
    automation_blocks = _collect_automation_live_repo_blocks(
        conn,
        sid=sid,
        tool_name=tool_name,
        files=files,
    )
    if automation_blocks:
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": automation_blocks,
            "warnings": warnings,
            "status": "blocked",
        }

    core_blocks = _collect_runtime_core_write_blocks(
        conn,
        sid=sid,
        tool_name=tool_name,
        files=files,
    )
    if core_blocks:
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": core_blocks,
            "warnings": warnings,
            "status": "blocked",
        }

    launchagent_blocks = _collect_launchagent_write_blocks(
        conn,
        sid=sid,
        tool_name=tool_name,
        files=files,
    )
    if launchagent_blocks:
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": launchagent_blocks,
            "warnings": warnings,
            "status": "blocked",
        }

    # Block K G3 (Francisco 2026-04-22): destructive commands require an
    # explicit cortex decision before they execute. Gated by
    # NEXO_G3_ENFORCE_DESTRUCTIVE (default "shadow"): shadow records a
    # warn-severity debt for observability; hard blocks the operation
    # with error severity; off disables the gate entirely.
    try:
        from guardian_runtime_config import resolve_guardian_flag
        g3_mode = resolve_guardian_flag("G3_ENFORCE_DESTRUCTIVE", default="shadow")
    except Exception:
        g3_mode = os.environ.get("NEXO_G3_ENFORCE_DESTRUCTIVE", "shadow").strip().lower()
    if g3_mode in {"shadow", "hard"} and tool_name == "Bash":
        shell_command = _extract_bash_command(tool_input)
        destructive_pattern = _classify_destructive_intent(shell_command)
        if destructive_pattern:
            if _find_recent_cortex_authorization(
                conn,
                sid=sid,
                task=open_task,
                gate_family="destructive",
                pattern_name=destructive_pattern,
            ):
                destructive_pattern = None
        if destructive_pattern:
            severity = "error" if g3_mode == "hard" else "warn"
            task_id = str((open_task or {}).get("task_id") or "").strip()
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task_id,
                debt_type="g3_destructive_command_requires_cortex",
                severity=severity,
                evidence=(
                    f"Bash command matched destructive pattern '{destructive_pattern}'. "
                    f"Command head: {shell_command[:120]}. "
                    "Run nexo_cortex_decide and record evidence before retrying."
                ),
                file_token=destructive_pattern,
            )
            if g3_mode == "hard":
                return {
                    "ok": True,
                    "session_id": sid,
                    "tool_name": tool_name,
                    "operation": op,
                    "strictness": strictness,
                    "blocks": [
                        {
                            "file": "",
                            "task_id": task_id,
                            "debt_id": debt.get("id"),
                            "debt_type": "g3_destructive_command_requires_cortex",
                            "reason_code": "g3_destructive_blocked",
                            "severity": "error",
                            "pattern": destructive_pattern,
                            "g3_mode": g3_mode,
                        }
                    ],
                    "status": "blocked",
                    "g3_mode": g3_mode,
                }

    # Block K G3 SSH wrapper (Francisco 2026-04-22 v7.2.0): remote-write
    # commands routed through ssh/rsync/scp/sftp never reach the local
    # destructive gate. Gated by NEXO_G3_SSH_ENFORCE_REMOTE_WRITE (default
    # "shadow") mirroring the destructive-local flag shape. Shadow logs a
    # warn debt row; hard blocks with error severity; off disables.
    try:
        from guardian_runtime_config import resolve_guardian_flag
        g3_ssh_mode = resolve_guardian_flag(
            "G3_SSH_ENFORCE_REMOTE_WRITE", default="shadow"
        )
    except Exception:
        g3_ssh_mode = os.environ.get(
            "NEXO_G3_SSH_ENFORCE_REMOTE_WRITE", "shadow"
        ).strip().lower()
    if g3_ssh_mode in {"shadow", "hard"} and tool_name == "Bash":
        shell_command = _extract_bash_command(tool_input)
        ssh_pattern = _classify_ssh_remote_write(shell_command)
        if ssh_pattern:
            if _find_recent_cortex_authorization(
                conn,
                sid=sid,
                task=open_task,
                gate_family="ssh",
                pattern_name=ssh_pattern,
            ):
                ssh_pattern = None
        if ssh_pattern:
            severity = "error" if g3_ssh_mode == "hard" else "warn"
            task_id = str((open_task or {}).get("task_id") or "").strip()
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task_id,
                debt_type="g3_ssh_remote_write_requires_cortex",
                severity=severity,
                evidence=(
                    f"Bash command matched SSH remote-write pattern '{ssh_pattern}'. "
                    f"Command head: {shell_command[:160]}. "
                    "Run nexo_cortex_decide (or nexo_task_open for the session) "
                    "and record evidence before retrying."
                ),
                file_token=ssh_pattern,
            )
            if g3_ssh_mode == "hard":
                return {
                    "ok": True,
                    "session_id": sid,
                    "tool_name": tool_name,
                    "operation": op,
                    "strictness": strictness,
                    "blocks": [
                        {
                            "file": "",
                            "task_id": task_id,
                            "debt_id": debt.get("id"),
                            "debt_type": "g3_ssh_remote_write_requires_cortex",
                            "reason_code": "g3_ssh_remote_write_blocked",
                            "severity": "error",
                            "pattern": ssh_pattern,
                            "g3_ssh_mode": g3_ssh_mode,
                        }
                    ],
                    "status": "blocked",
                    "g3_ssh_mode": g3_ssh_mode,
                }

    # Block K G4 (Francisco 2026-04-22): require nexo_guard_check to have
    # run within the session for every file about to be written. Opt-in
    # via NEXO_G4_ENFORCE_GUARD_CHECK (default "shadow"): ``shadow``
    # records a protocol_debt entry of severity ``warn`` but does NOT
    # block the write; ``hard`` blocks the write with severity ``error``
    # so the operator must run guard_check explicitly. Skipped entirely
    # in lenient mode or when there are no files, since the existing
    # strict-mode path already covers those cases with its own gating.
    try:
        from guardian_runtime_config import resolve_guardian_flag
        g4_mode = resolve_guardian_flag("G4_ENFORCE_GUARD_CHECK", default="shadow")
    except Exception:
        g4_mode = os.environ.get("NEXO_G4_ENFORCE_GUARD_CHECK", "shadow").strip().lower()
    if g4_mode in {"shadow", "hard"} and files and sid:
        g4_blocks: list[dict] = []
        g4_warnings: list[dict] = []
        for filepath in files:
            if _session_has_guard_for_file(conn, sid, filepath):
                continue
            severity = "error" if g4_mode == "hard" else "warn"
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="g4_guard_check_required",
                severity=severity,
                evidence=(
                    f"{tool_name} attempted on {filepath} without a prior "
                    "nexo_guard_check covering that file. "
                    "Run nexo_guard_check(files='{path}') first."
                ).format(path=filepath),
                file_token=filepath,
            )
            entry = {
                "file": filepath,
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "g4_guard_check_required",
                "reason_code": "g4_guard_check_required",
                "severity": severity,
                "g4_mode": g4_mode,
            }
            if g4_mode == "hard":
                g4_blocks.append(entry)
            else:
                g4_warnings.append(entry)
        if g4_blocks:
            return {
                "ok": True,
                "session_id": sid,
                "tool_name": tool_name,
                "operation": op,
                "strictness": strictness,
                "blocks": g4_blocks,
                "status": "blocked",
                "g4_mode": g4_mode,
            }
        # Shadow-mode warnings piggyback on the existing return path so
        # the surface stays observable without hijacking the control flow.
        if g4_warnings:
            # Store on the payload so callers that care about shadow
            # telemetry can pick it up. Do NOT return yet — continue
            # through the existing strict/lenient gates.
            # Stash under a well-known key for the post-tool hook.
            _shadow_cache = getattr(process_pre_tool_event, "_g4_shadow", None)
            if _shadow_cache is None:
                _shadow_cache = {}
                process_pre_tool_event._g4_shadow = _shadow_cache
            _shadow_cache[sid] = g4_warnings

    if strictness == "lenient":
        return {"ok": True, "skipped": True, "reason": "lenient mode", "strictness": strictness, "warnings": warnings, "status": "warn" if warnings else "clean"}

    blocks: list[dict] = []

    if not sid:
        debt = _ensure_protocol_debt(
            conn,
            session_id="",
            task_id="",
            debt_type="strict_protocol_write_without_startup",
            severity="error",
            evidence=f"{tool_name} attempted before nexo_startup/session mapping.",
            file_token="startup",
        )
        blocks.append(
            {
                "file": "",
                "task_id": "",
                "debt_id": debt.get("id"),
                "debt_type": "strict_protocol_write_without_startup",
                "reason_code": "missing_startup",
            }
        )
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": blocks,
            "warnings": warnings,
            "status": "blocked",
        }

    auto_opened_task = None
    if files:
        missing_task_files = [filepath for filepath in files if not _find_open_task_for_file(conn, sid, filepath)]
        if missing_task_files:
            auto_opened_task = _auto_open_protocol_task_for_write(
                sid=sid,
                tool_name=tool_name,
                operation=op,
                files=missing_task_files,
            )
    elif not _find_any_open_task(conn, sid):
        auto_opened_task = _auto_open_protocol_task_for_write(
            sid=sid,
            tool_name=tool_name,
            operation=op,
            files=[],
        )

    if not files:
        task = _find_any_open_task(conn, sid)
        if not task:
            severity = _strict_write_without_task_severity(sid)
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="strict_protocol_write_without_task",
                severity=severity,
                evidence=f"{tool_name} attempted without a detectable file path and without an open protocol task.",
                file_token="unknown-target",
            )
            blocks.append(
                {
                    "file": "",
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_task",
                    "reason_code": "missing_task",
                }
            )
        return {
            "ok": True,
            "session_id": sid,
            "tool_name": tool_name,
            "operation": op,
            "strictness": strictness,
            "blocks": blocks,
            "auto_opened_task": auto_opened_task,
            "warnings": warnings,
            "status": "blocked" if blocks else ("warn" if warnings else "clean"),
        }

    for filepath in files:
        task = _find_open_task_for_file(conn, sid, filepath)
        if not task:
            severity = _strict_write_without_task_severity(sid)
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="strict_protocol_write_without_task",
                severity=severity,
                evidence=f"{tool_name} attempted on {filepath} without an open protocol task for that file.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_task",
                    "reason_code": "missing_task",
                }
            )
            continue

        if _task_requires_guard_ack(task):
            _ensure_unacknowledged_guard_blocking_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                filepath=filepath,
                tool_name=tool_name,
            )
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="strict_protocol_write_without_guard_ack",
                severity="error",
                evidence=f"{tool_name} attempted on {filepath} before acknowledging guard debt for task {task['task_id']}.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "strict_protocol_write_without_guard_ack",
                    "reason_code": "guard_unacknowledged",
                }
            )
            continue

        # Check if guard_check was called for this specific file
        if not _session_has_guard_for_file(conn, sid, filepath):
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="write_without_file_guard_check",
                severity="warn",
                evidence=f"{tool_name} attempted on {filepath} without a prior guard_check covering that file.",
                file_token=filepath,
            )
            blocks.append(
                {
                    "file": filepath,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "write_without_file_guard_check",
                    "reason_code": "missing_file_guard",
                }
            )

    return {
        "ok": True,
        "session_id": sid,
        "tool_name": tool_name,
        "operation": op,
        "strictness": strictness,
        "blocks": blocks,
        "auto_opened_task": auto_opened_task,
        "warnings": warnings,
        "status": "blocked" if blocks else ("warn" if warnings else "clean"),
    }


def process_tool_event(payload: dict) -> dict:
    tool_name = str(payload.get("tool_name", "")).strip()
    op = _operation_kind(tool_name)
    tool_input = payload.get("tool_input")
    files = _extract_touched_files(tool_input)
    conn = get_db()
    sid = _resolve_nexo_sid(conn, str(payload.get("session_id", "")))
    warnings = _collect_protocol_warnings(conn, sid=sid, tool_name=tool_name)

    if op == "other" and not warnings:
        return {"ok": True, "skipped": True, "reason": "tool not monitored"}
    if not files and op in {"read", "write", "delete"} and not warnings:
        return {"ok": True, "skipped": True, "reason": "no touched files found"}
    if not sid and not warnings:
        return {"ok": True, "skipped": True, "reason": "session not mapped to nexo"}

    conditioned = _load_conditioned_learnings(conn, files) if sid else {}
    violations: list[dict] = []

    for filepath in files:
        hits = conditioned.get(filepath) or []
        if not hits:
            continue
        learning_ids = [int(row["id"]) for row in hits]
        task = _find_open_task_for_file(conn, sid, filepath)

        if op == "read":
            if not task:
                evidence = (
                    f"{tool_name} read conditioned file {filepath} linked to learning IDs {learning_ids} "
                    "without an open protocol task."
                )
                debt = _ensure_protocol_debt(
                    conn,
                    session_id=sid,
                    task_id="",
                    debt_type="conditioned_file_read_without_protocol",
                    severity="warn",
                    evidence=evidence,
                    file_token=filepath,
                )
                warnings.append(
                    {
                        "file": filepath,
                        "learning_ids": learning_ids,
                        "debt_id": debt.get("id"),
                        "debt_type": "conditioned_file_read_without_protocol",
                        "message": "Read conditioned file outside protocol task; review the file rules before any write/delete step.",
                    }
                )
            continue

        if not task:
            evidence = (
                f"{tool_name} touched conditioned file {filepath} linked to learning IDs {learning_ids} "
                f"without an open protocol task."
            )
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id="",
                debt_type="conditioned_file_touch_without_protocol",
                severity="error",
                evidence=evidence,
                file_token=filepath,
            )
            violations.append(
                {
                    "file": filepath,
                    "learning_ids": learning_ids,
                    "task_id": "",
                    "debt_id": debt.get("id"),
                    "debt_type": "conditioned_file_touch_without_protocol",
                }
            )
            continue

        if _task_requires_guard_ack(task):
            _ensure_unacknowledged_guard_blocking_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                filepath=filepath,
                tool_name=tool_name,
            )
            evidence = (
                f"{tool_name} touched conditioned file {filepath} linked to learning IDs {learning_ids} "
                f"before acknowledging blocking guard rules for task {task['task_id']}."
            )
            debt = _ensure_protocol_debt(
                conn,
                session_id=sid,
                task_id=task["task_id"],
                debt_type="conditioned_file_touch_without_guard_ack",
                severity="error",
                evidence=evidence,
                file_token=filepath,
            )
            violations.append(
                {
                    "file": filepath,
                    "learning_ids": learning_ids,
                    "task_id": task["task_id"],
                    "debt_id": debt.get("id"),
                    "debt_type": "conditioned_file_touch_without_guard_ack",
                }
            )

    return {
        "ok": True,
        "session_id": sid,
        "tool_name": tool_name,
        "operation": op,
        "warnings": warnings,
        "violations": violations,
        "status": "violation" if violations else ("warn" if warnings else "clean"),
    }


def format_hook_message(result: dict) -> str:
    if not result.get("violations") and not result.get("warnings"):
        return ""
    lines = ["NEXO DISCIPLINE:"]
    for item in result.get("warnings", []):
        if item.get("message") and not item.get("learning_ids"):
            lines.append(f"- PROTOCOL REMINDER: {item['message']}")
            continue
        if item.get("debt_id"):
            lines.append(
                f"- REVIEW FILE RULES: {item['file']} -> learnings {item['learning_ids']}. "
                f"{item['message']} (debt={item['debt_type']}, debt_id={item['debt_id']})"
            )
        else:
            lines.append(
                f"- REVIEW FILE RULES: {item['file']} -> learnings {item['learning_ids']}. "
                f"{item['message']}"
            )
    for item in result.get("violations", []):
        lines.append(
            f"- DEBT RECORDED: {item['debt_type']} on {item['file']} "
            f"(task={item['task_id'] or 'none'}, debt_id={item['debt_id']}, learnings={item['learning_ids']})"
        )
    return "\n".join(lines)


def format_pretool_block_message(result: dict) -> str:
    blocks = result.get("blocks") or []
    warnings = result.get("warnings") or []
    if not blocks and not warnings:
        return ""
    strictness = str(result.get("strictness") or "strict")
    if any(item.get("reason_code") == "automation_live_repo" for item in blocks):
        header = "NEXO AUTOMATION SAFETY BLOCKED THIS EDIT:"
    elif warnings and not blocks:
        header = "NEXO SAFETY WARNING:"
    else:
        header = (
            "NEXO LEARNING MODE BLOCKED THIS EDIT:"
            if strictness == "learning"
            else "NEXO STRICT MODE BLOCKED THIS EDIT:"
        )
    lines = [header]
    for item in warnings:
        message = item.get("message") or "Review this operation before continuing."
        debt_id = item.get("debt_id")
        suffix = f" (debt_id={debt_id})" if debt_id else ""
        lines.append(f"- WARN {item.get('reason_code') or item.get('debt_type')}: {message}{suffix}")
    for item in blocks:
        file_note = item["file"] or "(unknown target)"
        if item.get("reason_code") == "missing_startup":
            lines.append(
                f"- Start the shared-brain session first: call `nexo_startup`, then `nexo_task_open`, before editing {file_note}."
            )
        elif item.get("reason_code") == "automation_live_repo":
            lines.append(
                f"- {file_note}: automation sessions cannot write to the live NEXO repo. "
                "Use an isolated checkout/worktree or the public contribution Draft PR flow."
            )
        elif item.get("reason_code") == "runtime_core_protected":
            lines.append(
                f"- {file_note}: `~/.nexo/core` is a protected install surface. "
                "Route the change through the source repo + release/update flow instead of editing the live installed core."
            )
        elif item.get("reason_code") == "guard_unacknowledged":
            lines.append(
                f"- {file_note}: task {item['task_id']} still has blocking guard debt. Acknowledge it with `nexo_task_acknowledge_guard` before retrying."
            )
        elif item.get("reason_code") == "missing_file_guard":
            lines.append(
                f"- {file_note}: `nexo_guard_check` obligatorio antes de editar. "
                f"Run `nexo_guard_check(files='{file_note}')` first, then retry the edit."
            )
        elif strictness == "learning":
            lines.append(
                f"- {file_note}: open `nexo_task_open(task_type='edit', files=['{file_note}'])` first, then rerun the edit."
            )
        else:
            lines.append(
                f"- {file_note}: open `nexo_task_open(... files=['{file_note}'])` before editing."
            )
    return "\n".join(lines)


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    if os.environ.get("NEXO_HOOK_PHASE", "").strip().lower() == "pre":
        result = process_pre_tool_event(payload)
        message = format_pretool_block_message(result)
        if message:
            print(message, file=sys.stderr)
        return 2 if result.get("status") == "blocked" else 0
    result = process_tool_event(payload)
    message = format_hook_message(result)
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
