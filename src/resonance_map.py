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

from typing import Tuple


# ---------------------------------------------------------------------------
# Tier → (claude_model, claude_effort, codex_model, codex_effort)
# ---------------------------------------------------------------------------
# Keep this table in ONE place. When we promote a new Claude or Codex model,
# update only this dict — every caller rebalances automatically.
#
# If a future backend offers fewer tiers (e.g. only max + low), collapse
# adjacent tiers onto the closest available effort. MAXIMO + ALTO → highest,
# MEDIO + BAJO → lowest. If a backend has no effort setting at all, leave
# the effort string empty.

TIERS = ("maximo", "alto", "medio", "bajo")

_RESONANCE_TABLE: dict[str, dict[str, tuple[str, str]]] = {
    "maximo": {
        "claude_code": ("claude-opus-4-7[1m]", "max"),
        "codex":       ("gpt-5.4", "xhigh"),
    },
    "alto": {
        "claude_code": ("claude-opus-4-7[1m]", "xhigh"),
        "codex":       ("gpt-5.4", "high"),
    },
    "medio": {
        "claude_code": ("claude-opus-4-7[1m]", "high"),
        "codex":       ("gpt-5.4", "medium"),
    },
    "bajo": {
        "claude_code": ("claude-opus-4-7[1m]", "medium"),
        "codex":       ("gpt-5.4", "low"),
    },
}

DEFAULT_RESONANCE = "alto"


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
}

# System-owned callers. Grouped thematically for readability.
SYSTEM_OWNED_CALLERS: dict[str, str] = {
    # ---- Evolution and introspection: highest-quality reasoning needed ----
    "evolution/run":                    "maximo",
    "reflection":                       "maximo",

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
    # These produce short copy; we could run them at BAJO for speed, but the
    # output is user-visible on a public surface, so we lean MEDIO for safety
    # against embarrassing outputs.
    "gbp/daily_post":                   "medio",
    "gbp/post_wazion":                  "medio",
    "gbp/post_psicologa":               "medio",
    "gbp/monthly_audit":                "medio",
    "gbp/reviews_watch":                "medio",
}

ALL_REGISTERED_CALLERS: frozenset[str] = frozenset(
    list(USER_FACING_CALLERS.keys()) + list(SYSTEM_OWNED_CALLERS.keys())
)


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


def resolve_tier_for_caller(caller: str, user_default: str | None = None) -> str:
    """Return the resonance tier that should apply to ``caller``.

    - User-facing callers resolve to ``user_default`` (or ``DEFAULT_RESONANCE``
      if the user has no preference recorded).
    - System-owned callers resolve to their fixed tier.
    - Unknown callers raise ``UnregisteredCallerError``.

    When ``user_default`` is not passed, the function looks it up from the
    calibration.json preferences first and schedule.json second.
    """
    if not caller:
        raise UnregisteredCallerError(
            "caller= is required. Every automation subprocess must be registered "
            "in src/resonance_map.py so its reasoning budget is deliberate."
        )
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
) -> Tuple[str, str]:
    """Return ``(model, reasoning_effort)`` for ``caller`` on ``backend``.

    The ``backend`` key must match the entries in ``_RESONANCE_TABLE`` tier
    dicts (``claude_code`` or ``codex``). Unknown backends fall back to an
    empty pair; the caller is expected to handle that by raising or by
    passing its own explicit model/effort arguments.
    """
    tier = resolve_tier_for_caller(caller, user_default=user_default)
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
