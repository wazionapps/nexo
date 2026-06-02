from __future__ import annotations

import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_sanitize_html_removes_scripts_handlers_and_javascript_urls():
    from email_presentation import sanitize_html_fragment

    dirty = (
        '<p onclick="steal()">Hello <strong>world</strong></p>'
        '<img src=x onerror="steal()">'
        '<script>alert(1)</script>'
        '<a href="javascript:alert(1)">bad</a>'
        '<a href="https://example.com">good</a>'
    )
    clean = sanitize_html_fragment(dirty)

    assert "script" not in clean.lower()
    assert "onclick" not in clean.lower()
    assert "onerror" not in clean.lower()
    assert "javascript:" not in clean.lower()
    assert "<strong>world</strong>" in clean
    assert '<a href="https://example.com">good</a>' in clean


def test_normalize_agent_email_payload_accepts_legacy_body_and_sanitizes_html():
    from email_presentation import normalize_agent_email_payload

    presentation = normalize_agent_email_payload({
        "subject": "  Daily   briefing ",
        "body": "Plain fallback",
        "body_html": "<h2>Today</h2><p>Focus</p><script>bad()</script>",
    })

    assert presentation.subject == "Daily briefing"
    assert presentation.body_text == "Plain fallback"
    assert "<script" not in presentation.body_html.lower()
    assert "<h2>Today</h2>" in presentation.body_html


def test_signature_from_config_reads_account_metadata():
    from email_presentation import build_email_presentation, signature_from_config

    config = {"agent_account": {"metadata": {"signature": "Nero\nNEXO"}}}
    signature = signature_from_config(config, fallback="fallback")
    presentation = build_email_presentation(
        subject="Hello",
        body_text="Body",
        signature=signature,
        include_signature=True,
    )

    assert signature == "Nero\nNEXO"
    assert "Nero" in presentation.body_text
    assert "NEXO" in presentation.body_html
