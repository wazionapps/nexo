"""Headless Protocol Enforcement Engine for NEXO Brain.

Wraps a Claude Code subprocess with stream-json I/O, monitors tool calls,
and injects enforcement prompts when rules from tool-enforcement-map.json
are violated. Python equivalent of Desktop's enforcement-engine.js.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
import hashlib
from pathlib import Path
import re
import paths
from core_prompts import render_core_prompt
from operator_language import append_operator_language_contract

try:
    from r13_pre_edit_guard import should_inject_r13, ToolCallRecord, WATCHED_WRITE_TOOLS
except ImportError:  # pragma: no cover  — fallback for editable installs without sys.path tweak
    should_inject_r13 = None  # type: ignore
    ToolCallRecord = None  # type: ignore
    WATCHED_WRITE_TOOLS = frozenset()  # type: ignore

try:
    from r14_correction_learning import (
        detect_correction as _detect_correction,
        detect_accepted_correction as _detect_accepted_correction,
        INJECTION_PROMPT_TEMPLATE as _R14_PROMPT,
        ACCEPTANCE_INJECTION_PROMPT_TEMPLATE as _R14_ACCEPTANCE_PROMPT,
        DEFAULT_WINDOW_TOOL_CALLS as _R14_WINDOW,
    )
except ImportError:  # pragma: no cover
    _detect_correction = None  # type: ignore
    _detect_accepted_correction = None  # type: ignore
    _R14_PROMPT = ""  # type: ignore
    _R14_ACCEPTANCE_PROMPT = ""  # type: ignore
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
    from session_end_intent import detect_session_end_intent as _detect_session_end_intent
except ImportError:  # pragma: no cover
    _detect_session_end_intent = None  # type: ignore

try:
    from guard_verbal_ack import detect_guard_verbal_ack as _detect_guard_verbal_ack
except ImportError:  # pragma: no cover
    _detect_guard_verbal_ack = None  # type: ignore

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
    from r_catalog import should_inject_r_catalog as _r_catalog_should
except ImportError:  # pragma: no cover
    _r_catalog_should = None  # type: ignore

try:
    from r_primitive_choice import should_inject_r_primitive as _r_primitive_should
except ImportError:  # pragma: no cover
    _r_primitive_should = None  # type: ignore

try:
    from r34_identity_coherence import should_inject_r34 as _r34_should
except ImportError:  # pragma: no cover
    _r34_should = None  # type: ignore

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
    from r37_reality_preflight import (
        should_inject_r37 as _r37_should,
        INJECTION_PROMPT_TEMPLATE as _R37_PROMPT,
    )
except ImportError:  # pragma: no cover
    _r37_should = None  # type: ignore
    _R37_PROMPT = ""  # type: ignore

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
    from r23g_secrets_in_output import (
        should_inject_r23g as _r23g_should,
        has_external_sink as _r23g_has_sink,
    )
except ImportError:  # pragma: no cover
    _r23g_should = None  # type: ignore
    _r23g_has_sink = None  # type: ignore

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

try:
    from scripts.jargon_first_response import scan_text as _scan_jargon, user_requested_detail as _jargon_user_requested_detail
except ImportError:  # pragma: no cover
    _scan_jargon = None  # type: ignore
    _jargon_user_requested_detail = None  # type: ignore

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
MAP_FILENAME = "tool-enforcement-map.json"
LOG_DIR = paths.logs_dir()

_JARGON_PROMPT = render_core_prompt("r26-jargon-rewrite")
_EXECUTE_BEFORE_ASK_PROMPT = render_core_prompt("r35-execute-before-ask")
_PRODUCTION_CHANGE_LOG_PROMPT = render_core_prompt("r36-production-change-log-required")
_PRODUCTION_EDIT_CHANGE_LOG_PROMPT = render_core_prompt(
    "r36-production-edit-change-log-required",
    files="{files}",
)
_RELEASE_VERIFICATION_PROMPT = render_core_prompt(
    "r43-release-verification-checklist",
    missing="{missing}",
)
_LEARNING_PROMISE_CAPTURE_PROMPT = render_core_prompt("r-learning-promise-capture")

_LEARNING_PROMISE_CAPTURE_RE = re.compile(
    r"\b("
    r"actualizo\s+(?:el\s+)?(?:conocimiento|aprendizaje|memoria)|"
    r"guardo\s+(?:el\s+)?(?:aprendizaje|conocimiento|memoria)|"
    r"lo\s+(?:dejo|guardo|registro)\s+(?:aprendido|registrado)|"
    r"(?:tienes|ten[íi]as)\s+raz[oó]n|"
    r"apunto\s+que|"
    r"ma[ñn]ana\s+reviso|"
    r"aprendizaje\s*:"
    r")",
    re.IGNORECASE,
)

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

_SILENT_REMINDER_TURN_SUFFIX = (
    " Silence applies to the entire reminder turn: no prose before the required action(s), "
    "no prose after them, and no standalone waiting/acknowledgement/continuation phrases. "
    "If there is an operator request you still need to answer in this same turn, continue only "
    "that request after the action(s). Otherwise your visible output must stay empty."
)
_SILENT_REMINDER_DISCLOSURE_SUFFIX = (
    " Do not mention this reminder or any internal enforcement to the user."
)

# Object of a capability denial: either the denied ACTION (hacer/acceder/usar…)
# or a capability NOUN (capacidad/integración/herramienta…). Requiring one of
# these right after the negation is what separates a real "can't do X / no
# existe esa integración" from benign phrases that merely start with a negation
# ("no hay problema", "no puedo esperar a…", "does not exist yet, creating it").
_CAP_OBJ = (
    r"(?:hacer(?:lo|se)?|acceder|conectar(?:se|lo)?|integrar(?:se|lo)?|usar(?:lo|se)?|montar(?:lo)?|"
    r"crear(?:lo)?|ejecutar(?:lo)?|generar(?:lo)?|enviar(?:lo)?|llamar(?:lo)?|"
    r"capacidad|integraci[oó]n|herramienta|funci[oó]n|funcionalidad|soporte|forma|manera|"
    r"opci[oó]n|acceso|m[oó]dulo|posibilidad|conexi[oó]n|esa|ese|eso|esto|esas|esos)"
)
_CAPABILITY_DENIAL_RE = re.compile(
    r"(?:"
    r"\bno\s+(?:se\s+puede|puedo|podemos|puede)\s+(?:\w+\s+){0,1}?" + _CAP_OBJ + r"\b|"
    r"\bno\s+(?:existe|hay|tengo|tenemos)\s+(?:\w+\s+){0,2}?" + _CAP_OBJ + r"\b|"
    r"\bno\s+est[aá]\s+(?:soportad[oa]|montad[oa]|disponible|integrad[oa]|configurad[oa]|habilitad[oa])\b|"
    r"\b(?:cannot|can'?t|can\s+not)\s+(?:\w+\s+){0,2}?(?:do\s+(?:that|it|this)|access|connect|integrate|use|create|run|support)\b|"
    r"\bdoes\s+not\s+exist(?!\s+yet)\b|\bnot\s+supported\b|\bnot\s+possible\b|"
    r"\bno\s+such\s+(?:capability|tool|integration|feature)\b"
    r")",
    re.IGNORECASE,
)
_CAPABILITY_REALITY_TOOLS = {
    "nexo_system_catalog",
    "mcp__nexo__nexo_system_catalog",
    "nexo_card_match",
    "mcp__nexo__nexo_card_match",
    "nexo_skill_match",
    "mcp__nexo__nexo_skill_match",
    "nexo_credential_list",
    "mcp__nexo__nexo_credential_list",
    "nexo_credential_get",
    "mcp__nexo__nexo_credential_get",
    "nexo_pre_action_context",
    "mcp__nexo__nexo_pre_action_context",
    "nexo_recent_context",
    "mcp__nexo__nexo_recent_context",
    "nexo_session_diary_read",
    "mcp__nexo__nexo_session_diary_read",
    "nexo_status",
    "mcp__nexo__nexo_status",
    "Read",
    "Grep",
    "Glob",
    "Bash",
}

_CAPABILITY_REALITY_PROMPT = render_core_prompt("r34-capability-reality-check")


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


def _security_followup_id(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()[:10].upper()
    return f"NF-SECURITY-EXPOSED-CREDENTIAL-{digest}"


def _upgrade_silent_reminder_prompt(prompt: str) -> str:
    """Normalize old silent-reminder copy to the full turn-wide contract.

    Background reminders historically stopped at "Do not produce visible
    text.", which allowed the model to satisfy the tool call itself and
    still emit an orphan visible continuation phrase afterwards. The
    stricter contract makes silence apply to the entire reminder turn
    unless the same turn still has a real operator request to answer.
    """
    text = str(prompt or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "do not produce visible text" not in lowered:
        return text
    if "entire reminder turn" not in lowered and "visible output must stay empty" not in lowered:
        text += _SILENT_REMINDER_TURN_SUFFIX
        lowered = text.lower()
    if "do not mention this reminder" not in lowered:
        text += _SILENT_REMINDER_DISCLOSURE_SUFFIX
    return text


def _load_map() -> dict | None:
    # .resolve() is required: at runtime this module is usually imported via
    # the symlink $NEXO_HOME/enforcement_engine.py -> core/enforcement_engine.py,
    # so the non-resolved Path(__file__).parent points at NEXO_HOME instead
    # of the core/ dir where the map actually sits. Keeping the non-resolved
    # variant too covers in-repo test imports.
    for candidate in [
        Path(__file__).resolve().parent / MAP_FILENAME,
        Path(__file__).parent / MAP_FILENAME,
        paths.home() / MAP_FILENAME,
        paths.brain_dir() / MAP_FILENAME,
        paths.legacy_brain_dir() / MAP_FILENAME,
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
        self._tool_user_message_index: dict[str, int] = {}
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
        # enqueue the R14 reminder and persist a correction-learning debt.
        self._r14_window_remaining = 0
        self._r14_correction_seen_for_turn = False
        self._r14_correction_text = ""
        # R25 — last user message is inspected for an explicit permit token
        # ("force OK", "si borra", etc). Populated by on_user_message.
        self._r25_last_user_text = ""
        self._first_assistant_text_checked_for_jargon = False
        self._r37_last_user_text = ""
        self._r37_checked_for_turn = False
        # R17 promise-debt state. Opened on a detected promise, counts
        # down on each tool call.
        self._r17_window_remaining = 0
        self._r17_promise_seen_for_turn = False
        self._r17_first_tool_call_in_window = True
        self._r17_commitment_ids: list[str] = []
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
        self._production_mutation_tool_instance: int | None = None
        self._production_mutation_evidence: str = ""
        self._last_change_log_user_message_count: int | None = None
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
        # v7.6.0 parity fix: three rule types were declared in the map
        # (version 2.1) but never dispatched by Brain, only by Desktop.
        # This broke the Brain↔Desktop parity contract (fase2_schema
        # notes line) and made the map silently over-promise.
        self._before_tool: dict[str, list[dict]] = {}   # trigger_tool → [entries watching it]
        self._on_event: dict[str, list[dict]] = {}       # event_name → [entries listening]
        self._conditional: list[dict] = []               # [entries with counter-based conditions]
        # Monotonic tool-call instance counter. Used by after_tool /
        # before_tool to implement "per-instance" satisfaction instead of
        # the old "once in session" semantics that the checklist flagged
        # as broken: once target_tool was called a single time, every
        # subsequent trigger call was silently satisfied.
        self._tool_instance_counter: int = 0
        self._tool_last_instance: dict[str, int] = {}
        # Mapping trigger_instance → satisfaction required. Key: (trigger_tool, trigger_instance, target_tool).
        # The dependency is satisfied when target_tool is called with
        # tool_last_instance[target] > trigger_instance.
        self._after_tool_open_deps: list[tuple[str, int, str]] = []
        # conditional counters — tool_calls_without_target per rule.
        self._conditional_counters: dict[str, int] = {}
        # on_event grace windows — per (event_name) counter of messages
        # since event fired without the required tool being called.
        self._on_event_pending: dict[str, dict] = {}
        # v7.7 Gap 1: latch so multi_step_task_detected fires at most once
        # per task cycle. Cleared on skill_match OR task_close.
        self._multi_step_event_fired: bool = False
        self._post_close_cooldown_until: float = 0.0
        self._last_task_close_user_message_count: int = -1
        # A headless nexo_stop is terminal for the automation cycle. Once
        # seen, periodic/conditional reminders stay suppressed so cron
        # runners can reach TURN_END instead of reopening the task loop.
        self._session_stopped: bool = False
        self._first_visible_startup_gate_fired: bool = False
        self._first_visible_text_allowed: bool = False
        try:
            self._post_close_cooldown_seconds = max(
                0,
                int(os.environ.get("NEXO_ENFORCER_POST_CLOSE_COOLDOWN_SECONDS", "300")),
            )
        except Exception:
            self._post_close_cooldown_seconds = 300

        if self.map:
            self._build_indexes()
            _logger.info(
                "Map v%s loaded: %d on_start, %d on_end, %d periodic_msg, %d periodic_time, "
                "%d after_tool, %d before_tool, %d on_event (events), %d conditional",
                self.map.get("version", "?"), len(self._on_start), len(self._on_end),
                len(self._periodic_msg), len(self._periodic_time), len(self._after_tool),
                len(self._before_tool), len(self._on_event), len(self._conditional))
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
                elif rtype == "before_tool":
                    # Tool T declares a before_tool rule watching tools W1..Wn.
                    # When any Wi is about to be called, T must have been
                    # called first (since the last relevant reset). Example:
                    # nexo_guard_check has before_tool watching [Edit, Write].
                    for wt in rule.get("watch_tools", []):
                        self._before_tool.setdefault(wt, []).append(entry)
                elif rtype == "on_event":
                    # Tool T declares an on_event rule listening for event E.
                    # Hooks / the engine itself raise E via raise_event(E).
                    # When E fires, if T was not called within grace_messages,
                    # inject the reminder.
                    event = rule.get("event", "")
                    if event:
                        self._on_event.setdefault(event, []).append(entry)
                elif rtype == "conditional":
                    # Tool T must be called when a condition holds (currently
                    # "more_than_N_tool_calls_without_task_open" style). v7.6
                    # fix: the condition is evaluated at every tool call so
                    # the obligation re-opens per task, not just once in the
                    # session.
                    self._conditional.append(entry)

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

    def _guardian_rule_event(
        self,
        rule_id: str,
        event: str,
        *,
        mode: str = "",
        details: dict | None = None,
    ) -> None:
        if not rule_id or not event:
            return
        try:
            from guardian_telemetry import log_event as _telemetry_log  # type: ignore
            _telemetry_log(
                rule_id,
                event,
                mode=mode or self._guardian_mode_cache.get(rule_id, ""),
                tool="",
                session_id=self._session_id or "",
                details=details or {},
            )
        except Exception:
            pass

    def _guardian_rule_skip(
        self,
        rule_id: str,
        reason: str,
        *,
        mode: str = "",
        details: dict | None = None,
    ) -> None:
        payload = dict(details or {})
        payload.setdefault("reason", reason)
        self._guardian_rule_event(rule_id, "skipped", mode=mode, details=payload)

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
        prompt = render_core_prompt(
            "r13-pre-edit-guard-injection",
            tool_name=tool_name,
            path_str=path_str,
            first_file=(files[0] if files else ""),
        )
        if mode == "shadow":
            _logger.info("[R13 SHADOW] would inject: tag=%s files=%s", tag, files)
            return
        # soft + hard both enqueue; compliance tracking is downstream
        self._enqueue(prompt, tag, rule_id="R13_pre_edit_guard")
        _logger.info("[R13 %s] enqueued injection: tag=%s files=%s", mode.upper(), tag, files)

    def on_user_message(self, text: str, *, correction_detector=None, session_end_detector=None):
        """Called when a new user message enters the stream.

        Runs the R14 correction detector. When a correction is detected we
        open a 3-tool-call window; if no nexo_learning_add appears in that
        window, _advance_r14_window enqueues the R14 reminder.

        correction_detector is an injection point for tests. In production it
        defaults to r14_correction_learning.detect_correction which routes
        through semantic_router. Fail-closed: a
        broken classifier keeps the window closed (no false positives).
        """
        self.user_message_count += 1
        # R15/R25 context MUST be updated regardless of R14 module availability
        # (critical fix: R14 import failure was silently killing R15/R25 too).
        self._r25_last_user_text = text or ""
        self._first_assistant_text_checked_for_jargon = False
        self._r37_last_user_text = text or ""
        self._r37_checked_for_turn = False
        try:
            self.on_user_message_r15(text or "")
        except Exception as _r15_exc:  # noqa: BLE001
            _logger.warning("on_user_message_r15 failed: %s", _r15_exc)

        try:
            self._maybe_acknowledge_guard_from_user_text(text or "")
        except Exception as _guard_ack_exc:  # noqa: BLE001
            _logger.warning("guard verbal-ack bridge failed: %s", _guard_ack_exc)

        if self._run_session_end_detection(text or "", detector=session_end_detector):
            return

        # Session-start and periodic-by-message rules must be evaluated on the
        # same user turn that increments the counters, otherwise headless
        # sessions start cold and only inject startup/heartbeat reminders after
        # the first assistant turn finishes.
        self.check_periodic()

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
        self._r14_correction_text = text or ""
        _logger.info("[R14 %s] correction detected; window opened for %d tool calls",
                     mode.upper(), self._r14_window_remaining)
        # v7.7 Gap 7.2 — wire on_event so the map's
        # `user_correction_without_learning` rule fires in the live
        # stream. grace_messages was set to 0 in map v2.2 so the
        # learning reminder must surface the same turn.
        try:
            self.raise_event("user_correction_without_learning", {"text_hash": hash(text or "")})
        except Exception:
            pass  # telemetry-style; never crash R14 detection

    def _run_session_end_detection(self, text: str, *, detector=None) -> bool:
        if not self._on_end:
            return False
        if _detect_session_end_intent is None and detector is None:
            return False
        probe = detector if detector is not None else _detect_session_end_intent
        if probe is None:
            return False
        try:
            should_close = bool(probe(text))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("session-end detector failed (%s); staying silent", exc)
            return False
        if not should_close:
            return False
        enqueued = False
        for entry in self._on_end:
            if entry["enf"].get("level") != "must":
                continue
            prompt = entry["enf"].get("session_end_inject_prompt") or entry["enf"].get("inject_prompt", "")
            if not prompt:
                continue
            before = len(self.injection_queue)
            self._enqueue(prompt, f"session-end-intent:{entry['tool']}", rule_id="on_session_end")
            if len(self.injection_queue) > before:
                enqueued = True
        if enqueued:
            _logger.info("implicit session-end intent detected; queued end-of-session prompts")
        return enqueued

    def _decode_task_files(self, raw_files) -> list[str]:
        if raw_files is None:
            return []
        value = raw_files
        if isinstance(raw_files, str):
            try:
                value = json.loads(raw_files)
            except Exception:
                value = [raw_files]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _single_guard_pending_task(self) -> dict | None:
        if not self._session_id:
            return None
        try:
            from db import get_db  # type: ignore
        except Exception:
            return None
        try:
            rows = get_db().execute(
                """SELECT task_id, session_id, goal, task_type, files, guard_summary
                   FROM protocol_tasks
                   WHERE session_id = ?
                     AND status = 'open'
                     AND guard_has_blocking = 1
                     AND guard_acknowledged = 0
                   ORDER BY opened_at DESC, task_id DESC
                   LIMIT 2""",
                (self._session_id,),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("guard verbal-ack task probe failed (%s)", exc)
            return None
        if len(rows) != 1:
            return None
        task = dict(rows[0])
        if str(task.get("task_type") or "") not in {"edit", "execute", "delegate"}:
            return None
        files = self._decode_task_files(task.get("files"))
        if len(files) != 1:
            return None
        task["decoded_files"] = files
        task["single_file"] = files[0]
        return task

    def _maybe_acknowledge_guard_from_user_text(self, text: str, *, detector=None) -> bool:
        message = (text or "").strip()
        if not message:
            return False
        probe = detector if detector is not None else _detect_guard_verbal_ack
        if probe is None:
            return False
        task = self._single_guard_pending_task()
        if not task:
            return False
        try:
            approved = bool(
                probe(
                    message,
                    task_type=str(task.get("task_type") or ""),
                    goal=str(task.get("goal") or ""),
                    file_path=str(task.get("single_file") or ""),
                    guard_summary=str(task.get("guard_summary") or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("guard verbal-ack detector failed (%s); staying silent", exc)
            return False
        if not approved:
            return False
        try:
            from db import resolve_protocol_debts, set_protocol_task_guard_acknowledged  # type: ignore
        except Exception as exc:  # noqa: BLE001
            _logger.warning("guard verbal-ack DB bridge unavailable (%s)", exc)
            return False
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return False
        set_protocol_task_guard_acknowledged(task_id, acknowledged=True)
        resolved = resolve_protocol_debts(
            task_id=task_id,
            debt_types=["unacknowledged_guard_blocking"],
            resolution=f"Explicit user approval detected in-session: {message[:240]}",
        )
        _logger.info(
            "guard verbal-ack auto-applied sid=%s task=%s file=%s resolved=%s",
            self._session_id,
            task_id,
            task.get("single_file") or "",
            resolved,
        )
        return True

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
            self._r14_correction_text = ""
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
        if self._session_id:
            try:
                from db import create_protocol_debt, list_protocol_debts, record_session_correction_requirement  # type: ignore

                record_session_correction_requirement(
                    self._session_id,
                    self._r14_correction_text,
                    source="r14_window_exhausted",
                )
                existing = list_protocol_debts(
                    status="open",
                    session_id=self._session_id,
                    debt_type="missing_learning_after_correction",
                    limit=1,
                )
                if not existing:
                    create_protocol_debt(
                        self._session_id,
                        "missing_learning_after_correction",
                        severity="error",
                        evidence=(
                            "R14 detected a user correction and the 2-tool-call "
                            "learning window expired without nexo_learning_add."
                        ),
                    )
            except Exception:
                pass
        self._r14_correction_seen_for_turn = False
        self._r14_correction_text = ""

    def _learning_add_seen_for_current_turn(self) -> bool:
        current_turn = int(self.user_message_count or 0)
        return any(
            self._tool_user_message_index.get(tool, -1) >= current_turn
            for tool in ("nexo_learning_add", "mcp__nexo__nexo_learning_add")
        )

    def _record_missing_learning_after_accepted_correction(self) -> None:
        if not self._session_id:
            return
        try:
            from db import create_protocol_debt, list_protocol_debts, record_session_correction_requirement  # type: ignore

            record_session_correction_requirement(
                self._session_id,
                self._r14_correction_text,
                source="r14_accepted_correction_gate",
            )
            existing = list_protocol_debts(
                status="open",
                session_id=self._session_id,
                debt_type="missing_learning_after_correction",
                limit=1,
            )
            if not existing:
                create_protocol_debt(
                    self._session_id,
                    "missing_learning_after_correction",
                    severity="error",
                    evidence=(
                        "R14 detected that the assistant accepted a user correction "
                        "before nexo_learning_add was called."
                    ),
                )
        except Exception:
            pass

    def _check_r14_accepted_correction(self, text: str, *, accepted_detector=None) -> None:
        if not self._r14_correction_seen_for_turn:
            return
        if self._learning_add_seen_for_current_turn():
            return
        detector = accepted_detector if accepted_detector is not None else _detect_accepted_correction
        if detector is None:
            return
        mode = self._guardian_rule_mode("R14_correction_learning")
        if mode == "off":
            return
        try:
            accepted = bool(
                detector(
                    text or "",
                    correction_text=self._r14_correction_text,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R14 accepted-correction detector failed (%s); staying silent", exc)
            accepted = False
        if not accepted:
            return
        if mode == "shadow":
            _logger.info("[R14 SHADOW] would block accepted correction without learning_add")
            return
        self._enqueue(
            _R14_ACCEPTANCE_PROMPT,
            "r14:accepted-correction-without-learning",
            rule_id="R14_correction_learning",
        )
        self._record_missing_learning_after_accepted_correction()
        _logger.info("[R14 %s] enqueued accepted-correction learning gate", mode.upper())

    def _check_learning_promise_capture(self, text: str) -> None:
        if not _LEARNING_PROMISE_CAPTURE_RE.search(text or ""):
            return
        if self._learning_add_seen_for_current_turn():
            return
        mode = self._guardian_rule_mode("R14_correction_learning")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R14 SHADOW] would require promised learning_add before next turn")
            return
        self._enqueue(
            _LEARNING_PROMISE_CAPTURE_PROMPT,
            "r14:learning-promise-without-learning-add",
            rule_id="R14_correction_learning",
        )
        self._record_missing_learning_after_accepted_correction()
        _logger.info("[R14 %s] enqueued promised learning_add gate", mode.upper())

    def on_assistant_text(self, text: str, *, declared_detector=None, has_open_task=None, accepted_correction_detector=None):
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
        if not self._first_assistant_text_checked_for_jargon:
            self._first_assistant_text_checked_for_jargon = True
            self._check_jargon_text(text, tag="r26:first-response-jargon")
        self._check_reality_preflight_before_answer()
        self._check_execute_before_ask(text)
        self._check_capability_denial_requires_reality(text)
        self._check_r14_accepted_correction(
            text,
            accepted_detector=accepted_correction_detector,
        )
        self._check_learning_promise_capture(text)
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
        # v7.7 Gap 7.2 — fire the on_event rule wired to task_close so
        # the map's `done_claimed_with_open_task` trigger actually runs
        # from the live stream, not only via test harnesses.
        try:
            self.raise_event("done_claimed_with_open_task", {"source": "R16"})
        except Exception:
            pass

    def should_block_first_visible_text(self) -> bool:
        """Fail closed before the first visible answer when startup context is missing."""
        if self._first_visible_text_allowed:
            return False
        if self.user_message_count <= 0:
            self._first_visible_text_allowed = True
            return False

        current_turn = int(self.user_message_count or 0)
        has_startup = "nexo_startup" in self.tools_called
        continuity_tools = {
            "nexo_smart_startup",
            "nexo_session_diary_read",
            "nexo_reminders",
            "nexo_checkpoint_read",
        }
        has_continuity = bool(self.tools_called.intersection(continuity_tools))
        heartbeat_turn = max(
            self._tool_user_message_index.get("nexo_heartbeat", -1),
            self._tool_user_message_index.get("nexo_task_open", -1),
        )
        has_turn_heartbeat = heartbeat_turn >= current_turn

        missing = []
        if not has_startup:
            missing.append("nexo_startup")
        if not has_continuity:
            missing.append("continuidad minima")
        if not has_turn_heartbeat:
            missing.append("nexo_heartbeat")
        if not missing:
            self._first_visible_text_allowed = True
            return False
        if self._first_visible_startup_gate_fired:
            return True

        prompt = (
            "Before any visible answer, register the session, load minimal continuity, "
            "and associate the current user message with a heartbeat. Missing: "
            f"{', '.join(missing)}. Execute the required NEXO tool calls now. "
            "Do not produce visible text for this reminder."
        )
        self._enqueue(prompt, "first-visible-startup-heartbeat-gate", rule_id="R38_first_visible_startup_gate")
        self._first_visible_startup_gate_fired = True
        return True

    def _check_capability_denial_requires_reality(self, text: str):
        """Block unsupported capability denials until a live source was checked."""
        if not text or not _CAPABILITY_DENIAL_RE.search(text):
            return
        if self.tools_called.intersection(_CAPABILITY_REALITY_TOOLS):
            return
        mode = self._guardian_rule_mode("R34_capability_reality_check")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R34 SHADOW] would inject capability reality check")
            return
        self._enqueue(
            _CAPABILITY_REALITY_PROMPT,
            "r34:capability-denial-without-reality-check",
            rule_id="R34_capability_reality_check",
        )
        _logger.info("[R34 %s] enqueued capability reality check", mode.upper())

    def _check_reality_preflight_before_answer(self) -> None:
        if self._r37_checked_for_turn:
            return
        self._r37_checked_for_turn = True
        if _r37_should is None:
            return
        try:
            should = bool(_r37_should(self._r37_last_user_text or "", self.recent_tool_records))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R37 reality preflight failed (%s); staying silent", exc)
            return
        if not should:
            return
        mode = self._guardian_rule_mode("R37_reality_preflight")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R37 SHADOW] would enqueue reality preflight")
            return
        self._enqueue(
            _R37_PROMPT,
            "r37:reality-preflight",
            rule_id="R37_reality_preflight",
        )
        _logger.info("[R37 %s] enqueued reality preflight", mode.upper())

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
        self._record_r17_commitment(text or "")
        _logger.info("[R17 %s] promise detected; window open %d", mode.upper(), _R17_WINDOW)

    def _record_r17_commitment(self, text: str) -> None:
        statement = (text or "").strip()
        if not statement:
            return
        try:
            from db import create_commitment, record_memory_event
        except Exception:
            return
        source_id = hashlib.sha1(
            f"{self._session_id or ''}|{statement[:800]}".encode("utf-8", errors="ignore"),
            usedforsecurity=False,
        ).hexdigest()[:24]
        memory_event_uid = ""
        try:
            event = record_memory_event(
                event_type="assistant_promise_detected",
                source_type="commitment",
                source_id=source_id,
                session_id=self._session_id or "",
                actor=self._session_id or "nexo",
                metadata={"statement": statement[:800], "rule_id": "R17_promise_debt"},
                raw_ref=f"commitment:{source_id}",
                confidence=0.72,
                idempotency_key=f"r17-commitment:{source_id}",
            )
            memory_event_uid = str(event.get("event_uid") or "") if isinstance(event, dict) else ""
        except Exception as exc:  # noqa: BLE001
            _logger.debug("R17 commitment memory event skipped: %s", exc)
        try:
            result = create_commitment(
                statement=statement,
                source_type="assistant_text",
                source_id=source_id,
                memory_event_uid=memory_event_uid,
                session_id=self._session_id or "",
                owner="agent",
                status="active",
                confidence=0.72,
                evidence_ref=f"memory_event:{memory_event_uid}" if memory_event_uid else "",
                metadata={"rule_id": "R17_promise_debt"},
            )
            commitment_id = str(result.get("id") or "")
            if commitment_id and commitment_id not in self._r17_commitment_ids:
                self._r17_commitment_ids.append(commitment_id)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("R17 commitment create skipped: %s", exc)

    def _mark_r17_commitments_in_progress(self, tool_name: str) -> None:
        if not self._r17_commitment_ids:
            return
        try:
            from db import update_commitment_status
        except Exception:
            return
        for commitment_id in list(self._r17_commitment_ids)[-5:]:
            try:
                update_commitment_status(
                    commitment_id,
                    status="in_progress",
                    evidence_ref=f"tool:{tool_name}",
                    metadata={"last_tool_seen": tool_name},
                )
            except Exception as exc:  # noqa: BLE001
                _logger.debug("R17 commitment progress update skipped: %s", exc)

    def _resolve_r17_commitments_from_task_close(self, tool_input) -> None:
        payload = tool_input if isinstance(tool_input, dict) else {}
        sid = str(payload.get("sid") or self._session_id or "")
        task_id = str(payload.get("task_id") or "")
        evidence_text = " ".join(
            str(payload.get(field) or "")
            for field in ("evidence", "summary", "change_summary", "outcome_notes", "result", "verification")
        ).strip()
        if not sid or not evidence_text:
            return
        try:
            from db import resolve_matching_commitments
        except Exception:
            return
        try:
            resolve_matching_commitments(
                session_id=sid,
                evidence_text=evidence_text,
                action_ref_type="protocol_task" if task_id else "",
                action_ref_id=task_id,
                evidence_ref=f"protocol_task:{task_id}" if task_id else "nexo_task_close",
                status="fulfilled",
            )
        except Exception as exc:  # noqa: BLE001
            _logger.debug("R17 commitment resolution skipped: %s", exc)

    def _check_jargon_text(self, text: str, *, tag: str) -> None:
        if _scan_jargon is None:
            return
        clean = (text or "").strip()
        if not clean:
            return
        if _jargon_user_requested_detail is not None and _jargon_user_requested_detail(self._r25_last_user_text or ""):
            return
        try:
            matches = _scan_jargon(clean)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("jargon scan failed (%s); staying silent", exc)
            return
        if not matches:
            return
        mode = self._guardian_rule_mode("R26_jargon_filter")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R26 SHADOW] would inject jargon rewrite: %s", [m.get("token") for m in matches[:5]])
            return
        self._enqueue(_JARGON_PROMPT, tag, rule_id="R26_jargon_filter")

    def _check_execute_before_ask(self, text: str) -> None:
        user = (self._r25_last_user_text or "").lower()
        reply = (text or "").lower()
        if not user or not reply:
            return
        imperative = re.search(r"\b(hazlo|mira|reactiva|ejecuta|arregla|corrige|aplica|dale|haz|revisa|comprueba)\b", user)
        asking = (
            "?" in reply
            or "tengo dos decisiones" in reply
            or "elige" in reply
            or "quieres que" in reply
            or "confirmas" in reply
            or "necesito que decidas" in reply
        )
        hard_boundary = re.search(
            r"\b(credencial|contraseñ|password|pago|payment|destructiv|irreversible|borrar|delete|revocar|rotar|publicar|publish|dns|legal)\b",
            user + "\n" + reply,
        )
        if not imperative or not asking or hard_boundary:
            return
        mode = self._guardian_rule_mode("R35_execute_before_ask")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R35 SHADOW] would inject execute-before-ask")
            return
        self._enqueue(_EXECUTE_BEFORE_ASK_PROMPT, "r35:execute-before-ask", rule_id="R35_execute_before_ask")

    def _production_mutation_summary(self, tool_name: str, tool_input) -> str:
        if tool_name not in {"Bash", "mcp__nexo__Bash"} or not isinstance(tool_input, dict):
            return ""
        cmd = str(tool_input.get("command") or "")
        if not cmd:
            return ""
        patterns = (
            r"\bgit\s+push\b(?!.*--dry-run)(?=.*\b(?:main|master|release|stable)\b)",
            r"\bgit\s+push\b(?!.*--dry-run)(?=.*\bauto[-_\s]?deploy\b)",
            r"\b(?:rsync|scp)\b(?!.*--dry-run).+\s+\S+:\S+",
            r"\b(?:rsync|scp)\b(?!.*--dry-run).+\s+\S+:(?:/[^ \n\r;]*)(?:public_html|httpdocs|www|webroot)\b",
            r"\bssh\b[^'\"]*['\"][^'\"]*(?:sed\s+-i|tee\s+|>\s*\S|>>\s*\S|rm\s+-|mv\s+|cp\s+)[^'\"]*['\"]",
            r"\bssh\b[^'\"]*['\"][^'\"]*(?:sed\s+-i|tee\s+|>\s*\S|>>\s*\S|rm\s+-|mv\s+|cp\s+)[^'\"]*(?:public_html|httpdocs|/var/www|/opt/)[^'\"]*['\"]",
            r"\bnpm\s+publish\b",
            r"\bupload-release\.sh\b",
            r"\bfirebase\s+deploy\b(?!.*--dry-run)",
            r"\bdocker\s+push\b(?!.*--dry-run)",
            r"\bkubectl\s+(?:apply|rollout\s+restart|set\s+image)\b(?!.*--dry-run)",
            r"\bterraform\s+apply\b(?!.*(?:-destroy|--destroy|--dry-run))",
            r"\bshopify\s+theme\s+push\b(?!.*--dry-run)",
            r"\bvercel\s+(?:deploy\s+)?--prod\b(?!.*--dry-run)",
            r"\bnetlify\s+deploy\b(?!.*--dry-run)(?=.*(?:--prod|--prod-if-unlocked|--alias|--site)\b)",
            r"\baz\s+(?:webapp|functionapp)\b(?=.*\b(?:deploy|deployment|config-zip|up)\b)(?!.*--dry-run)",
            r"\bgcloud\s+builds\s+(?:submit|triggers\s+run)\b",
            r"\bgcloud\s+run\s+(?:deploy|services\s+update|jobs\s+deploy|jobs\s+update)\b",
            r"\bgcloud\s+dns\s+record-sets\s+transaction\s+execute\b",
            r"\b(?:alembic\s+upgrade|prisma\s+migrate\s+deploy|sequelize\s+db:migrate|knex\s+migrate:latest|rails\s+db:migrate|python(?:3)?\s+manage\.py\s+migrate|php\s+artisan\s+migrate)\b",
            r"\b(?:whmapi1|uapi|cpapi2)\b",
            r"\b(?:cloudflare|cfcli)\b.*\b(?:dns|record)\b.*\b(?:create|delete|update|patch|put|post)\b",
            r"\bcurl\b(?=.*api\.cloudflare\.com/client/v4/zones/.*/dns_records)(?=.*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b)",
        )
        for pattern in patterns:
            if re.search(pattern, cmd, re.IGNORECASE | re.DOTALL):
                return cmd[:300]
        return ""

    def _production_edit_files(self, tool_name: str, files: list[str]) -> list[str]:
        if tool_name not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
            return []
        matches: list[str] = []
        for file_path in files:
            path = str(file_path or "").strip()
            if not path:
                continue
            posix = path.replace("\\", "/").lower()
            if "/.nexo/runtime/" in posix:
                matches.append(path)
                continue
            if "vicshop" in posix or "canarirural" in posix:
                matches.append(path)
                continue
            if "/nexo-desktop/" in posix and (
                posix.endswith("/main.js") or "/lib/" in posix or "/scripts/" in posix
            ):
                matches.append(path)
        return matches

    def _change_log_called_recently(self, *, within_turns: int = 5) -> bool:
        last = self._last_change_log_user_message_count
        if last is None:
            last = self._tool_user_message_index.get("nexo_change_log")
        if last is None:
            return False
        return int(self.user_message_count or 0) - int(last) <= within_turns

    def _check_production_edit_change_log(self, tool_name: str, files: list[str]) -> None:
        matches = self._production_edit_files(tool_name, files)
        if not matches:
            return
        evidence = f"{tool_name} touched production path(s): {', '.join(matches[:5])}"
        self._production_mutation_tool_instance = self._tool_instance_counter
        self._production_mutation_evidence = evidence
        if self._change_log_called_recently(within_turns=5):
            return
        mode = self._guardian_rule_mode("R36_production_change_log")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R36 SHADOW] would inject production edit change_log requirement: %s", matches[:5])
            return
        self._enqueue(
            _PRODUCTION_EDIT_CHANGE_LOG_PROMPT.format(files=", ".join(matches[:5])),
            f"r36:production-edit-change-log:{self._tool_instance_counter}",
            rule_id="R36_production_change_log",
        )
        _logger.info("[R36 %s] enqueued production edit change_log requirement", mode.upper())

    def _task_close_has_change_trace(self, tool_input) -> bool:
        payload = tool_input if isinstance(tool_input, dict) else {}
        fields = (
            "files_changed",
            "change_summary",
            "change_why",
            "change_verify",
            "evidence_refs",
            "evidence",
            "verification",
        )
        joined = "\n".join(str(payload.get(field) or "") for field in fields).lower()
        if "change_log:" in joined or "nexo_change_log" in joined:
            return True
        return bool(str(payload.get("files_changed") or "").strip() and str(payload.get("change_summary") or "").strip())

    def _check_production_change_log_close(self, tool_name: str, tool_input) -> None:
        if tool_name in {"nexo_change_log", "mcp__nexo__nexo_change_log"}:
            self._last_change_log_user_message_count = int(self.user_message_count or 0)
            self._production_mutation_tool_instance = None
            self._production_mutation_evidence = ""
            return
        summary = self._production_mutation_summary(tool_name, tool_input)
        if summary:
            self._production_mutation_tool_instance = self._tool_instance_counter
            self._production_mutation_evidence = summary
            return
        if tool_name not in {"nexo_task_close", "mcp__nexo__nexo_task_close"}:
            return
        if self._production_mutation_tool_instance is None:
            return
        if self._task_close_has_change_trace(tool_input):
            self._production_mutation_tool_instance = None
            self._production_mutation_evidence = ""
            return
        mode = self._guardian_rule_mode("R36_production_change_log")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R36 SHADOW] would inject production change_log requirement")
            return
        self.injection_queue.append({
            "prompt": _PRODUCTION_CHANGE_LOG_PROMPT,
            "tag": "r36:production-change-log",
            "rule_id": "R36_production_change_log",
        })
        _logger.info("[R36 %s] enqueued production change_log requirement", mode.upper())

    def _release_close_scope(self, tool_input) -> bool:
        if not isinstance(tool_input, dict):
            return False
        high_signal_fields = (
            "work_type",
            "stakes",
            "triggered_by",
            "followup_description",
            "change_why",
            "change_summary",
            "summary",
            "result",
            "outcome_notes",
        )
        text = "\n".join(str(tool_input.get(field) or "") for field in high_signal_fields).lower()
        if not text:
            return False
        if "paridad" in text:
            return True
        if "release" not in text:
            return False
        return any(token in text for token in ("publish", "public", "stable", "version", "tag", "github", "manifest", "v0.", "v7."))

    def _desktop_release_close_scope(self, tool_input) -> bool:
        if not isinstance(tool_input, dict):
            return False
        fields = (
            "work_type",
            "stakes",
            "triggered_by",
            "followup_description",
            "change_why",
            "change_summary",
            "summary",
            "result",
            "outcome_notes",
        )
        text = "\n".join(str(tool_input.get(field) or "") for field in fields).lower()
        return "release" in text and ("nexo desktop" in text or re.search(r"\bdesktop\b", text))

    def _missing_release_verification_checks(self, tool_input) -> list[str]:
        if not isinstance(tool_input, dict):
            return []
        fields = (
            "evidence",
            "verification",
            "evidence_refs",
            "change_verify",
            "outcome_notes",
            "summary",
            "result",
        )
        text = "\n".join(str(tool_input.get(field) or "") for field in fields).lower()
        checks = {
            "gh pr view MERGED": (
                ("gh pr view" in text and "merged" in text)
                or "pr merged" in text
                or "merge state: merged" in text
                or "mergestatestatus: merged" in text
            ),
            "gh release view": "gh release view" in text or "github release" in text,
            "gh run view conclusion=success": (
                ("gh run view" in text or "workflow" in text)
                and ("conclusion" in text or "success" in text or "fallo" in text or "failure" in text)
            ),
            "curl manifest publico": (
                ("curl" in text and "manifest" in text)
                or "update.json" in text
                or "latest.yml" in text
                or "latest-mac.yml" in text
            ),
            "git tag -l": "git tag -l" in text or "tag pushed" in text or "tag existe" in text,
        }
        if self._desktop_release_close_scope(tool_input):
            checks["desktop promise audit"] = (
                ("promesas abiertas" in text or "open promises" in text or "transcript grep" in text)
                and ("dist/release" in text or "app.asar" in text or "packaged bundle" in text or "bundle empaquetado" in text)
                and ("0 promesas" in text or "0 open promises" in text or "followup" in text or "nf-" in text)
            )
        return [name for name, ok in checks.items() if not ok]

    def _check_release_task_close_verification(self, tool_name: str, tool_input) -> None:
        if tool_name not in {"nexo_task_close", "mcp__nexo__nexo_task_close"}:
            return
        if not self._release_close_scope(tool_input):
            return
        missing = self._missing_release_verification_checks(tool_input)
        if not missing:
            return
        mode = self._guardian_rule_mode("R43_release_verification_checklist")
        if mode == "off":
            return
        if mode == "shadow":
            _logger.info("[R43 SHADOW] would inject release verification checklist: %s", missing)
            return
        self._enqueue(
            _RELEASE_VERIFICATION_PROMPT.format(missing=", ".join(missing)),
            f"r43:release-verification:{self._tool_instance_counter}",
            rule_id="R43_release_verification_checklist",
        )
        _logger.info("[R43 %s] enqueued release verification checklist", mode.upper())

    def _advance_r17_window(self, tool_name: str):
        if not self._r17_promise_seen_for_turn:
            return
        if self._r17_first_tool_call_in_window:
            self._r17_first_tool_call_in_window = False
            self._mark_r17_commitments_in_progress(tool_name)
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
            self._guardian_rule_skip("R15_project_context", "rule_mode_off", mode=mode)
            return
        project_list = projects if projects is not None else self._projects_from_entities()
        if not project_list:
            self._guardian_rule_skip(
                "R15_project_context",
                "missing_dataset",
                mode=mode,
                details={"dataset": "projects"},
            )
            return
        records = recent_records if recent_records is not None else self.recent_tool_records
        decision = _r15_should(text or "", project_list, records)
        if not decision:
            return
        self._guardian_rule_event(
            "R15_project_context",
            "evaluated",
            mode=mode,
            details={"project": decision["project"], "tag": decision["tag"]},
        )
        # T4.2 — LLM gate: if the classifier says the turn is
        # conversational / off-topic, skip the injection. Regex wins on
        # "unknown" so legitimate R15 hits still fire without a working
        # classifier.
        if self._t4_gate_says_no("R15", span=(text or "")[:400]):
            _logger.info(
                "[R15 T4] gate=no, skipping project=%s", decision["project"]
            )
            self._guardian_rule_skip(
                "R15_project_context",
                "classifier_voted_no",
                mode=mode,
                details={"project": decision["project"], "tag": decision["tag"]},
            )
            return
        prompt = _R15_PROMPT.format(project=decision["project"])
        if mode == "shadow":
            _logger.info("[R15 SHADOW] would inject: project=%s", decision["project"])
            self._guardian_rule_skip(
                "R15_project_context",
                "shadow_mode",
                mode=mode,
                details={"project": decision["project"], "tag": decision["tag"]},
            )
            return
        self._enqueue(prompt, decision["tag"], rule_id="R15_project_context")
        _logger.info("[R15 %s] enqueued project=%s", mode.upper(), decision["project"])

    # ------------------------------------------------------------------
    # T4 LLM gate — central helper (Plan Consolidado T4.2-T4.6).
    # ------------------------------------------------------------------
    def _t4_gate_says_no(self, rule_id: str, *, span: str, context: str = "") -> bool:
        """Return True ONLY when the T4 classifier explicitly votes "no"
        for this rule hit. "yes" or "unknown" (classifier unavailable,
        import error, rate limit, parse failure) fall through to regex
        behaviour — never silently suppress a rule on infra flakiness.

        Every unavailable-path logs a WARNING once per (rule_id, reason)
        via ``_t4_gate_warned`` so degradations surface in the console
        without flooding it.
        """
        if not hasattr(self, "_t4_gate_warned"):
            self._t4_gate_warned = set()
        try:
            from t4_llm_gate import build_prompt, classify_with_llm
        except Exception as exc:
            key = (rule_id, f"import:{exc.__class__.__name__}")
            if key not in self._t4_gate_warned:
                self._t4_gate_warned.add(key)
                _logger.warning("[T4 gate] import failed for %s: %s", rule_id, exc)
            self._guardian_rule_event(
                rule_id,
                "classifier_unavailable",
                details={"reason": "import_failed", "error": str(exc)},
            )
            return False

        # Auditor H1 invariant: only an explicit semantic "no" may suppress
        # a regex hit. Router unavailable/no_route/ambiguous answers fall
        # through as "unknown" so the original rule still protects us.
        def _classifier_tristate(q: str, ctx: str) -> str:
            decision_kind_by_rule = {
                "R15": "t4_r15",
                "R23e": "t4_r23e",
                "R23f": "t4_r23f",
                "R23h": "t4_r23h",
            }
            decision_kind = decision_kind_by_rule.get(rule_id)
            if not decision_kind:
                return "unknown"
            try:
                from semantic_router import route as semantic_route
            except Exception:
                return "unknown"
            try:
                result = semantic_route(
                    decision_kind=decision_kind,
                    question=q,
                    context=ctx,
                    labels=("rule_applies", "false_positive"),
                )
            except Exception:
                return "unknown"
            if not result.ok:
                return "unknown"
            label = result.label or result.verdict
            if label == "rule_applies":
                return "yes"
            if label == "false_positive":
                return "no"
            return "unknown"

        prompt = build_prompt(rule_id, span=span, context=context)
        if not prompt:
            key = (rule_id, "no-prompt")
            if key not in self._t4_gate_warned:
                self._t4_gate_warned.add(key)
                _logger.warning(
                    "[T4 gate] no prompt template for rule_id=%s (check PROMPTS)",
                    rule_id,
                )
            self._guardian_rule_skip(
                rule_id,
                "missing_prompt_template",
                details={"gate": "t4"},
            )
            return False
        try:
            verdict = classify_with_llm(
                rule_id,
                prompt=prompt,
                context=context,
                classifier=_classifier_tristate,
            )
        except Exception as exc:
            key = (rule_id, f"classify:{exc.__class__.__name__}")
            if key not in self._t4_gate_warned:
                self._t4_gate_warned.add(key)
                _logger.warning(
                    "[T4 gate] classify failed for %s: %s — regex fallback active",
                    rule_id,
                    exc,
                )
            self._guardian_rule_event(
                rule_id,
                "classifier_unavailable",
                details={"reason": "classify_failed", "error": str(exc)},
            )
            return False
        return verdict == "no"

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
        span = (tool_input or {}).get("command", "") if isinstance(tool_input, dict) else ""
        if self._t4_gate_says_no("R23e", span=span):
            _logger.info("[R23e T4] gate=no, skipping")
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
        span = (tool_input or {}).get("command", "") if isinstance(tool_input, dict) else ""
        if self._t4_gate_says_no("R23f", span=span):
            _logger.info("[R23f T4] gate=no, skipping")
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
            # shadow → logs only. No enqueue, no followup, no side effects.
            _logger.info("[R23g SHADOW] would inject")
            return
        self._enqueue(prompt, "R23g_secrets_in_output", rule_id="R23g_secrets_in_output")
        self._ensure_exposed_credential_followup(tool_input)
        _logger.info("[R23g %s] enqueued", mode.upper())

    def _ensure_exposed_credential_followup(self, tool_input) -> None:
        if not isinstance(tool_input, dict):
            return
        cmd = tool_input.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return
        # A critical "rotate the credential" followup is only warranted when the
        # secret is actually exfiltrated to a third party. A bare local read
        # (cat .env, env, printenv) exposes nothing and must NOT mint an
        # un-closeable critical followup. The soft reminder above already nudges
        # the agent; the persistent debt is reserved for real exposure.
        if _r23g_has_sink is None or not _r23g_has_sink(cmd):
            return
        safe_cmd = _redact_for_log(cmd, max_len=160)
        # Deterministic id seeded only by the (redacted) command, so the same
        # exfiltration dedups across shadow/soft/hard instead of duplicating.
        followup_id = _security_followup_id(safe_cmd)
        try:
            from db import create_followup, get_followup  # type: ignore

            if get_followup(followup_id):
                return
            create_followup(
                followup_id,
                description=(
                    "SEGURIDAD: credencial expuesta a un tercero detectada por el guard. "
                    f"Origen: {safe_cmd}. Rotar/revocar la credencial y sustituirla en el gestor seguro."
                ),
                date=time.strftime("%Y-%m-%d"),
                verification=(
                    "Cierre solo con evidencia de revocación efectiva: llamada/API/HTTP 401 para la credencial antigua "
                    "o comprobación oficial equivalente, más nueva ubicación segura registrada."
                ),
                reasoning="R23g detected secret exfiltration to a third party",
                priority="critical",
                internal=1,
                owner="agent",
            )
            _logger.info("[R23g] security followup created: %s", followup_id)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R23g security followup create failed: %s", exc)

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
            self._guardian_rule_skip("R23k_script_duplicates_skill", "rule_mode_off", mode=mode)
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
            self._guardian_rule_skip(
                "R23k_script_duplicates_skill",
                "dataset_probe_failed",
                mode=mode,
                details={"dataset": "skill_matches", "error": str(exc)},
            )
            return
        if not matches:
            self._guardian_rule_skip(
                "R23k_script_duplicates_skill",
                "missing_dataset",
                mode=mode,
                details={"dataset": "skill_matches"},
            )
            return
        should, prompt = _r23k_should(
            tool_name,
            tool_input,
            skill_matches=matches,
        )
        if not should:
            return
        self._guardian_rule_event(
            "R23k_script_duplicates_skill",
            "evaluated",
            mode=mode,
            details={"match_count": len(matches)},
        )
        if mode == "shadow":
            _logger.info("[R23k SHADOW] would inject")
            self._guardian_rule_skip(
                "R23k_script_duplicates_skill",
                "shadow_mode",
                mode=mode,
                details={"match_count": len(matches)},
            )
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
        if mode == "off":
            self._guardian_rule_skip("R23m_message_duplicate", "rule_mode_off", mode=mode)
            return
        if not should:
            return
        self._guardian_rule_event(
            "R23m_message_duplicate",
            "evaluated",
            mode=mode,
            details={"window_seconds": 15 * 60, "recent_messages": len(self._r23m_recent_messages)},
        )
        if mode == "shadow":
            _logger.info("[R23m SHADOW] would inject")
            self._guardian_rule_skip(
                "R23m_message_duplicate",
                "shadow_mode",
                mode=mode,
                details={"recent_messages": len(self._r23m_recent_messages)},
            )
            return
        self._enqueue(prompt, "R23m_message_duplicate", rule_id="R23m_message_duplicate")
        _logger.info("[R23m %s] enqueued", mode.upper())

    def _check_post_external_action_verification(self, tool_name: str, tool_input):
        """Require an explicit re-open/re-read step after outbound actions."""
        external_tools = {
            "nexo_send",
            "nexo_email_send",
            "gmail_send",
            "nexo_calendar_create",
            "nexo_calendar_update",
            "google_calendar_create",
            "google_calendar_update",
            "calendar_create",
            "calendar_update",
        }
        if tool_name not in external_tools:
            return
        target = ""
        if isinstance(tool_input, dict):
            target = str(
                tool_input.get("to")
                or tool_input.get("recipient")
                or tool_input.get("thread")
                or tool_input.get("title")
                or tool_input.get("summary")
                or ""
            ).strip()
        prompt = (
            f"You just performed an external action with `{tool_name}`"
            f"{(' for ' + target[:120]) if target else ''}. "
            "Before you report it as sent or finished, reopen the real sent message/calendar item "
            "and verify the external facts: recipients, CC/BCC, subject, body/signature, "
            "date/time/timezone, links, invitees, attachments, and any identity/location claims. "
            "If anything is wrong, fix it first; otherwise include that verification in the closure evidence."
        )
        self._enqueue(
            prompt,
            f"post-action-verify:{tool_name}:{self.tool_call_count}",
            rule_id="R23n_post_action_verification",
        )
        if self._session_id:
            try:
                from db import create_protocol_debt, list_protocol_tasks  # type: ignore

                tasks = list_protocol_tasks(status="open", session_id=self._session_id, limit=1)
                task_id = str((tasks[0] if tasks else {}).get("task_id") or "")
                create_protocol_debt(
                    self._session_id,
                    "post_external_action_verification_required",
                    severity="warn",
                    task_id=task_id,
                    evidence=(
                        f"{tool_name} was called for an external action. The agent must reopen/re-read "
                        "the real sent/event artifact before claiming completion."
                    ),
                )
            except Exception:
                pass

    def _check_r23h(self, tool_name: str, tool_input):
        """R23h — script shebang vs interpreter mismatch (Fase D2 shadow)."""
        if _r23h_should is None:
            return
        mode = self._guardian_rule_mode("R23h_shebang_mismatch")
        if mode == "off":
            self._guardian_rule_skip("R23h_shebang_mismatch", "rule_mode_off", mode=mode)
            return
        should, prompt = _r23h_should(tool_name, tool_input)
        if not should:
            return
        self._guardian_rule_event("R23h_shebang_mismatch", "evaluated", mode=mode)
        span = ""
        if isinstance(tool_input, dict):
            span = tool_input.get("content") or tool_input.get("new_string") or ""
        if self._t4_gate_says_no("R23h", span=str(span)[:500]):
            _logger.info("[R23h T4] gate=no, skipping")
            self._guardian_rule_skip("R23h_shebang_mismatch", "classifier_voted_no", mode=mode)
            return
        if mode == "shadow":
            _logger.info("[R23h SHADOW] would inject")
            self._guardian_rule_skip("R23h_shebang_mismatch", "shadow_mode", mode=mode)
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

    def _check_r_catalog(self, tool_name: str, files: list[str] | None = None):
        """R-CATALOG — pre-create discovery probe.

        v7.7 Gap 3: the trigger set is now {nexo_*_create/_open/_add}
        UNION {Edit / Write into artefact-bearing paths}. The caller
        passes the extracted file list so plain Edit/Write materialising
        a skill / plugin / script without going through a dedicated MCP
        tool still triggers the probe.
        """
        if _r_catalog_should is None:
            return
        mode = self._guardian_rule_mode("R_CATALOG_before_artifact_create")
        if mode == "off":
            return
        # The trigger tool was just appended to recent_tool_records so we
        # inspect the preceding window (strip the current call).
        window = 60.0
        now = time.time()
        names = [
            r.tool for r in self.recent_tool_records[:-1]
            if (now - getattr(r, "ts", now)) <= window
        ]
        should, prompt = _r_catalog_should(tool_name, recent_tool_names=names, files=files or [])
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R_CATALOG SHADOW] would inject for %s", tool_name)
            return
        self._enqueue(prompt, f"R_CATALOG:{tool_name}", rule_id="R_CATALOG_before_artifact_create")
        _logger.info("[R_CATALOG %s] enqueued tool=%s", mode.upper(), tool_name)

    def _check_r_primitive_choice(self, tool_name: str, files: list[str] | None):
        """R_PRIMITIVE_CHOICE (v7.7 Gap 4) — SK-CREATE-NEXO-PRIMITIVE gate.

        Flags Edit/Write of a NEW artefact file without a recent primitive-
        choice probe. Does not duplicate R_CATALOG: R_CATALOG fires on
        every artefact-path write without inventory consultation, while
        this rule fires only when the file is genuinely new (no prior
        Read / Grep / Edit on the same path).
        """
        if _r_primitive_should is None:
            return
        mode = self._guardian_rule_mode("R_PRIMITIVE_CHOICE")
        if mode == "off":
            return
        window = 120.0
        now = time.time()
        names = [
            r.tool for r in self.recent_tool_records[:-1]
            if (now - getattr(r, "ts", now)) <= window
        ]
        records = [r for r in self.recent_tool_records[:-1]]
        should, prompt = _r_primitive_should(
            tool_name,
            files=files or [],
            recent_tool_names=names,
            recent_tool_records=records,
        )
        if not should:
            return
        if mode == "shadow":
            _logger.info("[R_PRIMITIVE_CHOICE SHADOW] would inject for %s", tool_name)
            return
        self._enqueue(
            prompt,
            f"R_PRIMITIVE_CHOICE:{tool_name}",
            rule_id="R_PRIMITIVE_CHOICE",
        )
        _logger.info("[R_PRIMITIVE_CHOICE %s] enqueued tool=%s", mode.upper(), tool_name)

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

    def on_assistant_message(self, text: str, *, classifier=None):
        """R34 entry point — called when an assistant message is complete.

        Plan Consolidado T5. If the message is a past-tense denial of an
        action (ES/EN patterns) and no shared-brain tool was called in the
        current turn, the rule fires a reminder to consult the shared brain
        before asserting what happened.

        Args:
            text: assistant output text.
            classifier: optional LLM yes/no callable used to disambiguate
                regex matches. Tests pass a fake.
        """
        if _r34_should is None or not text:
            return
        mode = self._guardian_rule_mode("R34_identity_coherence")
        if mode == "off":
            return
        recent_names = [r.tool for r in self.recent_tool_records]
        if classifier is None:
            def _semantic_classifier(question: str, context: str):
                try:
                    from semantic_router import route as semantic_route
                except Exception:
                    return "unknown"
                try:
                    result = semantic_route(
                        decision_kind="r34_identity_coherence",
                        question=question,
                        context=context,
                        labels=("past_action_denial", "not_a_denial"),
                    )
                except Exception:
                    return "unknown"
                if not result.ok:
                    return "unknown"
                label = result.label or result.verdict
                if label == "past_action_denial":
                    return "yes"
                if label == "not_a_denial":
                    return "no"
                return "unknown"

            classifier = _semantic_classifier
        try:
            inject, prompt, matched = _r34_should(
                text, recent_tool_names=recent_names, classifier=classifier,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("R34 probe failed (%s); staying silent", exc)
            return
        if not inject:
            return
        if mode == "shadow":
            _logger.info("[R34 SHADOW] would inject matched=%r", matched)
            return
        self._enqueue(prompt, f"R34:{matched[:40]}", rule_id="R34_identity_coherence")
        _logger.info("[R34 %s] enqueued matched=%r", mode.upper(), matched)

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
        # v7.6 per-instance counter. Every tool call advances it and we
        # pin the tool's latest instance so after_tool/before_tool can
        # tell "has target been called AFTER this trigger?" without
        # relying on the broken set-membership check.
        self._tool_instance_counter += 1
        self._tool_last_instance[name] = self._tool_instance_counter
        self.tools_called.add(name)
        self.tool_timestamps[name] = time.time()
        self.msg_since_tool[name] = 0
        self._tool_user_message_index[name] = int(self.user_message_count or 0)

        # v7.6 conditional counter advance. Tools watched by a
        # conditional rule tick a counter on every non-matching call.
        # When task_open (or whichever tool holds the rule) fires, the
        # counter is reset via reset_task_cycle().
        for entry in self._conditional:
            tool = entry["tool"]
            if tool == name:
                self._conditional_counters[tool] = 0
            else:
                self._conditional_counters[tool] = self._conditional_counters.get(tool, 0) + 1

        if name != "nexo_task_close":
            self._check_production_change_log_close(name, tool_input)

        # v7.6 task_close observed → rearm conditional for the companion
        # open tool so the next task cycle re-opens the obligation.
        if name == "nexo_task_close":
            if isinstance(tool_input, dict):
                close_text = "\n".join(
                    str(tool_input.get(field) or "")
                    for field in ("summary", "result", "evidence", "verification", "outcome_notes", "change_summary")
                )
                self._check_jargon_text(close_text, tag="r26:task-close-jargon")
            self._last_task_close_user_message_count = int(self.user_message_count or 0)
            self.reset_task_cycle("nexo_task_open")
            self._start_post_close_cooldown()
            self._check_production_change_log_close(name, tool_input)
            self._check_release_task_close_verification(name, tool_input)
            self._resolve_r17_commitments_from_task_close(tool_input)

        if name == "nexo_stop":
            self._session_stopped = True
            self._start_post_close_cooldown()

        # v7.7 Gap 1 — autonomous detector for multi_step_task_detected.
        # The event was dispatched by the map but nothing ever raised it.
        # Heuristic: three or more edit/execute/delegate calls within the
        # recent window (Edit/Write/Task/Bash-with-write-command) without
        # a nexo_skill_match in between signals multi-step work that
        # should consult skills first. We raise the event at most once per
        # task cycle — skill_match clears it; task_close rearms it.
        if not self._multi_step_event_fired:
            edit_like = {"Edit", "Write", "Task"}
            recent_edit_calls = sum(
                1 for r in self.recent_tool_records[-10:] if r.tool in edit_like
            )
            if recent_edit_calls >= 3 and "nexo_skill_match" not in self.tools_called:
                try:
                    self.raise_event("multi_step_task_detected", {"recent_edits": recent_edit_calls})
                except Exception:
                    pass  # telemetry-style; never crash enforcement
                self._multi_step_event_fired = True
        if name == "nexo_skill_match" or name == "nexo_task_close":
            # Both signals clear the multi-step flag so the next task
            # cycle gets its own detection window.
            self._multi_step_event_fired = False
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
        self._check_production_edit_change_log(name, files)

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
        self._check_post_external_action_verification(name, tool_input)

        # D2 shadow rules — low-signal, rolling out carefully.
        self._check_r23h(name, tool_input)
        self._check_r23j(name, tool_input)

        # R19 — Write on require_grep project without Grep.
        self._check_r19(name, tool_input)

        # R21 — legacy path reminder.
        self._check_r21(name, tool_input)

        # R22 — personal script create without prior context probes.
        self._check_r22(name, tool_input)

        # R-CATALOG (Plan 0.X.2) — nudge if we are about to create/open/add
        # without having consulted the live inventory in the last 60 s.
        self._check_r_catalog(name, files)

        # v7.7 Gap 4 — R_PRIMITIVE_CHOICE. Runs AFTER R_CATALOG because
        # R_CATALOG's prompt covers the generic inventory case; this one
        # adds the specific primitive-decision reminder when a brand-new
        # artefact file is being materialised via Edit/Write.
        self._check_r_primitive_choice(name, files)

        # R18 — retroactive followup-complete suggestion on closure actions.
        self._check_r18(name, tool_input)

        # R24 — stale memory window decay.
        self._advance_r24_window(name)

        # v7.6 per-instance satisfaction. The legacy check "target not in
        # tools_called" silently satisfied a dependency forever after the
        # first target call in the session. Now each trigger call opens a
        # fresh dependency; satisfaction is marked only when the target is
        # called AFTER this specific trigger instance.
        current_instance = self._tool_last_instance.get(name, self._tool_instance_counter)
        for entry in self._after_tool.get(name, []):
            target = entry["tool"]
            target_last = self._tool_last_instance.get(target, -1)
            if target_last < current_instance:
                prompt = entry["enf"].get("inject_prompt", "")
                if prompt:
                    self._enqueue(
                        prompt,
                        f"after:{name}:{current_instance}->{target}",
                        rule_id="after_tool_dependency",
                    )
                    self._after_tool_open_deps.append((name, current_instance, target))

        # v7.6 on_event pending resolution. If the target tool was called
        # within its grace window, clear the pending state.
        for event_name, pending in list(self._on_event_pending.items()):
            required_tool = pending.get("tool")
            if required_tool and self._tool_last_instance.get(required_tool, -1) > pending.get("fired_at_instance", -1):
                self._on_event_pending.pop(event_name, None)

    def on_tool_call_before(self, raw_name: str, tool_input=None):
        """Pre-invocation hook.

        Dispatches `before_tool` rules declared in the map. If the caller
        routes every tool call through this method, a missing required
        predecessor (e.g. nexo_guard_check before Edit) produces a visible
        injection BEFORE the destructive operation lands. The canonical
        pre-edit guard (R13, Capa 2) already handles Edit/Write defensively
        elsewhere — this path covers any future `before_tool` wiring the
        map declares without needing a new custom rule each time.
        """
        name = _normalize(raw_name)
        entries = self._before_tool.get(name, [])
        if not entries:
            return
        for entry in entries:
            required_tool = entry["tool"]
            rule = entry.get("rule", {})
            # R13 already emits a dedicated, context-aware prompt for the
            # nexo_guard_check → Edit/Write case. Skip the generic
            # before_tool injection there to avoid double-firing.
            if required_tool == "nexo_guard_check" and name in ("Edit", "Write"):
                continue
            current_instance = self._tool_instance_counter + 1  # upcoming call
            last = self._tool_last_instance.get(required_tool, -1)
            if last < current_instance - 1:  # required tool not called for this instance
                prompt = entry["enf"].get("inject_prompt", "") or rule.get("inject_prompt", "")
                if prompt:
                    self._enqueue(
                        prompt,
                        f"before:{name}:{current_instance}->{required_tool}",
                        rule_id="before_tool_dependency",
                    )

    def raise_event(self, event_name: str, context: dict | None = None):
        """External/hook trigger for `on_event` rules.

        Call this when a semantic event occurs that the map references:

        - `pre_compaction` / `post_compaction` (harness compaction hooks)
        - `factual_answer_with_high_stakes` (response contract upgrade)
        - `user_correction_without_learning` (R14-style detection)
        - `multi_step_task_detected` (3+ related edits or workflow-kind work)
        - `done_claimed_with_open_task` (R16 trigger on done/sent/fixed/published/deployed/shipped)

        The required tool (declared in the map) must be called within
        `grace_messages` (0 by default after the v7.6 checklist tightening
        so corrections land immediately, not 3 messages later). If not,
        a pending state is recorded and re-evaluated on every subsequent
        tool call via the same dispatcher as after_tool.
        """
        entries = self._on_event.get(event_name, [])
        if not entries:
            return
        for entry in entries:
            required_tool = entry["tool"]
            rule = entry.get("rule", {})
            grace = int(rule.get("grace_messages", 0))
            # If already called after the event fired, nothing to do.
            last = self._tool_last_instance.get(required_tool, -1)
            if last >= self._tool_instance_counter and grace == 0:
                continue
            prompt = entry["enf"].get("inject_prompt", "") or rule.get("inject_prompt", "")
            if not prompt:
                continue
            # Record pending so check_periodic and next on_tool_call can
            # clear it when the required tool actually fires.
            self._on_event_pending[event_name] = {
                "tool": required_tool,
                "fired_at_instance": self._tool_instance_counter,
                "grace": grace,
                "messages_since": 0,
            }
            # For grace=0 the injection is immediate. For grace>0 the
            # injection is deferred to check_periodic. The checklist set
            # learning_add to grace=0, so the typical path is immediate.
            if grace == 0:
                self._enqueue(
                    prompt,
                    f"on_event:{event_name}->{required_tool}",
                    rule_id=f"on_event:{event_name}",
                )

    def _check_conditional(self):
        """Evaluate conditional rules (e.g. task_open threshold).

        Called from check_periodic on every user turn boundary so the
        obligation opens per conversation turn rather than requiring a
        specific trigger event. v7.6 checklist fix: the previous
        threshold of 10 tool calls was criticized as "tarde" — the map
        now keeps the declared threshold but the engine halves it when
        the recent tool mix shows edit/execute/delegate signals (Edit,
        Write, Bash with mutation commands, Task dispatch).
        """
        if getattr(self, "_session_stopped", False):
            return
        if self._post_close_cooldown_active():
            return
        # A closed task cycle must not re-open itself on idle/background
        # ticks. Rearm only after a new visible user message advances the
        # turn counter.
        last_close_raw = getattr(self, "_last_task_close_user_message_count", -1)
        last_close_count = int(last_close_raw) if last_close_raw is not None else -1
        if last_close_count >= 0 and int(self.user_message_count or 0) <= last_close_count:
            return
        for entry in self._conditional:
            tool = entry["tool"]
            rule = entry.get("rule", {})
            base_threshold = int(rule.get("threshold", 10))
            # Heuristic: if the recent window shows at least one Edit /
            # Write / Task call, we treat the work as "edit/execute/
            # delegate" and halve the threshold (rounding up). This is
            # the checklist-driven early trigger without changing the
            # declared contract for non-edit flows.
            recent_names = {getattr(r, "tool", "") for r in self.recent_tool_records[-10:]}
            is_edit_flow = bool(recent_names & {"Edit", "Write", "Task"})
            threshold = max(1, (base_threshold + 1) // 2) if is_edit_flow else base_threshold
            counter = self._conditional_counters.get(tool, 0)
            if tool in self.tools_called:
                # Once task_open has been called at least once, the
                # conditional rule is satisfied for this task cycle. The
                # counter is reset on every task_close via reset_task_cycle().
                continue
            if counter >= threshold:
                prompt = entry["enf"].get("inject_prompt", "") or rule.get("inject_prompt", "")
                if prompt:
                    self._enqueue(
                        prompt,
                        f"conditional:{tool}:{counter}",
                        rule_id="conditional_threshold",
                    )

    def reset_task_cycle(self, tool: str = "nexo_task_open"):
        """Called when a task_close lands, so the conditional counter for
        the matching open-tool rearms for the next task.

        v7.7 Gap 7.1 (checklist pass-2 hotfix): v7.6 only reset the
        counter but left `tools_called` carrying `nexo_task_open` from
        the previous cycle. That meant `_check_conditional`'s early
        `if tool in self.tools_called: continue` short-circuit still
        blocked the re-nudge forever. We now also drop the open-tool
        from `tools_called` and clear its per-instance pin so the gate
        genuinely re-arms for the next task cycle. `_tool_last_instance`
        stays intact for the OTHER tools (per-instance semantics for
        after_tool still rely on it).
        """
        self._conditional_counters[tool] = 0
        if tool in self.tools_called:
            self.tools_called.discard(tool)
        # Clearing the per-instance pin lets future after_tool
        # dependencies on this tool re-open too; the conditional rule is
        # what the checklist focused on but the same "satisfied-by-once"
        # defect applied to after_tool gates pointing at task_open.
        self._tool_last_instance.pop(tool, None)

    def _post_close_cooldown_active(self) -> bool:
        cooldown_until = float(getattr(self, "_post_close_cooldown_until", 0.0) or 0.0)
        return bool(cooldown_until and time.time() < cooldown_until)

    def _post_close_cooldown_blocks(self, prompt: str, tag: str) -> bool:
        if not self._post_close_cooldown_active():
            return False
        normalized = str(prompt or "").lower()
        tag_text = str(tag or "").lower()
        if tag_text.startswith("r18:"):
            return False
        if tag_text.startswith("conditional:nexo_task_open"):
            return False
        if "nexo_" in normalized:
            return True
        return tag_text.startswith((
            "start:",
            "periodic_msg:",
            "periodic_time:",
            "after:nexo_task_close",
            "on_event:",
        ))

    def _start_post_close_cooldown(self) -> None:
        cooldown_seconds = int(getattr(self, "_post_close_cooldown_seconds", 300) or 0)
        if cooldown_seconds <= 0:
            return
        self._post_close_cooldown_until = time.time() + cooldown_seconds
        before = len(self.injection_queue)
        self.injection_queue = [
            item for item in self.injection_queue
            if not self._post_close_cooldown_blocks(str(item.get("prompt", "")), str(item.get("tag", "")))
        ]
        removed = before - len(self.injection_queue)
        if removed:
            _logger.info("POST_CLOSE_COOLDOWN: cleared %d queued protocol injection(s)", removed)

    def check_periodic(self):
        if getattr(self, "_session_stopped", False):
            _logger.info("SESSION_STOPPED: periodic checks suppressed (nexo_stop seen)")
            return
        if self._post_close_cooldown_active():
            _logger.info("POST_CLOSE_COOLDOWN: periodic checks suppressed")
            return
        for entry in self._on_start:
            tool = entry["tool"]
            threshold = entry["rule"].get("threshold", 2)
            if tool not in self.tools_called and self.user_message_count >= threshold:
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

        # v7.6 conditional + deferred on_event reminders.
        self._check_conditional()
        self._check_on_event_pending()
        # v7.8 — drain hook-emitted events (pre_compaction, post_compaction).
        self._consume_pending_hook_events()

    def _consume_pending_hook_events(self):
        """v7.8 / v7.8.1 — drain queued hook events for THIS session only.

        pre-compact.sh and post-compact.sh run in separate processes, so
        they cannot call `raise_event()` directly. They append one NDJSON
        row per event to `~/.nexo/runtime/data/pending_enforcer_events.ndjson`.

        v7.8.1 (Francisco correction): the queue is GLOBAL across all
        concurrent sessions, so the engine MUST filter by `self._session_id`
        before consuming. The original v7.8 drain read every row, fired
        `raise_event` for all of them, then truncated the whole file —
        that let a session A enforcer eat events addressed to session B.
        The fix:

          * Read all rows.
          * Split into (mine, others) by comparing row["session_id"] to
            the engine's own `_session_id`.
          * Fire `raise_event` for MY rows only.
          * Rewrite the file with only the OTHERS (plus any rows whose
            session_id we cannot parse — leave them for the next run).

        This preserves the "no double-fire" invariant and also closes
        the cross-session consumption bug.

        Fail-closed: any parse/IO error is swallowed so a broken queue
        cannot crash enforcement.
        """
        try:
            import os
            nexo_home = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
            queue_path = os.path.join(nexo_home, "runtime", "data", "pending_enforcer_events.ndjson")
            if not os.path.isfile(queue_path):
                return
            import json
            try:
                with open(queue_path, "r", encoding="utf-8") as fh:
                    raw_lines = [ln.rstrip("\n") for ln in fh.readlines()]
            except Exception:
                return
            if not raw_lines:
                return
            own_sid = str(self._session_id or "").strip()
            mine: list[dict] = []
            keep_raw: list[str] = []
            for line in raw_lines:
                s = line.strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except Exception:
                    # Preserve malformed lines so an unrelated parser
                    # error does not silently drop another session's event.
                    keep_raw.append(s)
                    continue
                row_sid = str((row or {}).get("session_id") or "").strip()
                if own_sid and row_sid and row_sid == own_sid:
                    mine.append(row)
                elif not own_sid:
                    # Engine has no session id yet (startup edge). Do
                    # not consume anything — another session might own
                    # these rows.
                    keep_raw.append(s)
                else:
                    keep_raw.append(s)
            # Rewrite the file with the rows this session did NOT claim.
            try:
                with open(queue_path, "w", encoding="utf-8") as fh:
                    for kept in keep_raw:
                        fh.write(kept + "\n")
            except Exception:
                # If the rewrite fails we still have the events cached in
                # `mine`; we just live with a duplicate-risk for the next
                # read (still bounded — raise_event is itself idempotent
                # via its dedup tag).
                pass
            for row in mine:
                event = (row or {}).get("event")
                if not isinstance(event, str) or not event:
                    continue
                try:
                    self.raise_event(event, row)
                except Exception:
                    pass
        except Exception:
            # Never crash on consumer errors.
            pass

    def _check_on_event_pending(self):
        """Re-evaluate on_event rules with grace > 0 after message ticks.

        Called from check_periodic. If the grace window has expired and
        the required tool was never called, fire the reminder. Otherwise
        the pending row stays put until the target fires or grace runs out.
        """
        for event_name, pending in list(self._on_event_pending.items()):
            required_tool = pending.get("tool")
            grace = int(pending.get("grace", 0))
            if required_tool and self._tool_last_instance.get(required_tool, -1) > pending.get("fired_at_instance", -1):
                self._on_event_pending.pop(event_name, None)
                continue
            pending["messages_since"] = int(pending.get("messages_since", 0)) + 1
            if pending["messages_since"] >= grace:
                # Locate the matching entry to pull the injection prompt.
                for entry in self._on_event.get(event_name, []):
                    if entry["tool"] != required_tool:
                        continue
                    rule = entry.get("rule", {})
                    prompt = entry["enf"].get("inject_prompt", "") or rule.get("inject_prompt", "")
                    if prompt:
                        self._enqueue(
                            prompt,
                            f"on_event:{event_name}:{pending['fired_at_instance']}->{required_tool}",
                            rule_id=f"on_event:{event_name}",
                        )
                    break

    def get_end_prompts(self) -> list[str]:
        prompts = []
        for entry in self._on_end:
            if entry["enf"].get("level") == "must":
                p = entry["enf"].get("session_end_inject_prompt") or entry["enf"].get("inject_prompt", "")
                if p:
                    prompts.append(append_operator_language_contract(p))
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

    @staticmethod
    def _mcp_restart_marker_path() -> "Path":
        """Resolve the path to the MCP restart-required marker on disk.

        The marker is written by `plugins/update.py` when a `nexo update`
        actually changes runtime `.py` bytes (cf. v7.11.0 fingerprint
        gating). Honors the F0.6 runtime/operations/ canonical layout
        with a fall-back to the pre-F0.6 operations/ legacy layout so
        half-migrated installs are still detected correctly.
        """
        from pathlib import Path as _Path
        home = _Path(os.environ.get("NEXO_HOME", str(_Path.home() / ".nexo")))
        new = home / "runtime" / "operations" / "mcp-restart-required.json"
        if new.is_file():
            return new
        legacy = home / "operations" / "mcp-restart-required.json"
        return legacy if legacy.is_file() else new

    def _mcp_restart_pending(self) -> bool:
        """Return True if the MCP server has a restart-required marker on disk.

        Cached per-instance with a 30s TTL: the marker rarely changes mid-
        session (it's written by `nexo update` and cleared by the next
        client restart) but a TTL keeps long-lived enforcer instances from
        getting stuck on a stale negative cache if the operator runs
        `nexo update` mid-session without restarting.
        """
        cached_at = getattr(self, "_mcp_restart_pending_cache_at", 0.0)
        if (time.time() - cached_at) < 30.0:
            return getattr(self, "_mcp_restart_pending_cache", False)
        try:
            result = self._mcp_restart_marker_path().is_file()
        except Exception:  # noqa: BLE001 — never block enforcement on path errors
            result = False
        self._mcp_restart_pending_cache = result
        self._mcp_restart_pending_cache_at = time.time()
        return result

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
        normalized_prompt = _upgrade_silent_reminder_prompt(prompt)
        if self._post_close_cooldown_blocks(normalized_prompt, tag):
            _logger.info("POST_CLOSE_COOLDOWN: skip %s (rule_id=%s)", tag, rule_id or "?")
            return
        if any(q["tag"] == tag for q in self.injection_queue):
            return
        # v7.11.2: suppress reminders that ask the agent to call nexo_*
        # tools while the MCP server has a restart-required marker on
        # disk. Without this gate every periodic ping ("Execute
        # nexo_session_diary_write", "Execute nexo_smart_startup",
        # nexo_guard_check pre-Edit, etc) returns mcp_restart_required
        # and the agent burns cycles on guaranteed no-ops. Reminders that
        # don't reference nexo_* (R23 deploy guards, R25 nora/maria
        # read-only, etc) still fire — they don't depend on the MCP.
        if "nexo_" in normalized_prompt and self._mcp_restart_pending():
            _logger.info(
                "SKIP: %s — mcp_restart_required marker present (rule_id=%s)",
                tag,
                rule_id or "?",
            )
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
        localized_prompt = append_operator_language_contract(normalized_prompt)
        self.injection_queue.append({"prompt": localized_prompt, "tag": tag, "at": time.time(), "rule_id": rule_id})
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
        enforcer.on_user_message(
            prompt or "",
            session_end_detector=lambda _text: False,
        )
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
    timed_out = False
    stdout_closed = False
    stdout_lines: queue.Queue[str | None] = queue.Queue()

    def _kill_proc(reason: str) -> None:
        _logger.warning("%s after %ds", reason, timeout)
        try:
            proc.kill()
        except Exception:
            pass

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

    def _read_stdout():
        try:
            for line in proc.stdout:
                stdout_lines.put(line)
        except Exception:
            pass
        finally:
            stdout_lines.put(None)

    def _read_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    last_periodic_check = time.time()

    def _handle_stdout_line(raw_line: str) -> bool:
        """Process one Claude stream-json line.

        Return True when the stream turn is complete and the caller should
        leave the main read loop.
        """
        nonlocal waiting_for_injection_response
        line = raw_line.strip()
        if not line:
            return False

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False

        event_type = event.get("type", "")

        if event_type == "assistant" and event.get("message", {}).get("content"):
            for block in event["message"]["content"]:
                if block.get("type") == "tool_use":
                    # v7.7 Gap 7.3 — wire before_tool in the live
                    # stream. Desktop already calls onBeforeToolCall
                    # before onToolCall; Brain's stream was only
                    # calling on_tool_call, silently skipping every
                    # before_tool rule the map declared.
                    enforcer.on_tool_call_before(block.get("name", ""), block.get("input"))
                    enforcer.on_tool_call(block.get("name", ""), block.get("input"))
        elif event_type == "content_block_start":
            cb = event.get("content_block", {})
            if cb.get("type") == "tool_use":
                enforcer.on_tool_call_before(cb.get("name", ""), cb.get("input"))
                enforcer.on_tool_call(cb.get("name", ""), cb.get("input"))

        if event_type == "assistant" and not waiting_for_injection_response:
            msg = event.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    try:
                        if enforcer.should_block_first_visible_text():
                            item = enforcer.flush()
                            if item:
                                _inject(item["prompt"])
                            return False
                    except Exception as _startup_gate_exc:  # noqa: BLE001
                        _logger.warning("first visible startup gate failed: %s", _startup_gate_exc)
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
                return False

            enforcer.check_periodic()
            item = enforcer.flush()
            if item:
                _inject(item["prompt"])
            else:
                _logger.info("TURN_END — no pending enforcements, done")
                return True

        return False

    try:
        while True:
            if time.time() - start_time > timeout:
                timed_out = True
                _kill_proc("TIMEOUT")
                break

            try:
                raw_line = stdout_lines.get(timeout=0.2)
            except queue.Empty:
                if stdout_closed and proc.poll() is not None:
                    break
                if time.time() - last_periodic_check > 30:
                    enforcer.check_periodic()
                    last_periodic_check = time.time()
                continue

            if raw_line is None:
                stdout_closed = True
                break

            if _handle_stdout_line(raw_line):
                break

    except Exception as e:
        _logger.error("EXCEPTION: %s", e)
    finally:
        end_prompts = [] if timed_out else enforcer.get_end_prompts()
        for ep in end_prompts:
            try:
                _inject(ep)
                deadline = time.time() + 15
                while time.time() <= deadline:
                    try:
                        raw_line = stdout_lines.get(timeout=0.2)
                    except queue.Empty:
                        if proc.poll() is not None:
                            break
                        continue
                    if raw_line is None:
                        stdout_closed = True
                        break
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

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    final_text = "\n".join(collected_text)
    final_stderr = "".join(stderr_lines)
    returncode = proc.returncode
    if timed_out:
        returncode = 124
        timeout_msg = f"NEXO enforcement timeout after {timeout}s"
        final_stderr = f"{final_stderr.rstrip()}\n{timeout_msg}".lstrip()
    elif returncode is None:
        returncode = 0

    return subprocess.CompletedProcess(
        stream_cmd, returncode, final_text, final_stderr
    )
