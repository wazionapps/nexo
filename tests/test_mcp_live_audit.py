from __future__ import annotations

import json

from mcp_live_audit import (
    CATALOG_ONLY,
    DEFERRED,
    INACTIVE,
    LIVE,
    MISSING,
    audit_client_probe,
    audit_mcp_live,
    explain_tool_status,
    format_markdown,
    parse_probe_json,
)


def _core_tool_names() -> list[str]:
    required = [
        "nexo_startup",
        "nexo_heartbeat",
        "nexo_session_diary_read",
        "nexo_session_diary_write",
        "nexo_session_compliance_state",
        "nexo_reminders",
        "nexo_smart_startup",
        "nexo_task_open",
        "nexo_task_close",
        "nexo_task_acknowledge_guard",
        "nexo_guard_check",
        "nexo_learning_add",
        "nexo_confidence_check",
        "nexo_followup_create",
        "nexo_protocol_debt_resolve",
        "nexo_card_match",
        "nexo_skill_match",
    ]
    live_plugin_duplicates = [
        "nexo_cortex_check",
        "nexo_workflow_open",
        "nexo_workflow_update",
    ]
    fillers = [f"nexo_core_{idx:03d}" for idx in range(1, 101)]
    assert len(required + live_plugin_duplicates + fillers) == 120
    return required + live_plugin_duplicates + fillers


def _plugin_records() -> list[dict]:
    core_duplicates = [
        "nexo_card_match",
        "nexo_confidence_check",
        "nexo_guard_check",
        "nexo_session_diary_read",
        "nexo_session_diary_write",
        "nexo_skill_match",
        "nexo_task_close",
        "nexo_task_open",
        "nexo_workflow_open",
        "nexo_workflow_update",
        "nexo_protocol_debt_resolve",
        "nexo_cortex_check",
        "nexo_task_acknowledge_guard",
    ]
    plugin_only = [f"nexo_plugin_{idx:03d}" for idx in range(1, 185)]
    names = core_duplicates + plugin_only
    assert len(names) == 197

    rows: list[dict] = []
    cursor = 0
    for idx in range(33):
        size = 6 if idx < 32 else 5
        chunk = names[cursor : cursor + size]
        cursor += size
        rows.append(
            {
                "filename": f"plugin_{idx:02d}.py",
                "tools_count": len(chunk),
                "tool_names": ",".join(chunk),
                "loaded_at": 1779134597.7631092,
                "created_by": "repo",
            }
        )
    assert cursor == len(names)
    return rows


def test_audit_reconciles_120_live_33_plugins_197_registered_fixture():
    live_tools = _core_tool_names()
    plugin_records = _plugin_records()
    plugin_tools = [
        name
        for row in plugin_records
        for name in row["tool_names"].split(",")
    ]
    catalog_tools = sorted(set(live_tools) | set(plugin_tools))

    report = audit_mcp_live(
        {
            "client": "codex",
            "plugin_mode": "none",
            "tool_names": live_tools,
            "tool_count": 120,
            "ok": True,
            "mcp_ready": True,
        },
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        required_tools=live_tools[:17],
    )

    assert report["ok"] is True
    assert report["summary"]["plugin_rows"] == 33
    assert report["summary"]["registered_plugin_tools_raw"] == 197
    assert report["summary"]["registered_plugin_tools_unique"] == 197
    assert report["summary"]["catalog_tools"] == 304

    client = report["clients"][0]
    counts = client["counts"]
    assert counts["live_tools"] == 120
    assert counts["plugin_rows"] == 33
    assert counts["registered_plugin_tools_raw"] == 197
    assert counts["registered_plugin_tools_unique"] == 197
    assert counts["registered_plugin_tools_live"] == 13
    assert counts["registered_not_live"] == 184
    assert counts["catalog_tools"] == 304
    assert counts["catalog_not_live"] == 184
    assert counts["required_tools"] == 17
    assert counts["required_missing"] == 0


def test_explain_tool_status_distinguishes_live_deferred_inactive_catalog_only_and_missing():
    plugin_records = [
        {
            "filename": "memory.py",
            "tools_count": 1,
            "tool_names": "nexo_recall",
        }
    ]
    catalog_tools = ["nexo_startup", "nexo_task_open", "nexo_recall", "nexo_docs_only"]

    live = explain_tool_status(
        "nexo_startup",
        live_tools=["nexo_startup"],
        deferred_tools=["nexo_task_open"],
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        plugin_mode="none",
    )
    deferred = explain_tool_status(
        "nexo_task_open",
        live_tools=["nexo_startup"],
        deferred_tools=["nexo_task_open"],
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        plugin_mode="none",
    )
    inactive = explain_tool_status(
        "nexo_recall",
        live_tools=["nexo_startup"],
        deferred_tools=["nexo_task_open"],
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        plugin_mode="none",
    )
    catalog_only = explain_tool_status(
        "nexo_docs_only",
        live_tools=["nexo_startup"],
        deferred_tools=["nexo_task_open"],
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        plugin_mode="none",
    )
    missing = explain_tool_status(
        "nexo_not_real",
        live_tools=["nexo_startup"],
        deferred_tools=["nexo_task_open"],
        plugin_records=plugin_records,
        catalog_tools=catalog_tools,
        plugin_mode="none",
    )

    assert live.status == LIVE
    assert live.live is True
    assert deferred.status == DEFERRED
    assert "schema" in deferred.reason
    assert inactive.status == INACTIVE
    assert inactive.plugins == ("memory.py",)
    assert "plugin_mode=none" in inactive.reason
    assert catalog_only.status == CATALOG_ONLY
    assert missing.status == MISSING


def test_audit_client_probe_preserves_reported_count_when_names_are_missing():
    audit = audit_client_probe(
        parse_probe_json(
            json.dumps(
                {
                    "client": "claude_code",
                    "plugin_mode": "none",
                    "tool_count": 120,
                    "ok": True,
                    "mcp_ready": True,
                }
            )
        ),
        plugin_records=_plugin_records(),
        catalog_tools=_core_tool_names(),
        required_tools=["nexo_startup"],
    )

    assert audit.counts["live_tools"] == 120
    assert audit.counts["live_tool_names_known"] == 0
    assert audit.counts["required_missing"] == 1
    assert any("without names" in note for note in audit.notes)


def test_format_markdown_includes_live_registry_and_required_missing_evidence():
    report = audit_mcp_live(
        {
            "client": "codex",
            "profile": "none",
            "plugin_mode": "none",
            "tool_names": ["nexo_startup"],
            "deferred_tools": ["nexo_task_open"],
        },
        plugin_records=[{"filename": "memory.py", "tools_count": 1, "tool_names": "nexo_recall"}],
        catalog_tools=["nexo_startup", "nexo_task_open", "nexo_recall"],
        required_tools=["nexo_startup", "nexo_task_open", "nexo_recall", "nexo_missing"],
    )

    markdown = format_markdown(report)

    assert "| codex | none | none | 1 | 3 | 1 | 1 | 1 | 3 |" in markdown
    assert "`codex` `nexo_task_open`: deferred" in markdown
    assert "`codex` `nexo_recall`: inactive" in markdown
    assert "`codex` `nexo_missing`: missing" in markdown
