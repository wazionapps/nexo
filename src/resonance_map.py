"""Central resonance map — single source of truth for (backend, model, effort)
decisions across every automation caller.

Motivation
----------
Before v5.9.0 each caller that wanted to invoke Claude or Codex had to either
pass explicit model + reasoning_effort arguments or rely on the global defaults
in ``model_defaults.json``. Global defaults meant every background cron shared
the same max-effort configuration as the interactive ``nexo chat``, so batch
jobs (daily synthesis, postmortem consolidation, gbp posts) burned maximum
reasoning effort on tasks that didn't need it, while downgrading any single
default changed behaviour everywhere at once.

This module introduces four **resonance tiers** (``MAXIMO`` / ``ALTO`` /
``MEDIO`` / ``BAJO``) and maps each tier to a concrete ``(model, effort)`` pair
per backend. Every caller is labelled in one of two ways:

    - **User-facing callers** (``nexo chat``, Desktop new session, interactive
      ``nexo update``) use the user's configured default resonance. When the
      user changes the default via ``nexo preferences --resonance`` or through
      the Desktop preferences pane, those three entry points adjust.

    - **System-owned callers** (every cron, every background script, every
      MCP-tool-triggered automation) use a fixed tier we pick per caller based
      on what the task needs. A quarterly evolution pass that synthesizes
      ten thousand lines into a new self-improvement plan is ``MAXIMO``. A
      daily GBP post that needs to produce 200 characters of marketing copy
      is ``BAJO``. That decision stays in this file and NEVER reads the user
      default.

If a backend does not offer all four effort settings (e.g. a hypothetical
model with only ``max`` and ``low``), we collapse adjacent tiers — ``MAXIMO``
and ``ALTO`` both map to the backend's highest available effort, ``MEDIO``
and ``BAJO`` to the lowest. If a backend has no effort knob at all, the tier
still resolves to the same model with an empty effort string; the resonance
label is then informational only.

Contract
--------
Every call into ``run_automation_prompt`` and ``run_automation_interactive``
MUST pass a ``caller=`` string that is registered here. Callers not in the
registry raise ``UnregisteredCallerError`` — there is no silent default. This
forces the resonance decision to be explicit and auditable, and prevents
future scripts from silently inheriting the wrong tier.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple


# ---------------------------------------------------------------------------
# Tier → (claude_model, claude_effort, codex_model, codex_effort)
# ---------------------------------------------------------------------------
# Single source of truth lives in ``src/resonance_tiers.json``. That file is
# the contract shared with NEXO Desktop and any other consumer — this module
# only reads it and exposes it as Python data. Editing the JSON is the one
# way to change tier assignments; this file no longer carries the table.
#
# If a future backend offers fewer tiers (e.g. only max + low), collapse
# adjacent tiers onto the closest available effort directly in the JSON
# (MAXIMO + ALTO → highest, MEDIO + BAJO → lowest). If a backend has no
# effort setting at all, leave the effort string empty.

TIERS = ("maximo", "alto", "medio", "bajo")

# Resolution order for the contract file:
#   1) ~/.nexo/brain/resonance_tiers.json  (v6.0.3+ public contract path, shared
#      with NEXO Desktop and any external client)
#   2) Legacy NEXO_HOME/resonance_tiers.json (pre-v6.0.3 runtime layout)
#   3) Package source src/resonance_tiers.json (dev checkouts / tests)
def _resolve_resonance_path() -> Path:
    nexo_home = os.environ.get("NEXO_HOME") or str(Path.home() / ".nexo")
    candidates = [
        Path(nexo_home) / "brain" / "resonance_tiers.json",
        Path(nexo_home) / "resonance_tiers.json",
        Path(__file__).resolve().parent / "resonance_tiers.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # No contract present: return the canonical path so the ValueError raised
    # at load time points at where the contract should live.
    return candidates[0]


_RESONANCE_JSON_PATH = _resolve_resonance_path()


def _normalize_tier_entry(entry: dict) -> dict[str, tuple[str, str]]:
    """Coerce ``{"claude_code": {"model": ..., "effort": ...}, ...}`` into
    the internal ``{backend: (model, effort)}`` shape used by the rest of
    this module. Missing fields collapse to empty strings so the loader is
    forgiving about hand-edited JSON."""
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(entry, dict):
        return out
    for backend, spec in entry.items():
        if not isinstance(backend, str):
            continue
        if isinstance(spec, dict):
            model = str(spec.get("model", "")).strip()
            effort = str(spec.get("effort", "")).strip()
            out[backend] = (model, effort)
    return out


def load_resonance_table(
    path: Path | None = None,
) -> tuple[dict[str, dict[str, tuple[str, str]]], str]:
    """Public loader used by tests and the runtime alike.

    Returns ``(table, default_tier)`` where ``table`` is keyed by tier name
    (``maximo``/``alto``/``medio``/``bajo``) and each value is a
    ``{backend: (model, effort)}`` dict.

    Raises ``FileNotFoundError`` if the JSON is missing — we never
    silently fall back to a hardcoded table because that is exactly what
    the pre-v6.0.0 code did, and the whole point of v6.0.0 is that the
    JSON is the only source of truth.
    """
    target = Path(path) if path else _RESONANCE_JSON_PATH
    data = json.loads(target.read_text())
    raw_tiers = data.get("tiers") or {}
    if not isinstance(raw_tiers, dict) or not raw_tiers:
        raise ValueError(f"resonance_tiers.json missing 'tiers' mapping: {target}")

    table: dict[str, dict[str, tuple[str, str]]] = {}
    for tier_name, entry in raw_tiers.items():
        normalized = _normalize_tier_entry(entry)
        if normalized:
            table[tier_name] = normalized

    # Must contain the four canonical tiers so callers can rely on them
    # without guarding every lookup.
    missing = [t for t in TIERS if t not in table]
    if missing:
        raise ValueError(f"resonance_tiers.json missing tiers: {missing} in {target}")

    default_tier = str(data.get("default_tier") or "alto").strip().lower()
    if default_tier not in table:
        default_tier = "alto"
    return table, default_tier


_RESONANCE_TABLE, DEFAULT_RESONANCE = load_resonance_table()


# ---------------------------------------------------------------------------
# Caller registry
# ---------------------------------------------------------------------------
# Every script that calls the automation backend is registered here. Two
# categories, in two separate dicts so a reviewer can see at a glance which
# callers follow the user's preference and which are locked by us.
#
# USE_USER_DEFAULT → the caller reads the user's configured resonance at
#                    runtime. Only three callers should ever be in this list:
#                    the two interactive entry points (terminal chat, Desktop
#                    new conversation) and the interactive nexo update flow.
#
# SYSTEM_OWNED     → the caller runs at whatever tier we deem appropriate
#                    for its workload, ignoring the user's default. Tier is
#                    picked for quality of output, not cost: batch jobs that
#                    synthesize across a lot of data lean ALTO/MAXIMO, jobs
#                    that apply a fixed transform or produce short copy lean
#                    MEDIO/BAJO.

USE_USER_DEFAULT_SENTINEL = "__USE_USER_DEFAULT__"

USER_FACING_CALLERS: dict[str, str] = {
    "nexo_chat": USE_USER_DEFAULT_SENTINEL,
    "desktop_new_session": USE_USER_DEFAULT_SENTINEL,
    "nexo_update_interactive": USE_USER_DEFAULT_SENTINEL,
    # v6.0.4 — dashboard "Open followup in Terminal" spawns a fresh
    # interactive Claude/Codex session. Treat it like nexo_chat so the
    # user's default_resonance preference flows through.
    "nexo_followup_terminal": USE_USER_DEFAULT_SENTINEL,
}

# System-owned callers. Grouped thematically for readability.
SYSTEM_OWNED_CALLERS: dict[str, str] = {
    # ---- Evolution and introspection: highest-quality reasoning needed ----
    "evolution/run":                    "maximo",
    "reflection":                       "maximo",

    # ---- Protocol Enforcer classifier (Fase 2 spec 0.1/0.22) ---------------
    # Short yes/no classification via call_model_raw (~200ms Haiku). NEVER
    # elevate to a higher tier — costs per turn are the main constraint when
    # every R13/R14/R16/R17/R20 decision hits this caller. If quality becomes
    # an issue, the fix is to refine the prompt or adopt the zero-shot local
    # classifier (item 0.21), not to raise the tier.
    "enforcer_classifier":              "muy_bajo",

    # ---- Deep sleep: extraction and synthesis benefit from quality --------
    "deep-sleep/extract":               "alto",
    "deep-sleep/synthesize":            "maximo",
    "deep-sleep/apply_findings":        "alto",
    "sleep/nightly":                    "alto",
    "synthesis/daily":                  "alto",

    # ---- User-facing outputs where quality is visible ---------------------
    "catchup/morning":                  "alto",
    "daily_self_audit":                 "alto",
    "postmortem_consolidator":          "alto",
    "proactive_dashboard":              "alto",
    "followup_runner":                  "alto",

    # ---- Defensive / consistency tasks ------------------------------------
    "immune/scan":                      "medio",
    "learning_validator":               "medio",
    "outcome_checker":                  "medio",
    "check_context":                    "medio",

    # ---- Agent orchestration ----------------------------------------------
    "agent_run/generic":                "alto",

    # ---- Tooling helpers (short, structured outputs) ----------------------
    "tools/drive_search":               "medio",

    # ---- Marketing automation ---------------------------------------------
    # These post to Google Business Profile on behalf of Francisco's
    # businesses. Short copy, but user-visible on a public surface; a
    # mediocre post embarrasses the brand. Running them ALTO even though
    # it's ~200 chars keeps the output quality tight.
    "gbp/daily_post":                   "alto",
    "gbp/post_wazion":                  "alto",
    "gbp/post_psicologa":               "alto",
    "gbp/monthly_audit":                "alto",
    "gbp/reviews_watch":                "alto",

    # ---- Personal scripts (operators' own LaunchAgents) -------------------
    # Francisco + Maria ship the same set of personal scripts via
    # ~/.nexo/scripts (installed per-user, not through the core manifest).
    # They all call into mcp__nexo__* so they cannot run under --bare.
    "personal/email-monitor":           "alto",   # answer real user emails, quality matters
    "personal/github-monitor":          "alto",   # reason about issues/PRs, not mechanical
    "personal/post-x":                  "alto",   # public-facing copy
    "personal/followup-runner":         "alto",   # executes due followups, output is user-visible
    "personal/orchestrator-v2":         "maximo", # autonomous orchestration, critical reasoning
}

ALL_REGISTERED_CALLERS: frozenset[str] = frozenset(
    list(USER_FACING_CALLERS.keys()) + list(SYSTEM_OWNED_CALLERS.keys())
)


# v6.0.2 — Reserved caller prefix for user-owned personal scripts that live
# outside this repo (``~/.nexo/scripts/``). Callers matching this prefix
# bypass the registry entirely: they cannot be required to register because
# they ship with each operator's own install. Instead, the script passes
# either an explicit ``tier`` (semantic) or a ``reasoning_effort`` (direct
# override) — or falls back to the user's ``default_resonance`` preference,
# and finally to ``DEFAULT_RESONANCE`` as the last line of defence.
#
# The prefix is NOT a loophole for new core scripts. Anything inside the
# ``src/`` tree or shipped via the core manifest continues to require a
# registered entry. The docs (``docs/personal-scripts-guide.md``) explain
# the split to any NEXO session helping an operator author a new script.
PERSONAL_CALLER_PREFIX = "personal/"


class UnregisteredCallerError(ValueError):
    """Raised when a caller string is not in the resonance registry.

    Every caller that dispatches an automation subprocess MUST register here.
    We do not fall back to a default tier silently — that would re-introduce
    the pre-v5.9.0 problem where the wrong script could inherit the wrong
    reasoning budget without anyone noticing. The fix for this error is:
    add an entry to SYSTEM_OWNED_CALLERS (or USER_FACING_CALLERS if it is a
    genuine interactive entry point) and pick the tier deliberately.
    """


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _load_user_default_resonance() -> str:
    """Resolve the user's ``default_resonance`` preference.

    Reads ``calibration.json`` first (``preferences.default_resonance``, the
    location NEXO Desktop's preferences UI writes to) and falls back to
    ``schedule.json`` (``default_resonance``, the location the CLI used to
    write to in v5.9.0). Returns an empty string if neither source has a
    valid tier — callers should treat empty as "no preference".
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    home = _Path(_os.environ.get("NEXO_HOME", str(_Path.home() / ".nexo")))

    # calibration.json (Desktop UI writes here)
    cal_path = home / "brain" / "calibration.json"
    try:
        if cal_path.exists():
            cal = _json.loads(cal_path.read_text())
            prefs = cal.get("preferences") if isinstance(cal, dict) else None
            if isinstance(prefs, dict):
                tier = str(prefs.get("default_resonance") or "").strip().lower()
                if tier in TIERS:
                    return tier
    except (OSError, _json.JSONDecodeError):
        pass

    # schedule.json (CLI legacy)
    sched_path = home / "config" / "schedule.json"
    try:
        if sched_path.exists():
            sched = _json.loads(sched_path.read_text())
            tier = str((sched or {}).get("default_resonance") or "").strip().lower()
            if tier in TIERS:
                return tier
    except (OSError, _json.JSONDecodeError):
        pass

    return ""


def _normalise_tier(candidate: str | None) -> str:
    """Coerce a tier string to canonical lowercase; empty when invalid."""
    if not candidate:
        return ""
    value = str(candidate).strip().lower()
    return value if value in TIERS else ""


def resolve_tier_for_caller(
    caller: str,
    user_default: str | None = None,
    *,
    explicit_tier: str | None = None,
) -> str:
    """Return the resonance tier that should apply to ``caller``.

    Resolution order:

    1. ``caller`` is empty → raise ``UnregisteredCallerError`` (same as v6.0.0).
    2. ``caller`` starts with ``PERSONAL_CALLER_PREFIX``:
         a. ``explicit_tier`` if valid — semantic override from the script.
         b. ``user_default`` if valid — operator's configured default.
         c. Stored ``preferences.default_resonance`` via the loader.
         d. ``DEFAULT_RESONANCE`` as the final fallback.
       The registry is NEVER consulted for personal callers: scripts outside
       the repo cannot register, and forcing them to pin a tier there would
       defeat the whole purpose of the ``personal/`` contract.
    3. User-facing callers: user default → DEFAULT (unchanged).
    4. System-owned callers: fixed tier (unchanged).
    5. Anything else: ``UnregisteredCallerError`` (unchanged).
    """
    if not caller:
        raise UnregisteredCallerError(
            "caller= is required. Every automation subprocess must be registered "
            "in src/resonance_map.py so its reasoning budget is deliberate."
        )

    if caller.startswith(PERSONAL_CALLER_PREFIX):
        explicit = _normalise_tier(explicit_tier)
        if explicit:
            return explicit
        from_user = _normalise_tier(user_default)
        if from_user:
            return from_user
        from_prefs = _normalise_tier(_load_user_default_resonance())
        if from_prefs:
            return from_prefs
        return DEFAULT_RESONANCE

    if caller in USER_FACING_CALLERS:
        resolved_default = user_default
        if resolved_default is None:
            resolved_default = _load_user_default_resonance()
        tier = (resolved_default or DEFAULT_RESONANCE).strip().lower()
        if tier not in TIERS:
            tier = DEFAULT_RESONANCE
        return tier
    if caller in SYSTEM_OWNED_CALLERS:
        return SYSTEM_OWNED_CALLERS[caller]
    raise UnregisteredCallerError(
        f"caller {caller!r} is not registered in resonance_map.py. "
        "Add it to SYSTEM_OWNED_CALLERS (or USER_FACING_CALLERS if it is an "
        "interactive entry point) with a deliberate tier."
    )


def resolve_model_and_effort(
    caller: str,
    backend: str,
    user_default: str | None = None,
    *,
    explicit_tier: str | None = None,
) -> Tuple[str, str]:
    """Return ``(model, reasoning_effort)`` for ``caller`` on ``backend``.

    The ``backend`` key must match the entries in ``_RESONANCE_TABLE`` tier
    dicts (``claude_code`` or ``codex``). Unknown backends fall back to an
    empty pair; the caller is expected to handle that by raising or by
    passing its own explicit model/effort arguments.
    """
    tier = resolve_tier_for_caller(
        caller, user_default=user_default, explicit_tier=explicit_tier
    )
    backend_entry = _RESONANCE_TABLE.get(tier, {}).get(backend)
    if backend_entry is None:
        return "", ""
    return backend_entry


def register_system_caller(caller: str, tier: str) -> None:
    """Test/debug helper: register a caller at runtime.

    Production code must add callers statically to ``SYSTEM_OWNED_CALLERS``
    at module level so the registry is reviewable. This helper exists so
    unit tests can exercise ``resolve_*`` against synthetic caller names
    without mutating the shipped table.
    """
    if tier not in TIERS:
        raise ValueError(f"tier {tier!r} not in {TIERS}")
    SYSTEM_OWNED_CALLERS[caller] = tier
    # Rebuild the frozen view so the guard below sees the new caller.
    global ALL_REGISTERED_CALLERS
    ALL_REGISTERED_CALLERS = frozenset(
        list(USER_FACING_CALLERS.keys()) + list(SYSTEM_OWNED_CALLERS.keys())
    )


def unregister_system_caller(caller: str) -> None:
    """Mirror helper for tests that need to remove what they registered."""
    SYSTEM_OWNED_CALLERS.pop(caller, None)
    global ALL_REGISTERED_CALLERS
    ALL_REGISTERED_CALLERS = frozenset(
        list(USER_FACING_CALLERS.keys()) + list(SYSTEM_OWNED_CALLERS.keys())
    )
