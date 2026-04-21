from __future__ import annotations

import json


def test_task_open_accepts_description_alias_and_create_task_type(monkeypatch, isolated_db):
    import plugins.protocol as protocol

    monkeypatch.setattr(protocol, "handle_guard_check", lambda files="", area="": "OK")

    raw = protocol.handle_task_open(
        sid="nexo-1776762228-40323",
        description="Create a new reusable task contract",
        task_type="create",
        files="notes.txt",
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["goal"] == "Create a new reusable task contract"
    assert payload["task_type"] == "edit"


def test_task_close_accepts_result_completed_and_summary_aliases(isolated_db):
    import plugins.protocol as protocol

    opened = json.loads(
        protocol.handle_task_open(
            sid="nexo-1776762228-40324",
            goal="Answer a short question",
            task_type="answer",
        )
    )

    closed = json.loads(
        protocol.handle_task_close(
            sid="nexo-1776762228-40324",
            task_id=opened["task_id"],
            result="completed",
            summary="Closed through compatibility aliases with enough context to count as substantive evidence.",
        )
    )

    assert closed["ok"] is True
    assert closed["outcome"] == "done"


def test_protocol_debt_list_accepts_sid_alias_and_resolve_accepts_debt_id(isolated_db):
    from db import create_protocol_debt
    from plugins.protocol import handle_protocol_debt_list, handle_protocol_debt_resolve

    debt = create_protocol_debt(
        "nexo-1776762228-40325",
        "missing_followup_payload",
        severity="warn",
        evidence="compatibility alias smoke",
    )

    listed = json.loads(handle_protocol_debt_list(sid="nexo-1776762228-40325"))
    assert listed["count"] >= 1
    assert any(item["id"] == debt["id"] for item in listed["items"])

    resolved = json.loads(handle_protocol_debt_resolve(debt_id=str(debt["id"])))
    assert resolved["ok"] is True
    assert debt["id"] in resolved["matched_ids"]


def test_tool_explain_accepts_tool_name_alias(monkeypatch):
    import tools_system_catalog

    monkeypatch.setattr(tools_system_catalog, "explain_tool", lambda name: {"name": name})
    monkeypatch.setattr(tools_system_catalog, "format_tool_explanation", lambda payload: payload["name"])

    assert tools_system_catalog.handle_tool_explain(tool_name="nexo_task_open") == "nexo_task_open"


def test_pre_action_context_accepts_intent_and_area_aliases(monkeypatch):
    import tools_hot_context

    seen = {}

    def _fake_build_pre_action_context(**kwargs):
        seen.update(kwargs)
        return {"has_matches": False, "contexts": [], "events": [], "reminders": [], "followups": [], "query": kwargs["query"]}

    monkeypatch.setattr(tools_hot_context, "build_pre_action_context", _fake_build_pre_action_context)
    monkeypatch.setattr(tools_hot_context, "format_pre_action_context_bundle", lambda bundle: bundle["query"])

    rendered = tools_hot_context.handle_pre_action_context(intent="rename email config", area="email")

    assert rendered == "rename email config | email"
    assert seen["query"] == "rename email config | email"


def test_rules_list_accepts_filter_aliases_and_limit(isolated_db):
    from plugins.core_rules import handle_rules_list

    rendered = handle_rules_list(filter_severity="block", limit=2)
    rule_lines = [line for line in rendered.splitlines() if line.strip().startswith("[")]

    assert "CORE RULES v" in rendered
    assert len(rule_lines) <= 2


def test_skill_list_accepts_status_alias_and_limit(isolated_db):
    from plugins.skills import handle_skill_create, handle_skill_list

    created = handle_skill_create(id="SK-ALIAS-TEST", name="Alias test skill", level="draft")
    assert "created" in created.lower()

    rendered = handle_skill_list(status="draft", limit=1)

    assert "SKILLS (" in rendered
    assert "SK-ALIAS-TEST" in rendered


def test_guard_cross_check_accepts_task_alias(isolated_db):
    from plugins.guard import handle_guard_cross_check

    rendered = handle_guard_cross_check(task="Review duplicate plugin registry rows", area="nexo-ops")

    assert "CROSS-CHECK RESULTS: 1 findings" in rendered


def test_personal_scripts_list_supports_limit_filter_source_and_summary(monkeypatch, isolated_db):
    import plugins.personal_scripts as personal_scripts

    monkeypatch.setattr(personal_scripts, "init_db", lambda: None)
    monkeypatch.setattr(personal_scripts, "sync_personal_scripts", lambda: {"ok": True})
    monkeypatch.setattr(
        personal_scripts,
        "list_personal_scripts",
        lambda: [
            {
                "id": "ps-1",
                "name": "alpha",
                "description": "user python script",
                "runtime": "python",
                "origin": "user",
                "path": "/tmp/alpha.py",
                "has_schedule": True,
            },
            {
                "id": "ps-2",
                "name": "beta",
                "description": "user shell script",
                "runtime": "shell",
                "origin": "user",
                "path": "/tmp/beta.sh",
                "has_schedule": False,
            },
            {
                "id": "ps-3",
                "name": "gamma",
                "description": "core python script",
                "runtime": "python",
                "origin": "core",
                "path": "/tmp/gamma.py",
                "has_schedule": False,
            },
        ],
    )

    payload = json.loads(
        personal_scripts.handle_personal_scripts_list(
            limit=1,
            filter_source="user",
            filter_runtime="python",
            summary=True,
        )
    )

    assert payload["ok"] is True
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["filters"]["origin"] == "user"
    assert payload["filters"]["runtime"] == "python"
    assert payload["summary"]["by_origin"] == {"user": 1}
    assert payload["summary"]["by_runtime"] == {"python": 1}
    assert payload["scripts"][0]["name"] == "alpha"


def test_doctor_requires_plane_as_input_error(monkeypatch):
    import plugins.doctor as doctor_plugin

    rendered = doctor_plugin.handle_doctor(output="json")
    payload = json.loads(rendered)

    assert payload["ok"] is False
    assert payload["missing_argument"] == "plane"
    assert "installation_live" in payload["valid_planes"]
