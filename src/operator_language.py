from __future__ import annotations

"""Operator-language helpers shared by prompts, hooks, and automations."""

from functools import lru_cache

from core_prompts import render_core_prompt


_LANGUAGE_LABELS = {
    "ca": "Catalan (ca)",
    "de": "German (de)",
    "en": "English (en)",
    "es": "Spanish (es)",
    "fr": "French (fr)",
    "gl": "Galician (gl)",
    "eu": "Basque (eu)",
    "it": "Italian (it)",
    "ja": "Japanese (ja)",
    "pt": "Portuguese (pt)",
    "zh": "Chinese (zh)",
}


def normalize_operator_language(value: str | None = "") -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    primary = raw.split("-", 1)[0]
    return primary or raw


def load_operator_language() -> str:
    try:
        from calibration_runtime import load_runtime_calibration
        from paths import brain_dir

        payload = load_runtime_calibration(brain_dir() / "calibration.json")
    except Exception:
        payload = {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    return normalize_operator_language(
        str(user.get("language") or "").strip()
        or str(payload.get("language") or "").strip()
        or str(payload.get("lang") or "").strip()
    )


def describe_operator_language(language: str | None = "") -> str:
    normalized = normalize_operator_language(language)
    if not normalized:
        return "the user's current conversation language"
    return _LANGUAGE_LABELS.get(normalized, f"{normalized} language")


@lru_cache(maxsize=8)
def build_operator_language_contract(language: str | None = "") -> str:
    label = describe_operator_language(language or load_operator_language())
    return render_core_prompt(
        "operator-language-contract",
        operator_language_label=label,
    )


def append_operator_language_contract(prompt: str, language: str | None = "") -> str:
    clean = str(prompt or "").strip()
    if not clean:
        return clean
    contract = build_operator_language_contract(language).strip()
    if contract and contract not in clean:
        clean = f"{clean} {contract}"
    return clean


__all__ = [
    "append_operator_language_contract",
    "build_operator_language_contract",
    "describe_operator_language",
    "load_operator_language",
    "normalize_operator_language",
]
