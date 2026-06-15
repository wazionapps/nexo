from __future__ import annotations

import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_preview_separates_internal_tests_blocks_secrets_and_caps_batch():
    from message_batch_preview import PreviewMessage, build_preview

    fake_secret = "sk-" + "a" * 20
    messages = [
        PreviewMessage(source="queue:1", channel="email", recipient="cliente1@empresa.es", body="Hola 1"),
        PreviewMessage(source="queue:2", channel="email", recipient="dev+test@company.com", body="Hola test"),
        PreviewMessage(source="queue:3", channel="whatsapp", recipient="+34600000001", body="[internal] Nota privada"),
        PreviewMessage(source="queue:4", channel="email", recipient="cliente2@empresa.es", body=f"Clave {fake_secret}"),
        PreviewMessage(source="queue:5", channel="whatsapp", recipient="+34600000002", body="Hola 2"),
        PreviewMessage(source="queue:6", channel="whatsapp", recipient="+34600000003", body="Hola 3"),
    ]

    result = build_preview(messages, real_send_limit=2)

    assert len(result.deliverable) == 3
    assert len(result.capped_deliverable) == 2
    assert result.over_limit_count == 1
    assert len(result.internal_or_test) == 2
    assert len(result.blocked) == 1
    assert "secret pattern matched" in result.blocked[0]["reason"]


def test_jsonl_reader_and_html_renderer(tmp_path: Path):
    from message_batch_preview import build_preview, read_messages, render_preview_html

    queue = tmp_path / "queue.jsonl"
    queue.write_text(
        "\n".join([
            json.dumps({"to": "real@empresa.es", "subject": "Entrega", "body": "Mensaje <b>real</b>"}),
            json.dumps({"to": "test@example.com", "body": "Mensaje de prueba"}),
        ]),
        encoding="utf-8",
    )

    messages = read_messages([queue])
    result = build_preview(messages, real_send_limit=5)
    html = render_preview_html(result)

    assert len(messages) == 2
    assert len(result.deliverable) == 1
    assert len(result.internal_or_test) == 1
    assert "<!DOCTYPE html>" in html
    assert "Previsualización de lote" in html
    assert "real@empresa.es" in html


def test_cli_writes_html_and_json(tmp_path: Path):
    from message_batch_preview import main

    payload = tmp_path / "messages.json"
    html_out = tmp_path / "preview.html"
    json_out = tmp_path / "summary.json"
    payload.write_text(
        json.dumps({"messages": [{"to": "+34611111111", "body": "WhatsApp real"}]}),
        encoding="utf-8",
    )

    assert main([str(payload), "--limit", "1", "--html-out", str(html_out), "--json-out", str(json_out)]) == 0

    assert html_out.exists()
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["deliverable_count"] == 1
    assert data["capped_deliverable_count"] == 1
    assert data["internal_or_test_count"] == 0
