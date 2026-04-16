"""Single source of truth for default model recommendations.

Values are loaded from `src/model_defaults.json` at runtime. The same JSON is
read by `bin/nexo-brain.js` during install/onboarding so that Python and JS
stay in sync automatically. Do not hardcode model defaults elsewhere — import
from this module (or read the JSON) so editing one file updates both runtimes.

When a new model is recommended, bump the client's `recommendation_version`
and append the previous default to `previous_defaults`. Existing users whose
model is in `[model] + previous_defaults` will be offered a one-time upgrade
prompt on their next interactive `nexo update`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_FALLBACK: dict[str, Any] = {
    "schema_version": 1,
    "claude_code": {
        "model": "claude-opus-4-7[1m]",
        "reasoning_effort": "max",
        "display_name": "Opus 4.7 with 1M context",
        "recommendation_version": 2,
        "previous_defaults": ["claude-opus-4-6[1m]"],
    },
    "codex": {
        "model": "gpt-5.4",
        "reasoning_effort": "xhigh",
        "display_name": "GPT-5.4 with max reasoning",
        "recommendation_version": 1,
        "previous_defaults": [],
    },
}


def _json_path() -> Path:
    return Path(__file__).resolve().parent / "model_defaults.json"


def _load_raw() -> dict[str, Any]:
    try:
        text = _json_path().read_text()
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _FALLBACK


def load_defaults() -> dict[str, Any]:
    """Return the raw defaults mapping, preferring the JSON and falling back
    to hardcoded values if the file is missing or malformed."""
    return _load_raw()


def client_default(client: str) -> dict[str, Any]:
    """Return normalized default config for a single client."""
    raw = _load_raw()
    fallback = _FALLBACK.get(client) or {}
    entry = raw.get(client) if isinstance(raw.get(client), dict) else fallback
    return {
        "model": str(entry.get("model") or fallback.get("model") or ""),
        "reasoning_effort": str(
            entry.get("reasoning_effort")
            if entry.get("reasoning_effort") is not None
            else fallback.get("reasoning_effort") or ""
        ),
        "display_name": str(entry.get("display_name") or fallback.get("display_name") or ""),
        "recommendation_version": int(entry.get("recommendation_version") or 0),
        "previous_defaults": [
            str(item) for item in (entry.get("previous_defaults") or []) if item
        ],
    }


def was_nexo_default(client: str, model: str) -> bool:
    """True if `model` is (or ever was) a NEXO recommended default for
    `client`. Used to distinguish a user who was riding our defaults (and
    should be offered upgrades) from one who customized (respect their choice)."""
    if not model:
        return False
    cfg = client_default(client)
    if model == cfg["model"]:
        return True
    return model in cfg["previous_defaults"]


_CLAUDE_MODEL_PREFIXES = ("claude", "opus", "sonnet", "haiku")


def looks_like_claude_model(model: str) -> bool:
    """Heuristic to detect a Claude-family model written where a Codex model
    is expected. Used by the heal path for users hit by the historical
    DEFAULT_CODEX_MODEL = DEFAULT_CLAUDE_CODE_MODEL alias bug."""
    return str(model or "").strip().lower().startswith(_CLAUDE_MODEL_PREFIXES)


_OPUS_46_PREFIX = "claude-opus-4-6"


def heal_runtime_profiles(profiles: dict) -> tuple[dict, list[str]]:
    """Detect and repair invalid models in client_runtime_profiles. Returns
    (healed_profiles_dict, list_of_heal_messages). Handles two cases:
    1. Claude-family model in the codex profile (historical bug).
    2. Opus 4.6 → 4.7 auto-migration for claude_code users on a NEXO default."""
    if not isinstance(profiles, dict):
        return profiles, []
    healed = dict(profiles)
    messages: list[str] = []

    # --- Codex heal (historical bug: Claude model in codex slot) ---
    codex_profile = healed.get("codex") if isinstance(healed.get("codex"), dict) else None
    if codex_profile is not None:
        current = str(codex_profile.get("model") or "").strip()
        if current and looks_like_claude_model(current):
            default = client_default("codex")
            healed["codex"] = {
                "model": default["model"],
                "reasoning_effort": default["reasoning_effort"],
            }
            messages.append(
                f"Healed Codex profile: model '{current}' → '{default['model']}' "
                f"(Claude models are invalid for Codex)."
            )

    # --- Opus 4.6 → 4.7 auto-migration for claude_code ---
    cc_profile = healed.get("claude_code") if isinstance(healed.get("claude_code"), dict) else None
    if cc_profile is not None:
        cc_model = str(cc_profile.get("model") or "").strip()
        if cc_model.startswith(_OPUS_46_PREFIX):
            default = client_default("claude_code")
            suffix = cc_model[len(_OPUS_46_PREFIX):]
            new_model = f"claude-opus-4-7{suffix}"
            old_effort = str(cc_profile.get("reasoning_effort") or "").strip()
            new_effort = default["reasoning_effort"]
            healed["claude_code"] = dict(cc_profile)
            healed["claude_code"]["model"] = new_model
            if old_effort in ("", "xhigh", "enabled"):
                healed["claude_code"]["reasoning_effort"] = new_effort
            messages.append(
                f"Auto-migrated Claude Code: '{cc_model}' → '{new_model}', "
                f"effort '{old_effort or '(empty)'}' → '{healed['claude_code']['reasoning_effort']}'."
            )

    return healed, messages


def detect_outdated_recommendations(
    preferences: dict,
) -> dict:
    """Classify clients into "needs interactive prompt" vs "silent ack".

    Returns a dict with two keys:
      - ``pending``: list of entries that require an interactive prompt
        because the user is still on an older NEXO-recommended model and a
        newer recommendation is available.
      - ``auto_ack``: mapping of ``{client: recommendation_version}`` that
        should be acknowledged silently without prompting (because either
        the user already matches the current recommended model, or they
        have customized their model and we must respect that silently).

    Saving the ``auto_ack`` entries prevents repeated stderr spam in
    non-interactive (cron/headless) update runs.

    ``pending`` entry shape:
      {
        "client": "codex",
        "current_model": "gpt-5.9",
        "current_effort": "high",
        "current_version": 2,
        "user_model": "gpt-5.4",
        "user_effort": "xhigh",
        "display_name": "GPT-5.9 with high reasoning",
      }
    """
    pending: list[dict] = []
    auto_ack: dict[str, int] = {}
    profiles = preferences.get("client_runtime_profiles") or {}
    acknowledged = preferences.get("acknowledged_model_recommendations") or {}
    for client in ("claude_code", "codex"):
        profile = profiles.get(client) if isinstance(profiles.get(client), dict) else None
        if not profile:
            continue
        user_model = str(profile.get("model") or "").strip()
        user_effort = str(profile.get("reasoning_effort") or "").strip()
        default = client_default(client)
        current_v = int(default.get("recommendation_version") or 0)
        ack_v = int(acknowledged.get(client) or 0)
        if current_v <= ack_v:
            continue
        # User customized their model entirely (not a previously recommended
        # NEXO default) — respect their choice and record ack silently so we
        # don't log a hint every `nexo update`.
        if not was_nexo_default(client, user_model):
            auto_ack[client] = current_v
            continue
        # User is already on the current recommended model. Even if their
        # reasoning_effort differs (e.g. they picked "max" at onboarding and
        # the JSON stores ""), nothing to migrate — their effort is a
        # personal choice layered on top of the recommended model.
        if user_model == default["model"]:
            auto_ack[client] = current_v
            continue
        # User is on a prior NEXO default and the current recommendation is
        # a different model → offer interactive upgrade.
        pending.append({
            "client": client,
            "current_model": default["model"],
            "current_effort": default["reasoning_effort"],
            "current_version": current_v,
            "user_model": user_model,
            "user_effort": user_effort,
            "display_name": default.get("display_name") or default["model"],
        })
    return {"pending": pending, "auto_ack": auto_ack}
