"""Agent-facing catalog and mutation tools for NEXO Desktop preferences."""

from __future__ import annotations

import json


def handle_desktop_preferences_catalog(query: str = "", include_values: bool = True, locale: str = "es") -> str:
    from preference_catalog import build_preference_catalog

    return json.dumps(
        build_preference_catalog(
            include_values=bool(include_values),
            query=str(query or "").strip() or None,
            locale=str(locale or "es"),
        ),
        ensure_ascii=False,
    )


def handle_desktop_preference_get(id: str) -> str:
    from preference_catalog import explain_preference

    return json.dumps(explain_preference(id), ensure_ascii=False)


def handle_desktop_preference_explain(id: str) -> str:
    from preference_catalog import explain_preference

    return json.dumps(explain_preference(id), ensure_ascii=False)


def handle_desktop_preference_set(id: str, value: str, dry_run: bool = False) -> str:
    from preference_catalog import set_preference

    return json.dumps(
        set_preference(id, value, dry_run=bool(dry_run)),
        ensure_ascii=False,
    )


TOOLS = [
    (
        handle_desktop_preferences_catalog,
        "nexo_desktop_preferences_catalog",
        "List the settings and automation preferences NEXO can explain or change for the operator.",
    ),
    (
        handle_desktop_preference_get,
        "nexo_desktop_preference_get",
        "Read one preference by id or alias, including its current value when available.",
    ),
    (
        handle_desktop_preference_explain,
        "nexo_desktop_preference_explain",
        "Explain what one Desktop/Brain preference means and where it is stored.",
    ),
    (
        handle_desktop_preference_set,
        "nexo_desktop_preference_set",
        "Change one supported preference by id or alias. Use dry_run=true to preview.",
    ),
]
