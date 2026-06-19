from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_morning_agent(name: str = "nexo_morning_agent_contract_test"):
    path = SRC / "scripts" / "nexo-morning-agent.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_morning_agent_prompt_sets_start_of_day_assistant_intent():
    prompt = (SRC.parent / "templates" / "core-prompts" / "morning-agent.md").read_text(encoding="utf-8")

    assert "start-of-day briefing" in prompt
    assert "professional personal assistant" in prompt
    assert "Do not ask the operator to choose a user type" in prompt
    assert "Include news and weather only when verified collected data exists" in prompt
    assert "Public headlines are not a generic news block" in prompt
    assert "stale_without_recent_signal" in prompt
    assert "Never reconstruct an old crisis" in prompt
    assert "recent_history" in prompt
    assert "Do not duplicate the same topic" in prompt
    assert "Never say \"authorized\"" in prompt


def test_followup_recency_fields_expose_age_and_staleness(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_recency_contract_test")

    class FixedDateTime(module.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls.fromisoformat("2026-06-15T09:00:00+02:00")
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    fields = module._followup_recency_fields({
        "created_at": "2026-06-07T02:20:00+00:00",
        "updated_at": "2026-06-10T07:01:00+00:00",
    })

    assert fields["days_open"] == 8
    assert fields["days_since_activity"] == 5
    assert fields["stale_without_recent_signal"] is True
    assert fields["last_activity"].startswith("2026-06-10")


def test_followup_serialization_exposes_history_resolution_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_history_contract_test")

    monkeypatch.setattr(
        module.nexo_db,
        "get_followups",
        lambda _filter: [
            {
                "id": "NF-VICSHOP-STOCK-EMAIL-TEMPLATE-20260616",
                "description": "Decide Vicshop sender and continue stock false-positive review",
                "date": "2026-06-18",
                "priority": "high",
                "owner": "agent",
                "status": "PENDING",
                "verification": "Stock false-positive still open",
                "reasoning": "Sender may be stale in description",
                "created_at": "2026-06-17T20:00:00+00:00",
                "updated_at": "2026-06-18T09:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        module.nexo_db,
        "get_item_history",
        lambda item_type, item_id, limit=5: [
            {
                "event_type": "decision",
                "actor": "nexo",
                "note": "Remitente decidido: opcion B, nero@nexoagentmail.com.",
                "created_at": "2026-06-17T23:02:00+00:00",
            }
        ],
    )

    items = module._serialize_followups("active", limit=5)

    assert len(items) == 1
    item = items[0]
    assert item["resolution_state"] == "resolved_or_decided_signal"
    assert item["has_resolution_signal"] is True
    assert item["recent_history"][0]["event_type"] == "decision"
    assert "Do not present resolved/decided subtopics" in item["status_claim_guard"]


def test_context_dedupe_removes_repeated_topic_across_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_dedupe_contract_test")

    first = [{"id": "F1", "description": "Vicshop sender decision stock alert format"}]
    duplicate = [{"id": "R1", "description": "Vicshop sender decision stock alert format"}]

    kept_first, kept_duplicate = module._dedupe_context_groups(first, duplicate)

    assert kept_first == first
    assert kept_duplicate == []


def test_collect_news_uses_interests_and_exclusions(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_news_contract_test")
    fetched_urls = []
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
      <item>
        <title>Technology update for business teams</title>
        <source>Example News</source>
        <pubDate>Wed, 03 Jun 2026 07:00:00 GMT</pubDate>
        <link>https://example.test/tech</link>
      </item>
      <item>
        <title>Football result of the day</title>
        <source>Sports News</source>
        <pubDate>Wed, 03 Jun 2026 07:00:00 GMT</pubDate>
        <link>https://example.test/sport</link>
      </item>
    </channel></rss>"""

    def fake_fetch(url, **_kwargs):
        fetched_urls.append(url)
        return xml

    monkeypatch.setattr(module, "_fetch_text_url", fake_fetch)
    result = module._collect_news(
        {"language": "es", "current_residence": "Mallorca", "role": "founder"},
        {"news_interests": ["technology", "local"], "excluded_topics": ["sports"]},
    )

    assert result["available"] is True
    assert result["source"] == "google-news-rss"
    assert result["mode"] == "relevant_public_context"
    assert result["interests"] == ["technology", "local"]
    assert "sports" in result["excluded_topics"]
    assert len(result["headlines"]) == 1
    assert result["headlines"][0]["interest"] == "technology"
    assert "Football" not in result["headlines"][0]["title"]
    assert fetched_urls and "rss/search" in fetched_urls[0]
    assert "NEXO_NEWS_RSS_URL" not in fetched_urls[0]


class _Result:
    returncode = 0
    stderr = ""

    def __init__(self, payload):
        self.stdout = json.dumps(payload)


def test_generate_briefing_accepts_legacy_body(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_legacy_contract_test")
    monkeypatch.setattr(module, "resolve_automation_backend", lambda: "none")
    monkeypatch.setattr(module, "run_automation_prompt", lambda *a, **k: _Result({
        "subject": "Hello",
        "body": "Plain body",
    }))

    presentation = module.generate_briefing("prompt")

    assert presentation.subject == "Hello"
    assert presentation.body_text == "Plain body"
    assert "<p>Plain body</p>" in presentation.body_html


def test_generate_briefing_sanitizes_new_body_html(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_html_contract_test")
    monkeypatch.setattr(module, "resolve_automation_backend", lambda: "none")
    monkeypatch.setattr(module, "run_automation_prompt", lambda *a, **k: _Result({
        "subject": "Hello",
        "body_text": "Plain body",
        "body_html": "<p onclick='x()'>Plain body</p><script>bad()</script>",
    }))

    presentation = module.generate_briefing("prompt")

    assert "onclick" not in presentation.body_html
    assert "script" not in presentation.body_html.lower()
    assert "<p>Plain body</p>" in presentation.body_html


def test_send_briefing_passes_html_file_and_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo"))
    module = _load_morning_agent("nexo_morning_agent_send_contract_test")
    sender = tmp_path / "nexo-send-reply.py"
    sender.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setattr(module, "get_send_reply_script_path", lambda local_script_dir=None: sender)

    calls = {}

    def fake_run(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        class Completed:
            returncode = 0
            stdout = "OK:<id>"
            stderr = ""
        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    output = module.send_briefing(
        recipient="user@example.com",
        subject="Subject",
        body_text="Body",
        body_html="<p>Body</p>",
    )

    assert output == "OK:<id>"
    assert "--html-file" in calls["args"]
    assert calls["args"][calls["args"].index("--message-kind") + 1] == "morning_briefing"
