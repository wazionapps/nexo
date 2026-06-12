from __future__ import annotations

import types


def test_capability_denial_without_reality_check_enqueues_prompt(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine.on_assistant_text("No se puede hacer eso; no existe esa capacidad.", declared_detector=lambda _text: False)

    assert any(item.get("tag") == "r34:capability-denial-without-reality-check" for item in engine.injection_queue)


def test_capability_denial_after_catalog_check_is_allowed(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    engine.tools_called.add("nexo_system_catalog")
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine.on_assistant_text("No se puede hacer eso; no existe esa capacidad.", declared_detector=lambda _text: False)

    assert not any(item.get("tag") == "r34:capability-denial-without-reality-check" for item in engine.injection_queue)


def test_r23g_creates_security_followup(monkeypatch):
    import enforcement_engine

    created = []
    fake_db = types.SimpleNamespace(
        get_followup=lambda _id: None,
        create_followup=lambda *args, **kwargs: created.append((args, kwargs)) or {"id": args[0]},
    )
    monkeypatch.setitem(__import__("sys").modules, "db", fake_db)

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine._check_r23g("Bash", {"command": "echo $OPENAI_API_KEY > /tmp/key.txt"})

    assert created
    followup_id = created[0][0][0]
    payload = created[0][1]
    assert followup_id.startswith("NF-SECURITY-EXPOSED-CREDENTIAL-")
    assert payload["priority"] == "critical"
    assert payload["owner"] == "agent"
    assert "HTTP 401" in payload["verification"]
