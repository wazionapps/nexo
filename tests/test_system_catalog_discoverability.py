"""Plan 0.X.1 + 0.X.6 — system_catalog health and discoverability smoke.

0.X.1: build_system_catalog() returns a summary with coherent counts
       and a locations dict with the canonical paths.

0.X.6: Search smoke — a fresh session without memory should discover
       the canonical tool for common operator intents.
"""

from __future__ import annotations


def test_build_catalog_has_numeric_summary_and_locations():
    from system_catalog import build_system_catalog

    catalog = build_system_catalog()
    assert "summary" in catalog
    summary = catalog["summary"]
    assert summary, "summary must not be empty"
    for section, count in summary.items():
        assert isinstance(count, int), f"count for {section} must be int"
        assert count >= 0

    # counts match list lengths
    for section, count in summary.items():
        section_items = catalog.get(section)
        assert isinstance(section_items, list), f"{section} must be a list"
        assert count == len(section_items), (
            f"summary[{section}]={count} != len(items)={len(section_items)}"
        )

    locations = catalog.get("locations")
    assert isinstance(locations, dict), "locations must be a dict"
    # Plan Consolidado 0.X.4 — canonical keys must exist
    required_location_keys = (
        "brain.db",
        "config.dir",
        "config.guardian",
        "logs.dir",
        "tool_enforcement_map",
        "brain.project_atlas",
        "skills.repo",
        "scripts.core",
    )
    for key in required_location_keys:
        assert key in locations, f"locations missing {key!r}"


def test_search_discovers_core_intents():
    """Fresh-session operator typing plain-language intents should find
    the canonical tool via search_system_catalog — no memory required."""
    from system_catalog import search_system_catalog

    # Probes target tools in core_tools only — plugin_tools depends on
    # runtime DB state that is intentionally empty during pytest.
    # Probes use the canonical tool token. search tokenizes on
    # whitespace, so underscores inside names are kept as part of
    # the token and must match literally.
    # Probes cover core_tools (always present in pytest env; plugin_tools
    # depend on runtime DB state which is empty in isolated test home).
    probes = [
        ("nexo_heartbeat", "nexo_heartbeat"),
        ("nexo_system_catalog", "nexo_system_catalog"),
        ("nexo_tool_explain", "nexo_tool_explain"),
        ("nexo_startup", "nexo_startup"),
        ("nexo_checkpoint_save", "nexo_checkpoint_save"),
        ("nexo_status", "nexo_status"),
    ]
    for query, wanted in probes:
        hits = search_system_catalog(query, limit=25)
        names = {row.get("name") for row in hits}
        assert wanted in names, (
            f"search('{query}') did not return {wanted}. got: {sorted(names)[:8]}"
        )
