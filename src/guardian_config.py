"""guardian_config — Load and validate ~/.nexo/config/guardian.json.

Fase 2 spec items 0.5 + 0.19. Provides the canonical loader for the
Guardian configuration and the validator that enforces the core-rule
invariant: R13, R14, R16, R25, R30 can only be shadow / soft / hard.
A mode of 'off' is rejected with a clear error so the operator cannot
accidentally disable a rule that Fase 2 declared non-negotiable.

Resolution order for the config file:

  1. ``NEXO_HOME/config/guardian.json``   — the user's live config.
  2. Package default                      — ``src/presets/guardian_default.json``.

If (1) does not exist, the defaults are returned as-is; ``nexo init`` and
``nexo update`` copy (2) → (1) when appropriate (Fase E E.4). If (1)
exists but fails schema validation, ``load_guardian_config`` raises
``GuardianConfigError`` — fail-closed, Rule #249. The installer is
expected to catch this and surface a migration prompt.

Merging semantics (for ``nexo update``):

  - Keys present in (1) win.
  - Keys present in (2) but not in (1) are added.
  - For ``rules`` the same rule-by-rule policy applies so adding a new
    rule in a future release does not silently run ``off``; it falls
    back to the default mode from the preset.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import paths

# Core rules — Fase 2 spec 0.19 + 0.5. These rules may only be shadow /
# soft / hard. mode=off is rejected by the validator. Any future Fase 2
# amendment that promotes a rule to core status must also add it here.
CORE_RULES: frozenset[str] = frozenset({
    "R13_pre_edit_guard",
    "R14_correction_learning",
    "R16_declared_done",
    "R25_nora_maria_read_only",
    "R30_pre_done_evidence_system_prompt",
})

VALID_MODES: frozenset[str] = frozenset({"off", "shadow", "soft", "hard"})


class GuardianConfigError(ValueError):
    """Fail-closed sentinel for guardian.json validation errors.

    Raised from ``load_guardian_config`` and ``validate_guardian_config``
    when the config violates the schema or the core-rule invariant.
    Caller (installer, nexo doctor, enforcement engine) is responsible
    for either repairing the config or refusing to start. Rule #249.
    """


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent / "presets" / "guardian_default.json"


def _user_config_path() -> Path:
    return paths.config_dir() / "guardian.json"


def load_default_guardian_config() -> dict[str, Any]:
    """Return the packaged default config (``src/presets/guardian_default.json``)."""
    path = _default_config_path()
    try:
        text = path.read_text()
    except FileNotFoundError as exc:
        raise GuardianConfigError(
            f"packaged default missing at {path}; install broken"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GuardianConfigError(
            f"packaged default is not valid JSON: {exc}"
        ) from exc


def load_guardian_config(
    *,
    user_path: Path | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Load and validate the live Guardian config.

    Args:
        user_path: Override the default ``~/.nexo/config/guardian.json``
            path. Mainly for tests.
        strict: When True (default) errors surface as
            ``GuardianConfigError``. When False, errors are silently
            collapsed to the packaged default. ``nexo doctor`` passes
            strict=True; a production enforcement engine that must boot
            even on a broken config may set strict=False and log.

    Returns:
        The merged config dict (user config overlaid on packaged
        defaults, with any new keys from the default filled in so future
        rules never run 'off' by surprise).
    """
    defaults = load_default_guardian_config()
    path = user_path if user_path is not None else _user_config_path()
    if not path.is_file():
        return defaults

    try:
        user_raw = path.read_text()
    except OSError as exc:
        if strict:
            raise GuardianConfigError(f"cannot read {path}: {exc}") from exc
        return defaults
    try:
        user_cfg = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise GuardianConfigError(f"{path} invalid JSON: {exc}") from exc
        return defaults

    merged = _deep_merge_with_rule_fallback(defaults, user_cfg)
    errors = validate_guardian_config(merged)
    if errors:
        if strict:
            raise GuardianConfigError("; ".join(errors))
        # Non-strict: log-and-fallback is the caller's responsibility.
    return merged


def _deep_merge_with_rule_fallback(
    defaults: dict[str, Any],
    user: dict[str, Any],
) -> dict[str, Any]:
    """Shallow merge for top-level keys, rule-by-rule for ``rules``.

    A new rule added in the packaged default must NEVER run 'off' just
    because the user's older guardian.json does not mention it. So for
    ``rules`` we start from defaults and overlay the user's values.
    Same logic for ``core_rules`` (static) and ``fail_closed`` (a new
    fail path added in a release must take effect immediately).
    """
    merged = dict(defaults)
    for key, value in (user or {}).items():
        if key in ("rules", "fail_closed", "telemetry", "dedup", "runtime_overrides"):
            base = dict(defaults.get(key, {}))
            if isinstance(value, dict):
                base.update(value)
            merged[key] = base
        else:
            merged[key] = value
    return merged


def validate_guardian_config(config: dict[str, Any]) -> list[str]:
    """Return a list of human-readable error strings.

    Checks:

      - `rules` is a dict.
      - Every mode value is one of {off, shadow, soft, hard}.
      - CORE_RULES are not 'off' (Fase 2 spec 0.19).
      - `core_rules` list in config, if present, matches CORE_RULES
        exactly so docs and code cannot drift.
      - `fail_closed.classifier_timeout_seconds` is a positive number.

    An empty list means the config is valid.
    """
    errors: list[str] = []
    rules = config.get("rules")
    if not isinstance(rules, dict):
        errors.append("`rules` must be a dict of {rule_id: mode}")
        return errors

    for rule_id, mode in rules.items():
        mode_str = str(mode or "").strip().lower()
        if mode_str not in VALID_MODES:
            errors.append(
                f"rule {rule_id!r} has invalid mode {mode!r} "
                f"(expected one of {sorted(VALID_MODES)})"
            )
            continue
        if rule_id in CORE_RULES and mode_str == "off":
            errors.append(
                f"core rule {rule_id!r} cannot be disabled "
                f"(mode=off not allowed for {sorted(CORE_RULES)})"
            )

    # All core rules must be present.
    missing_core = sorted(CORE_RULES - set(rules.keys()))
    if missing_core:
        errors.append(
            f"core rules missing from `rules`: {missing_core}"
        )

    declared_core = config.get("core_rules")
    if declared_core is not None:
        if not isinstance(declared_core, list):
            errors.append("`core_rules` must be a list when present")
        elif set(declared_core) != CORE_RULES:
            # Allow the declared list to be a subset or different ordering,
            # but diverging from CORE_RULES is a drift bug we want to
            # surface early.
            diff_added = set(declared_core) - CORE_RULES
            diff_missing = CORE_RULES - set(declared_core)
            parts = []
            if diff_missing:
                parts.append(f"missing {sorted(diff_missing)}")
            if diff_added:
                parts.append(f"extra {sorted(diff_added)}")
            errors.append(
                "`core_rules` in config diverges from module CORE_RULES: "
                + ", ".join(parts)
            )

    fail_closed = config.get("fail_closed") or {}
    timeout = fail_closed.get("classifier_timeout_seconds")
    if timeout is not None:
        try:
            if float(timeout) <= 0:
                errors.append("`fail_closed.classifier_timeout_seconds` must be > 0")
        except (TypeError, ValueError):
            errors.append("`fail_closed.classifier_timeout_seconds` must be numeric")

    return errors


def rule_mode(config: dict[str, Any], rule_id: str, default: str = "shadow") -> str:
    """Return the current mode for a rule, honoring runtime overrides.

    Respects ``runtime_overrides`` path if configured and the override
    file exists. Override entries that have expired (``expires_at`` in
    the past) are ignored; callers running a long session should re-load
    the config periodically.
    """
    mode = str(config.get("rules", {}).get(rule_id, default)).strip().lower()
    if rule_id in CORE_RULES and mode == "off":
        # Defence in depth: even if a bad config slipped past validation,
        # never return 'off' for a core rule at read time.
        return "shadow"
    if mode not in VALID_MODES:
        return default

    # Runtime override (Fase 2 spec 0.17). Kill-switch tool
    # nexo_guardian_rule_override writes to this file; we honour time-
    # limited entries without a restart.
    overrides_cfg = config.get("runtime_overrides") or {}
    if not overrides_cfg.get("enabled", True):
        return mode
    raw_path = overrides_cfg.get(
        "path",
        "~/.nexo/personal/config/guardian-runtime-overrides.json",
    )
    try:
        override_path = Path(os.path.expanduser(str(raw_path)))
        if override_path.is_file():
            data = json.loads(override_path.read_text() or "{}")
            entry = (data.get(rule_id) or {}) if isinstance(data, dict) else {}
            override_mode = str(entry.get("mode", "") or "").strip().lower()
            expires_at = entry.get("expires_at")
            if override_mode in VALID_MODES:
                if expires_at is None or float(expires_at) > __import__("time").time():
                    if rule_id in CORE_RULES and override_mode == "off":
                        return "shadow"
                    return override_mode
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # Fail-closed: corrupt override file → ignore and return base mode.
        pass

    return mode


__all__ = [
    "CORE_RULES",
    "VALID_MODES",
    "GuardianConfigError",
    "load_default_guardian_config",
    "load_guardian_config",
    "validate_guardian_config",
    "rule_mode",
]
