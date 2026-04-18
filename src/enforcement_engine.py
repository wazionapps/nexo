"""Headless Protocol Enforcement Engine for NEXO Brain.

Wraps a Claude Code subprocess with stream-json I/O, monitors tool calls,
and injects enforcement prompts when rules from tool-enforcement-map.json
are violated. Python equivalent of Desktop's enforcement-engine.js.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
import re

try:
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord, WATCHED_WRITE_TOOLS
except ImportError:  # pragma: no cover  — fallback for editable installs without sys.path tweak
    should_inject_r13 = None  # type: ignore
    ToolCallRecord = None  # type: ignore
    WATCHED_WRITE_TOOLS = frozenset()  # type: ignore

try:
    from r14_correction_learning import (
        detect_correction as _detect_correction,
        INJECTION_PROMPT_TEMPLATE as _R14_PROMPT,
        DEFAULT_WINDOW_TOOL_CALLS as _R14_WINDOW,
    )
except ImportError:  # pragma: no cover
    _detect_correction = None  # type: ignore
    _R14_PROMPT = ""  # type: ignore
    _R14_WINDOW = 3

try:
    from r16_declared_done import (
        detect_declared_done as _detect_declared_done,
        INJECTION_PROMPT_TEMPLATE as _R16_PROMPT,
    )
except ImportError:  # pragma: no cover
    _detect_declared_done = None  # type: ignore
    _R16_PROMPT = ""  # type: ignore

try:
    from r25_nora_maria_read_only import (
        should_inject_r25 as _r25_should,
        INJECTION_PROMPT_TEMPLATE as _R25_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r25_should = None  # type: ignore
    _R25_PROMPT = ""  # type: ignore

try:
    from r17_promise_debt import (
        detect_promise as _detect_promise,
        INJECTION_PROMPT_TEMPLATE as _R17_PROMPT,
        DEFAULT_WINDOW_TOOL_CALLS as _R17_WINDOW,
    )
except ImportError:  # pragma: no cover
    _detect_promise = None  # type: ignore
    _R17_PROMPT = ""  # type: ignore
    _R17_WINDOW = 2

try:
    from r20_constant_change import (
        should_inject_r20 as _r20_should,
        INJECTION_PROMPT_TEMPLATE as _R20_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r20_should = None  # type: ignore
    _R20_PROMPT = ""  # type: ignore

try:
    from r15_project_context import (
        should_inject_r15 as _r15_should,
        INJECTION_PROMPT_TEMPLATE as _R15_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r15_should = None  # type: ignore
    _R15_PROMPT = ""  # type: ignore

try:
    from r23_ssh_without_atlas import (
        should_inject_r23 as _r23_should,
        INJECTION_PROMPT_TEMPLATE as _R23_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r23_should = None  # type: ignore
    _R23_PROMPT = ""  # type: ignore

try:
    from r19_project_grep import (
        should_inject_r19 as _r19_should,
        INJECTION_PROMPT_TEMPLATE as _R19_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r19_should = None  # type: ignore
    _R19_PROMPT = ""  # type: ignore

try:
    from r21_legacy_path import (
        should_inject_r21 as _r21_should,
        INJECTION_PROMPT_TEMPLATE as _R21_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r21_should = None  # type: ignore
    _R21_PROMPT = ""  # type: ignore

try:
    from r22_personal_script import (
        should_inject_r22 as _r22_should,
        INJECTION_PROMPT_TEMPLATE as _R22_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r22_should = None  # type: ignore
    _R22_PROMPT = ""  # type: ignore

try:
    from r18_followup_autocomplete import (
        should_suggest_r18 as _r18_should,
        format_suggestions as _r18_format,
        INJECTION_PROMPT_TEMPLATE as _R18_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r18_should = None  # type: ignore
    _r18_format = None  # type: ignore
    _R18_PROMPT = ""  # type: ignore

try:
    from r24_stale_memory import (
        is_verification_tool as _r24_is_verif,
        should_flag_r24 as _r24_should,
        INJECTION_PROMPT_TEMPLATE as _R24_PROMPT,
        DEFAULT_STALE_THRESHOLD_DAYS as _R24_STALE_DAYS,
        DEFAULT_WINDOW_TOOL_CALLS as _R24_WINDOW,
    )
except ImportError:  # pragma: no cover
    _r24_is_verif = None  # type: ignore
    _r24_should = None  # type: ignore
    _R24_PROMPT = ""  # type: ignore
    _R24_STALE_DAYS = 7
    _R24_WINDOW = 3

try:
    from r23b_deploy_vhost import (
        should_inject_r23b as _r23b_should,
        INJECTION_PROMPT_TEMPLATE as _R23B_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r23b_should = None  # type: ignore
    _R23B_PROMPT = ""  # type: ignore

try:
    from r23e_force_push_main import (
        should_inject_r23e as _r23e_should,
        INJECTION_PROMPT_TEMPLATE as _R23E_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r23e_should = None  # type: ignore
    _R23E_PROMPT = ""  # type: ignore

try:
    from r23f_db_no_where import (
        should_inject_r23f as _r23f_should,
        INJECTION_PROMPT_TEMPLATE as _R23F_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r23f_should = None  # type: ignore
    _R23F_PROMPT = ""  # type: ignore

try:
    from r23l_resource_collision import (
        should_inject_r23l as _r23l_should,
        INJECTION_PROMPT_TEMPLATE as _R23L_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r23l_should = None  # type: ignore
    _R23L_PROMPT = ""  # type: ignore

try:
    from r23c_cwd_mismatch import should_inject_r23c as _r23c_should
except ImportError:  # pragma: no cover
    _r23c_should = None  # type: ignore

try:
    from r23d_chown_chmod_recursive import should_inject_r23d as _r23d_should
except ImportError:  # pragma: no cover
    _r23d_should = None  # type: ignore

try:
    from r23g_secrets_in_output import should_inject_r23g as _r23g_should
except ImportError:  # pragma: no cover
    _r23g_should = None  # type: ignore

try:
    from r23i_auto_deploy_ignored import (
        should_inject_r23i as _r23i_should,
        extract_push as _r23i_is_push,
    )
except ImportError:  # pragma: no cover
    _r23i_should = None  # type: ignore
    _r23i_is_push = None  # type: ignore

try:
    from r23k_script_duplicates_skill import should_inject_r23k as _r23k_should
except ImportError:  # pragma: no cover
    _r23k_should = None  # type: ignore

try:
    from r23m_message_duplicate import should_inject_r23m as _r23m_should
except ImportError:  # pragma: no cover
    _r23m_should = None  # type: ignore

try:
    from r23h_shebang_mismatch import should_inject_r23h as _r23h_should
except ImportError:  # pragma: no cover
    _r23h_should = None  # type: ignore

try:
    from r23j_global_install import should_inject_r23j as _r23j_should
except ImportError:  # pragma: no cover
    _r23j_should = None  # type: ignore

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
MAP_FILENAME = "tool-enforcement-map.json"
LOG_DIR = NEXO_HOME / "logs"

_logger = logging.getLogger("nexo.enforcer")
if not _logger.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(LOG_DIR / "enforcer-headless.log")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _logger.addHandler(_fh)
    _logger.setLevel(logging.INFO)


# Audit-HIGH fix: redact secret-bearing patterns before they hit
# ~/.nexo/logs/enforcer-headless.log. Prior code logged the full
# prompt and the injected text verbatim, so a `echo $TOKEN` or
# `Authorization: Bearer xyz` inside the user message or inside an
# R23g-triggered reminder ended up on disk. We redact defensively.
# (regex, replacement_template) — replacement_template may include \g<1> to
# preserve a captured prefix (e.g. `Bearer ` or `api_key=`). When the whole
# match should be hidden, replacement_template is the plain redaction marker.
_SECRET_REDACT_PATTERNS = [
    # Bearer <token>
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/]{8,}", re.IGNORECASE), r"\g<1><redacted>"),
    # OpenAI sk-proj-*, Anthropic sk-ant-api03-*, and legacy sk-/pk-.
    # The body may contain `_` and `-` in the post-2024 formats, so the
    # char class now includes both. Anchor on `sk-`/`pk-` + optional sub-
    # prefix (proj/ant/etc.) so we keep the false-positive floor low.
    (re.compile(r"\bsk-(?:[a-z]+-)?[A-Za-z0-9_\-]{20,}\b"), "sk-<redacted>"),
    (re.compile(r"\bpk-(?:[a-z]+-)?[A-Za-z0-9_\-]{20,}\b"), "pk-<redacted>"),
    # api_key=VALUE / api-key: VALUE
    (re.compile(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._\-]{16,}", re.IGNORECASE), r"\g<1><redacted>"),
    # GitHub / GitLab / Slack / Shopify token prefixes
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat|glpat|xoxb|xoxp|shpat)_[A-Za-z0-9_]{16,}\b"), r"\g<1>_<redacted>"),
    # AWS access / secret keys (AKIA..., ASIA... and 40-char base64-ish)
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16,}\b"), r"\g<1><redacted>"),
    (re.compile(r"(aws[_-]?(?:secret|access)[_-]?(?:access)?[_-]?key\s*[:=]\s*)[A-Za-z0-9/+=]{20,}", re.IGNORECASE), r"\g<1><redacted>"),
    # JWT (header.payload.signature with base64url segments).
    (re.compile(r"\bey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "<redacted-jwt>"),
    # Generic KEY=VALUE / TOKEN=VALUE / PASSWORD=VALUE / SECRET=VALUE where
    # the value is long enough to plausibly be a secret.
    (re.compile(r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASS)\s*[:=]\s*)[A-Za-z0-9._/+=\-]{12,}", re.IGNORECASE), r"\g<1><redacted>"),
    # mysql -pPASSWORD / psql … password=… — the `-p<pass>` form has no space
    # between flag and value.
    (re.compile(r"(\s-p)[^\s]{4,}"), r"\g<1><redacted>"),
    # Generic `password=`/`pwd=` in URLs, env strings, connection URIs.
    (re.compile(r"(password\s*[:=]\s*)[^\s&]+", re.IGNORECASE), r"\g<1><redacted>"),
    # Env-variable REFERENCES ($TOKEN, ${API_KEY}). The reference itself is
    # not a secret value, but echoing $TOKEN in a script signals the caller
    # is about to expand and leak it. Replace the whole reference with a
    # placeholder so the log does not encourage copy-pasting the name.
    (re.compile(r"\$\{?[A-Za-z_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASS|BEARER)[A-Za-z_]*\}?", re.IGNORECASE), "<redacted-env-ref>"),
]


def _redact_for_log(text: str, max_len: int = 200) -> str:
    """Return a log-safe truncation of `text` with secret-like tokens
    replaced by `<redacted>` or `<redacted-*>` markers. Fail-closed on
    non-string input. Applied defensively to the initial prompt + every
    enforcer injection before they reach enforcer-headless.log."""
    if not isinstance(text, str) or not text:
        return ""
    out = text
    for pat, repl in _SECRET_REDACT_PATTERNS:
        out = pat.sub(repl, out)
    if len(out) > max_len:
        out = out[:max_len] + "..."
    return out


def _load_map() -> dict | None:
    for candidate in [
        NEXO_HOME / MAP_FILENAME,
        NEXO_HOME / "brain" / MAP_FILENAME,
        Path(__file__).parent.parent / MAP_FILENAME,
    ]:
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Audit-MEDIUM fix: narrow exception + log so operators see
            # a malformed enforcement-map rather than silently falling
            # back to the packaged default.
            _logger.warning("enforcement map at %s unreadable: %s", candidate, exc)
            continue
    return None


def _normalize(name: str) -> str:
    return name.replace("mcp__nexo__", "")


class HeadlessEnforcer:
    """Monitor a Claude Code stream-json process and enforce protocol rules."""

    def __init__(self):
        self.map = _load_map()
        self.tools_called: set[str] = set()
        self.tool_call_count = 0
        self.user_message_count = 0
        self.tool_timestamps: dict[str, float] = {}
        self.msg_since_tool: dict[str, int] = {}
        self.injection_queue: list[dict] = []
        self._started_at = time.time()
        self._injections_done = 0
        # Fase 2 Capa 2 — recent tool_use records with file paths so R13
        # (pre-edit guard) and future R19/R20 rules can reason about the
        # write path that Claude is about to touch. Capped at 60 entries
        # (~30 tool calls worth of working memory for the enforcer).
        self.recent_tool_records: list = []
        self.max_recent_records = 60
        self._guardian_mode_cache: dict[str, str] = {}
        # R14 state — opened on a detected correction, counts down by one each
        # tool call. When it reaches zero without a nexo_learning_add we
        # enqueue the R14 reminder. The window guard is "3 tool calls" per
        # plan doc 1; make it overridable via the env for field tuning.
        self._r14_window_remaining = 0
        self._r14_correction_seen_for_turn = False
        # R25 — last user message is inspected for an explicit permit token
        # ("force OK", "si borra", etc). Populated by on_user_message.
        self._r25_last_user_text = ""
        # R17 promise-debt state. Opened on a detected promise, counts
        # down on each tool call.
        self._r17_window_remaining = 0
        self._r17_promise_seen_for_turn = False
        self._r17_first_tool_call_in_window = True
        # R24 stale-memory state — incremented externally via notify_
        # stale_memory_cited (e.g. from R07 when age_days >= threshold).
        # Counts down on each tool call; fires when it reaches zero
        # without a verification tool in between.
        self._r24_window_remaining = 0
        self._r24_verification_seen = False
        # R23i — the engine flips this on after a git push and
        # clears it once the next Edit/Write is evaluated. Keeping
        # it as a bool avoids carrying stale push context across
        # unrelated tool chains.
        self._r23i_recent_push = False
        # R23m — circular buffer of outbound-message sends with
        # {thread, body, ts}. Capped at 16 entries.
        self._r23m_recent_messages: list[dict] = []
        self._r23m_max_recent = 16
        # Audit-HIGH fix: engine now carries the driving session id so R16
        # can filter open protocol_tasks by SID. Without this, R16 fires
        # whenever ANY task in ANY historical session is open — permanent
        # noise post-deploy. Set by run_with_enforcement via set_session_id().
        self._session_id: str | None = None

        self._on_start: list[dict] = []
        self._on_end: list[dict] = []
        self._periodic_msg: list[dict] = []
        self._periodic_time: list[dict] = []
        self._after_tool: dict[str, list[dict]] = {}

        if self.map:
            self._build_indexes()
            _logger.info("Map v%s loaded: %d on_start, %d on_end, %d periodic_msg, %d periodic_time, %d after_tool",
                         self.map.get("version", "?"), len(self._on_start), len(self._on_end),
                         len(self._periodic_msg), len(self._periodic_time), len(self._after_tool))
        else:
            _logger.warning("No enforcement map found")

    def _build_indexes(self):
        for tool_name, tool_def in self.map.get("tools", {}).items():
            enf = tool_def.get("enforcement")
            if not enf or enf.get("level") == "none":
                continue
            for rule in enf.get("rules", []):
                rtype = rule.get("type", "")
                entry = {"tool": tool_name, "rule": rule, "enf": enf}
                if rtype == "on_session_start":
                    self._on_start.append(entry)
                elif rtype == "on_session_end":
                    self._on_end.append(entry)
                elif rtype == "periodic_by_messages":
                    self._periodic_msg.append(entry)
                elif rtype == "periodic_by_time":
                    self._periodic_time.append(entry)
                elif rtype == "after_tool":
                    for wt in rule.get("watch_tools", []):
                        self._after_tool.setdefault(wt, []).append(entry)

            for triggered in enf.get("triggers_after", []):
                self._after_tool.setdefault(tool_name, []).append({
                    "tool": triggered,
                    "rule": {"type": "after_tool"},
                    "enf": self.map["tools"].get(triggered, {}).get("enforcement", {}),
                })

    def _guardian_rule_mode(self, rule_id: str) -> str:
        """Fase 2 helper — cached guardian.json lookup with fail-closed default.

        Absent config → "shadow" (never "off") so a missing or corrupt
        guardian.json does not silently disable enforcement. Core rules
        (R13, R14, R16, R25, R30) stay at hard by default even in that path
        because guardian_config.rule_mode applies its own defence-in-depth.
        """
        cached = self._guardian_mode_cache.get(rule_id)
        if cached is not None:
            return cached
        try:
            from guardian_config import load_guardian_config, rule_mode  # type: ignore
            cfg = load_guardian_config(strict=False)
            mode = rule_mode(cfg, rule_id, default="shadow")
        except Exception as exc:  # noqa: BLE001  — fail-closed on config errors
            _logger.warning("guardian_config unavailable (%s); defaulting R-mode=shadow", exc)
            mode = "shadow"
        self._guardian_mode_cache[rule_id] = mode
        return mode

    def _extract_files(self, tool_input) -> list[str]:
        """Best-effort extraction of file paths from a stream-json tool_use input."""
        if not isinstance(tool_input, dict):
            return []
        paths: list[str] = []
        for key in ("file_path", "path", "filepath"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
        for key in ("file_paths", "paths"):
            value = tool_input.get(key)
            if isinstance(value, list):
                paths.extend(str(p) for p in value if p)
        return paths

    def _check_r13(self, tool_name: str, files: list[str]):
        """R13 pre-Edit guard (Fase 2 Capa 2).

        Fires when a write-class tool (Edit / Write / MultiEdit / NotebookEdit
        / Delete) is about to touch a path without a recent nexo_guard_check
        on that path. In strict configurations the enqueued prompt asks the
        model to call nexo_guard_check before proceeding; in shadow mode the
        event is only logged.
        """
        if should_inject_r13 is None or ToolCallRecord is None:
            return
        if tool_name not in WATCHED_WRITE_TOOLS:
            return
        mode = self._guardian_rule_mode("R13_pre_edit_guard")
        if mode == "off":
            # guardian_config validator prevents off for core rules, but a
            # quarantined non-core override could still pass off — respect it.
            return
        # The record for the current call is already appended; exclude the
        # last entry so R13 does not match itself as its own guard.
        history = self.recent_tool_records[:-1] if self.recent_tool_records else []
        tag = should_inject_r13(
            current_tool=tool_name,
            current_files=files,
            recent_calls=history,
        )
        if tag is None:
            return
        path_str = ", ".join(files) if files else "(unknown target)"
        prompt = (
            "R13 pre-Edit guard: a write-class tool (" + tool_name + ") is about "
            "to touch " + path_str + " without a recent nexo_guard_check on that "
            "path. Execute nexo_guard_check(files='" + (files[0] if files else "") + "') "
            "first, review any blocking learnings, then acknowledge via "
            "nexo_task_acknowledge_guard before the next edit. Do not produce "
            "visible text for this reminder — act on it. After handling the silent "
            "action, continue with your previous task; this reminder is silent "
            "context, not a new instruction."
        )
        if mode == "shadow":
            _logger.info("[R13 SHADOW] would inject: tag=%s files=%s", tag, files)
            return
        # soft + hard both enqueue; compliance tracking is downstream
        self._enqueue(prompt, tag, rule_id="R13_pre_edit_guard")
        _logger.info("[R13 %s] enqueued injection: tag=%s files=%s", mode.upper(), tag, files)

    def on_user_message(self, text: str, *, correction_detector=None):
        """Called when a new user message enters the stream.

        Runs the R14 correction detector. When a correction is detected we
        open a 3-tool-call window; if no nexo_learning_add appears in that
        window, _advance_r14_window enqueues the R14 reminder.

        correction_detector is an injection point for tests. In production it
        defaults to r14_correction_learning.detect_correction which routes
        through enforcement_classifier + call_model_raw. Fail-closed: a
        broken classifier keeps the window closed (no false positives).
        """
        self.user_message_count += 1
        # R15/R25 context MUST be updated regardless of R14 module availability
        # (critical fix: R14 import failure was silently killing R15/R25 too).
        self._r25_last_user_text = text or ""
        try:
            self.on_user_message_r15(text or "")
        except Exception as _r15_exc:  # noqa: BLE001
            _logger.warning("on_user_message_r15 failed: %s", _r15_exc)

        # R14 correction detection is optional — if the module is absent the
        # rule is effectively off, but R15/R25 above still fired.
        if _detect_correction is None:
            return
        detector = correction_detector if correction_detector is not None else _detect_correction
        mode = self._guardian_rule_mode("R14_correction_learning")
        if mode == "off":
            return
        try:
            is_correction = bool(detector(text or ""))
        except Exception as exc:  # noqa: BLE001  — fail-closed
            _logger.warning("R14 detector failed (%s); staying silent", exc)
            is_correction = False
        if not is_correction:
            return
        self._r14_window_remaining = _R14_WINDOW
        self._r14_correction_seen_for_turn = True
        _logger.info("[R14 %s] correction detected; window opened for %d tool calls",
                     mode.upper(), self._r14_window_remaining)

    def _advance_r14_window(self, tool_name: str):
        """Decrement the R14 window and enqueue the reminder when it expires.

        Called after each tool call. If the agent already emitted a
        nexo_learning_add inside the window we close the window silently —
        R14 is satisfied by that call. Otherwise, on reaching zero remaining
        tool calls we enqueue the reminder (soft/hard) or log it (shadow).
        """
        if not self._r14_correction_seen_for_turn:
            return
        if tool_name in {"nexo_learning_add", "mcp__nexo__nexo_learning_add"}:
            _logger.info("[R14] satisfied by learning_add; closing window")
            self._r14_window_remaining = 0
            self._r14_correction_seen_for_turn = False
            return
        self._r14_window_remaining -= 1
        if self._r14_window_remaining > 0:
            return
        mode = self._guardian_rule_mode("R14_correction_learning")
        # window exhausted — either inject or log (never bypass; R14 is CORE)
        if mode == "shadow":
            _logger.info("[R14 SHADOW] would enqueue reminder")
        else:
            self._enqueue(_R14_PROMPT, "r14:correction-window-exhausted", rule_id="R14_correction_learning")
            _logger.info("[R14 %s] enqueued correction reminder", mode.upper())
        self._r14_correction_seen_for_turn = False

    def on_assistant_text(self, text: str, *, declared_detector=None, has_open_task=None):
        """R16 — scan assistant message for done-claim with open protocol_task.

        Args:
            text: The assistant's visible text (no tool_use blocks).
            declared_detector: Injection point for tests. Defaults to
                r16_declared_done.detect_declared_done which routes through
                the enforcement classifier.
            has_open_task: Callable returning True iff there is at least one
                open protocol_task for the current session. Injection point
                for tests. Defaults to a fresh DB query via
                db.list_protocol_tasks; falls back to False on import error
                (fail-closed — never warn about closing a task that does not
                exist).

        R16 is a CORE rule so guardian_config never resolves it to "off".
        shadow → logs only. soft/hard → enqueues the reminder. Dedup 60s
        via the standard _enqueue tag guard.
        """
        if _detect_declared_done is None:
            return
        mode = self._guardian_rule_mode("R16_declared_done")
        if mode == "off":
            return
        detector = declared_detector if declared_detector is not None else _detect_declared_done
        try:
            declared = bool(detector(text or ""))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R16 detector failed (%s); staying silent", exc)
            declared = False
        if not declared:
            return
        open_check = has_open_task if has_open_task is not None else self._default_has_open_task
        try:
            open_task = bool(open_check())
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R16 open-task probe failed (%s); assuming no open task", exc)
            open_task = False
        if not open_task:
            return
        if mode == "shadow":
            _logger.info("[R16 SHADOW] would inject: declared-done with open task")
            return
        self._enqueue(_R16_PROMPT, "r16:declared-done-without-close", rule_id="R16_declared_done")
        _logger.info("[R16 %s] enqueued declared-done reminder", mode.upper())

    def _r25_context(self) -> tuple[set[str], list[str]]:
        """Resolve the (read_only_hosts, destructive_patterns) pair from
        the shared entities registry. Returns empty sets/list on any
        error so the rule fails-closed to "no data → no injection" and
        the caller never crashes on a DB hiccup.
        """
        try:
            from db import list_entities  # type: ignore
            import json as _json
            read_only: set[str] = set()
            patterns: list[str] = []
            for row in list_entities(type="host"):
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                access = str(meta.get("access_mode", "")).strip().lower()
                if access == "read_only":
                    name = str(row.get("name", "")).strip()
                    if name:
                        read_only.add(name)
                    for alias in meta.get("aliases", []) or []:
                        alias_str = str(alias).strip()
                        if alias_str:
                            read_only.add(alias_str)
            for row in list_entities(type="destructive_command"):
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                pattern = str(meta.get("pattern", "")).strip()
                if pattern:
                    patterns.append(pattern)
            return read_only, patterns
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R25 context probe failed (%s); returning empty", exc)
            return set(), []

    def _check_r25(self, tool_name: str, tool_input):
        """R25 — destructive SSH/scp/rsync towards a read-only host."""
        if _r25_should is None:
            return
        if tool_name not in {"Bash", "mcp__nexo__Bash"}:
            return
        mode = self._guardian_rule_mode("R25_nora_maria_read_only")
        if mode == "off":
            return
        cmd = ""
        if isinstance(tool_input, dict):
            raw = tool_input.get("command")
            if isinstance(raw, str):
                cmd = raw
        if not cmd:
            return
        read_only, patterns = self._r25_context()
        if not read_only or not patterns:
            return
        decision = _r25_should(
            cmd,
            read_only_hosts=read_only,
            destructive_patterns=patterns,
            last_user_text=self._r25_last_user_text,
        )
        if not decision:
            return
        prompt = _R25_PROMPT.format(host=decision["host"], matched=decision["matched_pattern"])
        if mode == "shadow":
            _logger.info("[R25 SHADOW] would inject: tag=%s host=%s", decision["tag"], decision["host"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R25_nora_maria_read_only")
        _logger.info("[R25 %s] enqueued host=%s pattern=%s", mode.upper(), decision["host"], decision["matched_pattern"])

    def on_assistant_text_r17(self, text: str, *, promise_detector=None):
        """R17 — detect future-action promises that may go unexecuted."""
        if _detect_promise is None:
            return
        mode = self._guardian_rule_mode("R17_promise_debt")
        if mode == "off":
            return
        detector = promise_detector if promise_detector is not None else _detect_promise
        try:
            is_promise = bool(detector(text or ""))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R17 detector failed (%s); staying silent", exc)
            return
        if not is_promise:
            return
        self._r17_window_remaining = _R17_WINDOW
        self._r17_promise_seen_for_turn = True
        self._r17_first_tool_call_in_window = True
        _logger.info("[R17 %s] promise detected; window open %d", mode.upper(), _R17_WINDOW)

    def _advance_r17_window(self, tool_name: str):
        if not self._r17_promise_seen_for_turn:
            return
        if self._r17_first_tool_call_in_window:
            self._r17_first_tool_call_in_window = False
            return
        self._r17_window_remaining -= 1
        if self._r17_window_remaining > 0:
            return
        mode = self._guardian_rule_mode("R17_promise_debt")
        if mode == "shadow":
            _logger.info("[R17 SHADOW] would enqueue promise-debt reminder")
        else:
            self._enqueue(_R17_PROMPT, "r17:promise-debt-open", rule_id="R17_promise_debt")
        self._r17_promise_seen_for_turn = False

    def _check_r20(self, tool_name: str, tool_input):
        """R20 — constant-change edit without grep-all-usages."""
        if _r20_should is None:
            return
        if tool_name not in {"Edit", "Write", "MultiEdit", "mcp__nexo__Edit", "mcp__nexo__Write"}:
            return
        mode = self._guardian_rule_mode("R20_constant_grep")
        if mode == "off":
            return
        if not isinstance(tool_input, dict):
            return
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        new_string = str(tool_input.get("new_string") or tool_input.get("content") or "")
        if not file_path or not new_string:
            return
        decision = _r20_should(
            file_path,
            new_string,
            self.recent_tool_records[:-1],
        )
        if not decision:
            return
        prompt = _R20_PROMPT.format(path=decision["path"])
        if mode == "shadow":
            _logger.info("[R20 SHADOW] tag=%s candidates=%s", decision["tag"], decision["candidates"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R20_constant_change")
        _logger.info("[R20 %s] enqueued path=%s", mode.upper(), decision["path"])

    def _projects_from_entities(self) -> list[dict]:
        """Fetch project entities from the shared brain for R15 matching.
        Fails-closed to [] on DB errors."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            out: list[dict] = []
            for row in list_entities(type="project"):
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                aliases = meta.get("aliases", []) if isinstance(meta, dict) else []
                out.append({"name": name, "aliases": list(aliases or [])})
            return out
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R15 project probe failed (%s)", exc)
            return []

    def _known_hosts_from_entities(self) -> set[str]:
        """Fetch host entity names + aliases for R23 matching. Empty on error."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            names: set[str] = set()
            for row in list_entities(type="host"):
                name = str(row.get("name") or "").strip()
                if name:
                    names.add(name)
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                for alias in (meta.get("aliases") or []) if isinstance(meta, dict) else []:
                    alias_str = str(alias).strip()
                    if alias_str:
                        names.add(alias_str)
            return names
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23 host probe failed (%s)", exc)
            return set()

    def on_user_message_r15(self, text: str, *, projects=None, recent_records=None):
        """R15 — project-context enforcement on the last user message."""
        if _r15_should is None:
            return
        mode = self._guardian_rule_mode("R15_project_context")
        if mode == "off":
            return
        project_list = projects if projects is not None else self._projects_from_entities()
        if not project_list:
            return
        records = recent_records if recent_records is not None else self.recent_tool_records
        decision = _r15_should(text or "", project_list, records)
        if not decision:
            return
        prompt = _R15_PROMPT.format(project=decision["project"])
        if mode == "shadow":
            _logger.info("[R15 SHADOW] would inject: project=%s", decision["project"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R15_project_context")
        _logger.info("[R15 %s] enqueued project=%s", mode.upper(), decision["project"])

    def _check_r23(self, tool_name: str, tool_input):
        """R23 — ssh/scp/rsync/curl towards an unregistered host."""
        if _r23_should is None:
            return
        if tool_name not in {"Bash", "mcp__nexo__Bash"}:
            return
        mode = self._guardian_rule_mode("R23_ssh_without_atlas")
        if mode == "off":
            return
        cmd = ""
        if isinstance(tool_input, dict):
            raw = tool_input.get("command")
            if isinstance(raw, str):
                cmd = raw
        if not cmd:
            return
        hosts = self._known_hosts_from_entities()
        decision = _r23_should(cmd, known_hosts=hosts)
        if not decision:
            return
        prompt = _R23_PROMPT.format(host=decision["host"])
        if mode == "shadow":
            _logger.info("[R23 SHADOW] would inject: host=%s", decision["host"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R23_ssh_without_atlas")
        _logger.info("[R23 %s] enqueued host=%s", mode.upper(), decision["host"])

    def _r19_projects(self) -> list[dict]:
        """Resolve projects with require_grep flag + path_patterns."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            out: list[dict] = []
            for row in list_entities(type="project"):
                name = str(row.get("name") or "").strip()
                if not name:
                    continue
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                out.append({
                    "name": name,
                    "require_grep": bool(meta.get("require_grep")),
                    "path_patterns": list(meta.get("path_patterns") or []),
                })
            return out
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R19 project probe failed (%s)", exc)
            return []

    def _r21_legacy_mappings(self) -> list[dict]:
        """Resolve legacy_path entities — their metadata carries old/canonical."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            out: list[dict] = []
            for row in list_entities(type="legacy_path"):
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                old = str(meta.get("old") or "").strip()
                canonical = str(meta.get("canonical") or "").strip()
                if old and canonical:
                    out.append({"old": old, "canonical": canonical})
            return out
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R21 legacy probe failed (%s)", exc)
            return []

    def _vhost_mappings(self) -> list[dict]:
        """Resolve vhost_mapping entities from the entity registry."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            out: list[dict] = []
            for row in list_entities(type="vhost_mapping"):
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                name = str(row.get("name") or "").strip() or meta.get("domain", "")
                out.append({"name": name, "metadata": meta})
            return out
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23b vhost probe failed (%s)", exc)
            return []

    def _db_production_markers(self) -> list[str]:
        """Return hostnames / URIs flagged env=production for R23f."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
            markers: list[str] = []
            for row in list_entities(type="db"):
                meta_raw = (row or {}).get("value") or "{}"
                try:
                    meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
                except (ValueError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    continue
                env = str(meta.get("env") or "").lower()
                if env != "production":
                    continue
                for key in ("host", "hostname", "uri", "connection_string"):
                    v = meta.get(key)
                    if isinstance(v, str) and v.strip():
                        markers.append(v.strip())
            return markers
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23f production markers probe failed (%s)", exc)
            return []

    def _all_known_entities(self) -> list[dict]:
        """Return every entity currently registered (for R23l collision check)."""
        try:
            from db import list_entities  # type: ignore
            return [
                {"name": r.get("name"), "type": r.get("type")}
                for r in list_entities()
                if r.get("name")
            ]
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23l entity probe failed (%s)", exc)
            return []

    def _check_r23b(self, tool_name: str, tool_input):
        """R23b — deploy path vs vhost mismatch (Fase D2 hard)."""
        if _r23b_should is None:
            return
        mode = self._guardian_rule_mode("R23b_deploy_vhost")
        if mode == "off":
            return
        vhosts = self._vhost_mappings()
        if not vhosts:
            return
        context_text = self._r25_last_user_text or ""
        should, prompt = _r23b_should(tool_name, tool_input, context_text, vhosts)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23b SHADOW] would inject")
            return
        self._enqueue(prompt, "R23b_deploy_vhost", rule_id="R23b_deploy_vhost")
        _logger.info("[R23b %s] enqueued", mode.upper())

    def _check_r23e(self, tool_name: str, tool_input):
        """R23e — git push --force on protected branch (Fase D2 hard)."""
        if _r23e_should is None:
            return
        mode = self._guardian_rule_mode("R23e_force_push_main")
        if mode == "off":
            return
        should, prompt = _r23e_should(tool_name, tool_input)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23e SHADOW] would inject")
            return
        self._enqueue(prompt, "R23e_force_push_main", rule_id="R23e_force_push_main")
        _logger.info("[R23e %s] enqueued", mode.upper())

    def _check_r23f(self, tool_name: str, tool_input):
        """R23f — production DB DELETE/UPDATE without WHERE (Fase D2 hard)."""
        if _r23f_should is None:
            return
        mode = self._guardian_rule_mode("R23f_db_no_where")
        if mode == "off":
            return
        markers = self._db_production_markers()
        should, prompt = _r23f_should(tool_name, tool_input, production_markers=markers or None)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23f SHADOW] would inject")
            return
        self._enqueue(prompt, "R23f_db_no_where", rule_id="R23f_db_no_where")
        _logger.info("[R23f %s] enqueued", mode.upper())

    def _check_r23l(self, tool_name: str, tool_input):
        """R23l — create resource with existing name (Fase D2 hard)."""
        if _r23l_should is None:
            return
        mode = self._guardian_rule_mode("R23l_resource_collision")
        if mode == "off":
            return
        entities = self._all_known_entities()
        if not entities:
            return
        should, prompt = _r23l_should(tool_name, tool_input, entities)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23l SHADOW] would inject")
            return
        self._enqueue(prompt, "R23l_resource_collision", rule_id="R23l_resource_collision")
        _logger.info("[R23l %s] enqueued", mode.upper())

    def _current_project_context(self) -> dict | None:
        """Best-effort resolution of the project the user is currently
        discussing. Uses the most recent user message text and entity
        type=project to produce {name, local_path, deploy}."""
        try:
            from db import list_entities  # type: ignore
            import json as _json
        except Exception:
            return None
        text = (self._r25_last_user_text or "").lower()
        if not text:
            return None
        best = None
        best_len = -1
        try:
            rows = list(list_entities(type="project"))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23c/i project probe failed (%s)", exc)
            return None
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            meta_raw = (row or {}).get("value") or "{}"
            try:
                meta = _json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw or {})
            except (ValueError, TypeError):
                meta = {}
            if not isinstance(meta, dict):
                continue
            aliases = [name.lower()] + [str(a).lower() for a in (meta.get("aliases") or [])]
            for alias in aliases:
                if not alias:
                    continue
                if alias in text and len(alias) > best_len:
                    best = {
                        "name": name,
                        "local_path": meta.get("local_path") or "",
                        "deploy": meta.get("deploy") or {},
                    }
                    best_len = len(alias)
        return best

    def _check_r23c(self, tool_name: str, tool_input):
        """R23c — destructive Bash in wrong cwd (Fase D2 soft)."""
        if _r23c_should is None:
            return
        mode = self._guardian_rule_mode("R23c_cwd_mismatch")
        if mode == "off":
            return
        current = self._current_project_context()
        if not current:
            return
        cwd = ""
        if isinstance(tool_input, dict):
            cwd = str(tool_input.get("cwd") or "")
        import os as _os
        if not cwd:
            cwd = _os.getcwd()
        should, prompt = _r23c_should(tool_name, tool_input, cwd, current)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23c SHADOW] would inject (cwd=%s)", cwd)
            return
        self._enqueue(prompt, "R23c_cwd_mismatch", rule_id="R23c_cwd_mismatch")
        _logger.info("[R23c %s] enqueued", mode.upper())

    def _check_r23d(self, tool_name: str, tool_input):
        """R23d — recursive chown/chmod without ls probe (Fase D2 soft)."""
        if _r23d_should is None:
            return
        mode = self._guardian_rule_mode("R23d_chown_chmod_recursive")
        if mode == "off":
            return
        should, prompt = _r23d_should(tool_name, tool_input, self.recent_tool_records)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23d SHADOW] would inject")
            return
        self._enqueue(prompt, "R23d_chown_chmod_recursive", rule_id="R23d_chown_chmod_recursive")
        _logger.info("[R23d %s] enqueued", mode.upper())

    def _check_r23g(self, tool_name: str, tool_input):
        """R23g — secrets dumped to output (Fase D2 soft)."""
        if _r23g_should is None:
            return
        mode = self._guardian_rule_mode("R23g_secrets_in_output")
        if mode == "off":
            return
        should, prompt = _r23g_should(tool_name, tool_input)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23g SHADOW] would inject")
            return
        self._enqueue(prompt, "R23g_secrets_in_output", rule_id="R23g_secrets_in_output")
        _logger.info("[R23g %s] enqueued", mode.upper())

    def _check_r23i(self, tool_name: str, tool_input):
        """R23i — Edit after recent git push on auto_deploy project (soft)."""
        if _r23i_should is None or _r23i_is_push is None:
            return
        mode = self._guardian_rule_mode("R23i_auto_deploy_ignored")
        # Always update push state regardless of mode so shadow telemetry
        # behaves consistently.
        if tool_name in {"Bash", "mcp__nexo__Bash"} and isinstance(tool_input, dict):
            cmd = tool_input.get("command")
            if isinstance(cmd, str) and _r23i_is_push(cmd):
                self._r23i_recent_push = True
                return
        if mode == "off":
            return
        if not self._r23i_recent_push:
            return
        current = self._current_project_context()
        if not current:
            return
        should, prompt = _r23i_should(
            tool_name,
            tool_input,
            current_project=current,
            recent_push=self._r23i_recent_push,
        )
        # Clear the flag after the first Edit/Write evaluation regardless
        # of outcome — we only want to nudge once per push→edit transition.
        if tool_name in {"Edit", "Write", "MultiEdit"}:
            self._r23i_recent_push = False
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23i SHADOW] would inject")
            return
        self._enqueue(prompt, "R23i_auto_deploy_ignored", rule_id="R23i_auto_deploy_ignored")
        _logger.info("[R23i %s] enqueued", mode.upper())

    def _check_r23k(self, tool_name: str, tool_input):
        """R23k — personal script duplicates existing skill (soft)."""
        if _r23k_should is None:
            return
        if tool_name != "nexo_personal_script_create":
            return
        mode = self._guardian_rule_mode("R23k_script_duplicates_skill")
        if mode == "off":
            return
        # Silent skill_match probe — fail-closed on any backend error.
        matches: list[dict] = []
        try:
            from plugins.skill_registry import skill_match as _skill_match  # type: ignore
            description = ""
            if isinstance(tool_input, dict):
                description = str(
                    tool_input.get("description")
                    or tool_input.get("name")
                    or ""
                )
            if description:
                matches = list(_skill_match(description) or [])
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23k skill_match probe failed (%s)", exc)
            return
        should, prompt = _r23k_should(
            tool_name,
            tool_input,
            skill_matches=matches,
        )
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23k SHADOW] would inject")
            return
        self._enqueue(prompt, "R23k_script_duplicates_skill", rule_id="R23k_script_duplicates_skill")
        _logger.info("[R23k %s] enqueued", mode.upper())

    def _check_r23m(self, tool_name: str, tool_input):
        """R23m — duplicate outbound message in 15min window (soft)."""
        if _r23m_should is None:
            return
        if tool_name not in {"nexo_send", "nexo_email_send", "gmail_send"}:
            return
        mode = self._guardian_rule_mode("R23m_message_duplicate")
        if mode == "off":
            return
        should, prompt = _r23m_should(
            tool_name,
            tool_input,
            recent_messages=self._r23m_recent_messages,
        )
        # Record this send in the ring buffer after the check so the
        # next call can detect a duplicate of this one too.
        if isinstance(tool_input, dict):
            body = str(tool_input.get("body") or tool_input.get("content") or "")
            thread = str(
                tool_input.get("to")
                or tool_input.get("thread")
                or tool_input.get("recipient")
                or ""
            )
            if body and thread:
                import time as _time
                self._r23m_recent_messages.append(
                    {"thread": thread, "body": body, "ts": _time.time()}
                )
                if len(self._r23m_recent_messages) > self._r23m_max_recent:
                    self._r23m_recent_messages.pop(0)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23m SHADOW] would inject")
            return
        self._enqueue(prompt, "R23m_message_duplicate", rule_id="R23m_message_duplicate")
        _logger.info("[R23m %s] enqueued", mode.upper())

    def _check_r23h(self, tool_name: str, tool_input):
        """R23h — script shebang vs interpreter mismatch (Fase D2 shadow)."""
        if _r23h_should is None:
            return
        mode = self._guardian_rule_mode("R23h_shebang_mismatch")
        if mode == "off":
            return
        should, prompt = _r23h_should(tool_name, tool_input)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23h SHADOW] would inject")
            return
        self._enqueue(prompt, "R23h_shebang_mismatch", rule_id="R23h_shebang_mismatch")
        _logger.info("[R23h %s] enqueued", mode.upper())

    def _check_r23j(self, tool_name: str, tool_input):
        """R23j — global package install without explicit request (shadow)."""
        if _r23j_should is None:
            return
        mode = self._guardian_rule_mode("R23j_global_install")
        if mode == "off":
            return
        user_text = self._r25_last_user_text or ""
        should, prompt = _r23j_should(tool_name, tool_input, user_text=user_text)
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R23j SHADOW] would inject")
            return
        self._enqueue(prompt, "R23j_global_install", rule_id="R23j_global_install")
        _logger.info("[R23j %s] enqueued", mode.upper())

    def _check_r19(self, tool_name: str, tool_input):
        """R19 — Write on project path with require_grep flag but no Grep."""
        if _r19_should is None:
            return
        mode = self._guardian_rule_mode("R19_project_grep")
        if mode == "off":
            return
        if not isinstance(tool_input, dict):
            return
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if not file_path:
            return
        projects = self._r19_projects()
        if not projects:
            return
        decision = _r19_should(tool_name, file_path, projects, self.recent_tool_records[:-1])
        if not decision:
            return
        prompt = _R19_PROMPT.format(project=decision["project"], path=decision["path"])
        if mode == "shadow":
            _logger.info("[R19 SHADOW] tag=%s project=%s", decision["tag"], decision["project"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R19_project_grep")
        _logger.info("[R19 %s] enqueued project=%s path=%s", mode.upper(), decision["project"], decision["path"])

    def _check_r21(self, tool_name: str, tool_input):
        """R21 — legacy path reminder."""
        if _r21_should is None:
            return
        mode = self._guardian_rule_mode("R21_legacy_path")
        if mode == "off":
            return
        if tool_name not in {"Edit", "Write", "MultiEdit", "Bash",
                             "mcp__nexo__Edit", "mcp__nexo__Write",
                             "mcp__nexo__MultiEdit", "mcp__nexo__Bash"}:
            return
        mappings = self._r21_legacy_mappings()
        if not mappings:
            return
        decision = _r21_should(tool_name, tool_input, mappings)
        if not decision:
            return
        prompt = _R21_PROMPT.format(legacy=decision["legacy"], canonical=decision["canonical"])
        if mode == "shadow":
            _logger.info("[R21 SHADOW] legacy=%s canonical=%s", decision["legacy"], decision["canonical"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R21_legacy_path")
        _logger.info("[R21 %s] enqueued legacy=%s", mode.upper(), decision["legacy"])

    def _check_r22(self, tool_name: str, tool_input):
        """R22 — personal script create without prior context probes."""
        if _r22_should is None:
            return
        mode = self._guardian_rule_mode("R22_personal_script")
        if mode == "off":
            return
        decision = _r22_should(tool_name, tool_input, self.recent_tool_records[:-1])
        if not decision:
            return
        prompt = _R22_PROMPT.format(path=decision["path"])
        if mode == "shadow":
            _logger.info("[R22 SHADOW] path=%s missing=%s", decision["path"], decision["missing"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R22_personal_script")
        _logger.info("[R22 %s] enqueued path=%s missing=%s", mode.upper(), decision["path"], decision["missing"])

    def _check_r18(self, tool_name: str, tool_input):
        """R18 — suggest followup_complete on closure-class actions."""
        if _r18_should is None or _r18_format is None:
            return
        mode = self._guardian_rule_mode("R18_followup_autocomplete")
        if mode == "off":
            return
        decision = _r18_should(tool_name, tool_input)
        if not decision:
            return
        prompt = _R18_PROMPT.format(
            count=decision["count"],
            items=_r18_format(decision["matches"]),
        )
        if mode == "shadow":
            _logger.info("[R18 SHADOW] tag=%s count=%d", decision["tag"], decision["count"])
            return
        self._enqueue(prompt, decision["tag"], rule_id="R18_followup_autocomplete")
        _logger.info("[R18 %s] enqueued %d matches", mode.upper(), decision["count"])

    def notify_stale_memory_cited(self):
        """External hook for R24 — caller (handle_cognitive_retrieve post-
        processing) flags when a stale memory entered the context. Opens
        the window; subsequent verification closes it silently, exhaustion
        triggers the reminder."""
        self._r24_window_remaining = _R24_WINDOW
        self._r24_verification_seen = False

    def _advance_r24_window(self, tool_name: str):
        if self._r24_window_remaining <= 0:
            return
        if _r24_is_verif is not None and _r24_is_verif(tool_name):
            self._r24_verification_seen = True
            return
        self._r24_window_remaining -= 1
        if self._r24_window_remaining > 0:
            return
        # Window exhausted — decide to warn
        if _r24_should is None:
            return
        should = _r24_should(True, self._r24_verification_seen, True)
        self._r24_window_remaining = 0
        if not should:
            self._r24_verification_seen = False
            return
        mode = self._guardian_rule_mode("R24_stale_memory")
        if mode == "off":
            self._r24_verification_seen = False
            return
        if mode == "shadow":
            _logger.info("[R24 SHADOW] would enqueue stale-memory reminder")
            self._r24_verification_seen = False
            return
        prompt = _R24_PROMPT.format(threshold_days=_R24_STALE_DAYS)
        self._enqueue(prompt, "r24:stale-memory-unverified", rule_id="R24_stale_memory")
        self._r24_verification_seen = False
        _logger.info("[R24 %s] enqueued stale-memory reminder", mode.upper())

    def set_session_id(self, sid: str | None) -> None:
        """Attach the driving NEXO session id so R16 filters open tasks by it."""
        self._session_id = sid or None

    def _default_has_open_task(self) -> bool:
        """Query the shared brain for an open protocol_task in THIS session.

        When the enforcer carries a session id (set by run_with_enforcement),
        we filter the open-task query by it — without this filter R16 would
        fire whenever any historical session left a task open. When the sid
        is not available we fall back to the permissive "any session" check
        with a warning; Desktop's JS twin always has a convId so this path
        is Python-headless-only.
        """
        try:
            from db import list_protocol_tasks  # type: ignore
        except Exception:
            return False
        try:
            if self._session_id:
                tasks = list_protocol_tasks(status="open", session_id=self._session_id, limit=1)
            else:
                _logger.info("[R16] session_id unset — falling back to any-session open-task check")
                tasks = list_protocol_tasks(status="open", limit=1)
            return bool(tasks)
        except TypeError:
            # Older db.list_protocol_tasks without session_id kwarg.
            try:
                tasks = list_protocol_tasks(status="open", limit=1)
                return bool(tasks)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("R16 fallback query failed: %s", exc)
                return False
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R16 query failed: %s", exc)
            return False

    def on_tool_call(self, raw_name: str, tool_input=None):
        name = _normalize(raw_name)
        self.tool_call_count += 1
        self.tools_called.add(name)
        self.tool_timestamps[name] = time.time()
        self.msg_since_tool[name] = 0
        # Track the recent tool_use with the file paths it targets so Fase 2
        # Capa 2 rules (R13, future R19/R20) can inspect the write path.
        files = self._extract_files(tool_input)
        meta: dict | None = None
        if name in {"Bash", "mcp__nexo__Bash"} and isinstance(tool_input, dict):
            _cmd = tool_input.get("command")
            if isinstance(_cmd, str):
                meta = {"command": _cmd}
        if ToolCallRecord is not None:
            record = ToolCallRecord(tool=name, ts=time.time(), files=tuple(files), meta=meta)
            self.recent_tool_records.append(record)
            if len(self.recent_tool_records) > self.max_recent_records:
                self.recent_tool_records.pop(0)
        _logger.info("TOOL_CALL #%d: %s (files=%s)", self.tool_call_count, name, files)

        # R13 pre-Edit guard (Fase 2 Capa 2) — runs BEFORE after_tool chains so
        # a missing guard_check gets surfaced even if the tool has its own
        # after_tool dependencies.
        self._check_r13(name, files)

        # R14 post-correction learning — advance the window opened on the
        # last detected correction. Must run AFTER _check_r13 so the tool
        # call is fully accounted for before we decide to inject.
        self._advance_r14_window(name)

        # R25 Nora/María read-only guard — applies when the tool is Bash
        # and the command targets a host flagged access_mode=read_only.
        self._check_r25(name, tool_input)

        # R17 — promise-debt window advance.
        self._advance_r17_window(name)

        # R20 — constant-change edit without grep.
        self._check_r20(name, tool_input)

        # R23 — ssh/curl towards an unregistered host.
        self._check_r23(name, tool_input)

        # D2 hard rules — fire BEFORE the existing R19/R21/R22 chain so a
        # collision / force-push / SQL-no-WHERE / vhost-mismatch short-
        # circuits a visible block before the engine nudges about grep.
        self._check_r23b(name, tool_input)
        self._check_r23e(name, tool_input)
        self._check_r23f(name, tool_input)
        self._check_r23l(name, tool_input)

        # D2 soft rules — run after the hard chain so the hard rules
        # short-circuit before a softer warning competes for attention.
        self._check_r23c(name, tool_input)
        self._check_r23d(name, tool_input)
        self._check_r23g(name, tool_input)
        self._check_r23i(name, tool_input)
        self._check_r23k(name, tool_input)
        self._check_r23m(name, tool_input)

        # D2 shadow rules — low-signal, rolling out carefully.
        self._check_r23h(name, tool_input)
        self._check_r23j(name, tool_input)

        # R19 — Write on require_grep project without Grep.
        self._check_r19(name, tool_input)

        # R21 — legacy path reminder.
        self._check_r21(name, tool_input)

        # R22 — personal script create without prior context probes.
        self._check_r22(name, tool_input)

        # R18 — retroactive followup-complete suggestion on closure actions.
        self._check_r18(name, tool_input)

        # R24 — stale memory window decay.
        self._advance_r24_window(name)

        for entry in self._after_tool.get(name, []):
            target = entry["tool"]
            if target not in self.tools_called:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"after:{name}->{target}", rule_id="after_tool_dependency")

    def check_periodic(self):
        for entry in self._on_start:
            tool = entry["tool"]
            threshold = entry["rule"].get("threshold", 2)
            if tool not in self.tools_called and self.tool_call_count >= threshold:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"start:{tool}", rule_id="on_session_start")

        for entry in self._periodic_msg:
            tool = entry["tool"]
            threshold = entry["rule"].get("threshold", 3)
            count = self.msg_since_tool.get(tool, self.user_message_count)
            if count >= threshold:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"periodic_msg:{tool}", rule_id="periodic_by_messages")

        for entry in self._periodic_time:
            tool = entry["tool"]
            threshold_min = entry["rule"].get("threshold", 15)
            last = self.tool_timestamps.get(tool, self._started_at)
            elapsed_min = (time.time() - last) / 60
            if elapsed_min >= threshold_min:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(prompt, f"periodic_time:{tool}", rule_id="periodic_by_time")

    def get_end_prompts(self) -> list[str]:
        prompts = []
        for entry in self._on_end:
            if entry["enf"].get("level") == "must":
                p = entry["enf"].get("session_end_inject_prompt") or entry["enf"].get("inject_prompt", "")
                if p:
                    prompts.append(p)
        _logger.info("END_PROMPTS: %d prompts to inject", len(prompts))
        return prompts

    def flush(self) -> dict | None:
        if not self.injection_queue:
            return None
        item = self.injection_queue.pop(0)
        # Learning #344 fix: reset periodic-time timer after flushing so the
        # reminder does not re-fire on the next turn. Matches the JS fix in
        # nexo-desktop/enforcement-engine.js (lines 330-341).
        tag = item.get("tag", "") if isinstance(item, dict) else ""
        if tag.startswith("periodic_time:"):
            tool = tag.split(":", 1)[1]
            self.tool_timestamps[tool] = time.time()
            _logger.info("TIMER_RESET: %s (after flush)", tool)
        return item

    # Legacy tags from Fase 1 (`after:X->Y`, `periodic_msgs:Y`, `start:Y`)
    # carry the gating tool name in their tail; the engine's original 60-second
    # window dedup only works for those. Capa 2 rules (R13 … R25 … R23m) emit
    # semantic tags like `r13:/path/to/file` or `R23e_force_push_main` that
    # have no tool suffix — for those we rely on exact in-queue tag dedup,
    # the per-rule tag collision check, and time-dedup at the call site.
    _LEGACY_TAG_PREFIXES = ("after:", "periodic_msgs:", "periodic_time:", "start:")

    def _enqueue(self, prompt: str, tag: str, rule_id: str = ""):
        """Enqueue an injection. Mirrors Desktop _enqueue for parity.

        Args:
            prompt: The reminder text to inject.
            tag: The dedup key. Must be unique per logical rule instance;
                collisions across rules silently suppress the second call.
            rule_id: Canonical rule identifier (e.g. `R13_pre_edit_guard`,
                `R23b_deploy_vhost`). Used for telemetry + guardian-mode
                lookup. If empty, telemetry records an empty rule_id and
                the efficacy metric in Fase F cannot aggregate it; callers
                MUST pass the canonical ID.
        """
        if any(q["tag"] == tag for q in self.injection_queue):
            return
        legacy = tag.startswith(self._LEGACY_TAG_PREFIXES)
        if legacy:
            tool = tag.split(":")[-1].split("->")[-1]
            last_called = self.tool_timestamps.get(tool)
            if last_called and tool in self.tools_called:
                if time.time() - last_called < 60:
                    _logger.info("DEDUP_SKIP: %s — %s called %ds ago", tag, tool, int(time.time() - last_called))
                    return
            if tool in self.tools_called and not tag.startswith("periodic_"):
                _logger.info("SKIP: %s — already called", tag)
                return
        self.injection_queue.append({"prompt": prompt, "tag": tag, "at": time.time(), "rule_id": rule_id})
        _logger.info("ENQUEUED: %s (queue size: %d rule_id=%s)", tag, len(self.injection_queue), rule_id or "?")
        # Fase F telemetry — log one "injection" event per enqueue. The
        # engine does not see the final event lifecycle (compliance / FP);
        # those are recorded by downstream hooks in session post-mortem.
        try:
            from guardian_telemetry import log_event as _telemetry_log
            canonical = rule_id or (tag.split(":")[0] if legacy else tag)
            mode = self._guardian_mode_cache.get(canonical, "")
            _telemetry_log(canonical, "injection", mode=mode, tool="")
        except Exception:  # noqa: BLE001 — telemetry is fire-and-forget
            pass

    def summary(self) -> str:
        return (f"tools_called={len(self.tools_called)} tool_calls={self.tool_call_count} "
                f"injections={self._injections_done} tools={sorted(self.tools_called)}")


def run_with_enforcement(
    cmd: list[str],
    *,
    prompt: str,
    cwd: str = "",
    env: dict | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    enforcer = HeadlessEnforcer()
    _sid_env = os.environ.get("NEXO_SID") or os.environ.get("NEXO_SESSION_ID") or ""
    if _sid_env:
        enforcer.set_session_id(_sid_env)
    _logger.info("=== SESSION START === prompt=%s timeout=%d sid=%s", _redact_for_log(prompt, 120), timeout, _sid_env or "?")

    if not enforcer.map:
        _logger.warning("No map — falling back to plain subprocess.run")
        return subprocess.run(cmd, cwd=cwd or None, capture_output=True, text=True,
                              timeout=timeout, env=env)

    stream_cmd = []
    skip_next = False
    for i, arg in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        if arg == "-p":
            skip_next = True
            continue
        if arg == "--output-format":
            skip_next = True
            continue
        stream_cmd.append(arg)

    stream_cmd.extend([
        "--print",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ])

    proc = subprocess.Popen(
        stream_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd or None,
        env=env,
        text=True,
    )

    # Forward the initial prompt to the enforcer so R14/R15 fire in the
    # headless runtime. Without this, correction detection and project-
    # context rules only run when tests invoke on_user_message directly.
    try:
        enforcer.on_user_message(prompt or "")
    except Exception as _init_exc:  # noqa: BLE001 — fail-closed
        _logger.warning("on_user_message (initial prompt) failed: %s", _init_exc)

    initial_msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}
    })
    proc.stdin.write(initial_msg + "\n")
    proc.stdin.flush()

    collected_text = []
    stderr_lines = []
    start_time = time.time()
    waiting_for_injection_response = False

    def _inject(text: str):
        nonlocal waiting_for_injection_response
        msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}
        })
        try:
            proc.stdin.write(msg + "\n")
            proc.stdin.flush()
            waiting_for_injection_response = True
            enforcer._injections_done += 1
            _logger.info("INJECTED: %s", _redact_for_log(text, 150))
        except Exception as e:
            _logger.error("INJECT_FAILED: %s", e)

    def _read_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    last_periodic_check = time.time()

    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            if time.time() - start_time > timeout:
                _logger.warning("TIMEOUT after %ds", timeout)
                proc.kill()
                break

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "assistant" and event.get("message", {}).get("content"):
                for block in event["message"]["content"]:
                    if block.get("type") == "tool_use":
                        enforcer.on_tool_call(block.get("name", ""), block.get("input"))
            elif event_type == "content_block_start":
                cb = event.get("content_block", {})
                if cb.get("type") == "tool_use":
                    enforcer.on_tool_call(cb.get("name", ""), cb.get("input"))

            if event_type == "assistant" and not waiting_for_injection_response:
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "text":
                        collected_text.append(block["text"])
                        # R16 — probe each assistant text block as it arrives
                        # so a declared-done line is caught on the same turn
                        # rather than only at session end.
                        try:
                            enforcer.on_assistant_text(block["text"])
                        except Exception as _r16_exc:  # noqa: BLE001
                            _logger.warning("on_assistant_text failed: %s", _r16_exc)
                        try:
                            enforcer.on_assistant_text_r17(block["text"])
                        except Exception as _r17_exc:  # noqa: BLE001
                            _logger.warning("on_assistant_text_r17 failed: %s", _r17_exc)

            if event_type == "result":
                if waiting_for_injection_response:
                    waiting_for_injection_response = False
                    _logger.info("INJECTION_RESPONSE received")
                    item = enforcer.flush()
                    if item:
                        _inject(item["prompt"])
                    continue

                enforcer.check_periodic()
                item = enforcer.flush()
                if item:
                    _inject(item["prompt"])
                else:
                    _logger.info("TURN_END — no pending enforcements, done")
                    break

            if time.time() - last_periodic_check > 30:
                enforcer.check_periodic()
                last_periodic_check = time.time()

    except Exception as e:
        _logger.error("EXCEPTION: %s", e)
    finally:
        end_prompts = enforcer.get_end_prompts()
        for ep in end_prompts:
            try:
                _inject(ep)
                deadline = time.time() + 15
                for raw_line in proc.stdout:
                    if time.time() > deadline:
                        _logger.warning("END_PROMPT timeout")
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("type") == "result":
                            _logger.info("END_PROMPT response received")
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception:
                break

        elapsed = time.time() - start_time
        _logger.info("=== SESSION END === duration=%.1fs %s", elapsed, enforcer.summary())

        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    stderr_thread.join(timeout=2)
    final_text = "\n".join(collected_text)
    final_stderr = "".join(stderr_lines)

    return subprocess.CompletedProcess(
        stream_cmd, proc.returncode or 0, final_text, final_stderr
    )
