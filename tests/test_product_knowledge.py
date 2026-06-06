from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_product_knowledge_catalog_is_valid():
    from product_knowledge import list_capabilities, validate_catalog

    assert validate_catalog() == []
    capabilities = list_capabilities()
    assert len(capabilities) >= 8
    assert {capability["id"] for capability in capabilities} >= {
        "nexo_product_self_knowledge",
        "nexo_desktop_settings",
        "nexo_credits_provider_proxy",
    }


def test_product_knowledge_search_and_answer_are_source_safe():
    from product_knowledge import answer_product_question, find_capabilities

    matches = find_capabilities("credits provider models", limit=3)
    assert matches
    assert matches[0]["id"] == "nexo_credits_provider_proxy"
    answer = answer_product_question("Can NEXO spend credits on video models?")
    assert "backend" in answer.lower()
    assert "verific" in answer.lower()


def test_product_knowledge_feeds_system_catalog():
    from system_catalog import build_system_catalog

    catalog = build_system_catalog()
    names = {entry["name"] for entry in catalog["product_capabilities"]}
    assert "nexo_desktop_settings" in names
    assert "nexo_credits_provider_proxy" in names


def test_product_knowledge_tool_handlers_return_json():
    from tools_product_knowledge import (
        handle_capability_explain,
        handle_product_capabilities,
        handle_product_knowledge_validate,
        handle_product_surface_status,
    )

    capabilities = json.loads(handle_product_capabilities(query="email"))
    assert capabilities["ok"] is True
    assert capabilities["capabilities"]
    explanation = json.loads(handle_capability_explain("nexo_agent_email"))
    assert explanation["capability"]["category"] == "email"
    surface = json.loads(handle_product_surface_status("Desktop"))
    assert surface["ok"] is True
    assert surface["capabilities"]
    validation = json.loads(handle_product_knowledge_validate())
    assert validation["ok"] is True


def test_product_knowledge_validate_handler_reports_catalog_errors(monkeypatch):
    import tools_product_knowledge

    monkeypatch.setattr(tools_product_knowledge, "validate_catalog", lambda: ["catalog invalid"])
    monkeypatch.setattr(
        tools_product_knowledge,
        "list_capabilities",
        lambda: (_ for _ in ()).throw(AssertionError("must not count invalid catalog")),
    )

    validation = json.loads(tools_product_knowledge.handle_product_knowledge_validate())
    assert validation == {"ok": False, "errors": ["catalog invalid"], "capability_count": 0}
