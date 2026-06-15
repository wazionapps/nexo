"""Build safe HTML previews before real WhatsApp/email batch sends.

This module is intentionally send-agnostic: it reads code/log/queue artifacts,
separates internal or test messages from deliverable candidates, renders a
sanitized HTML review document, and enforces a hard cap on real sends.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from email_presentation import compose_html_document, text_to_html_fragment
from tools_email_guard import should_block_email_send


DEFAULT_REAL_SEND_LIMIT = 10
INTERNAL_MARKERS = (
    "[internal]",
    "internal:",
    "nexo_internal",
    "solo interno",
    "nota interna",
    "mensaje interno",
    "test:",
    "[test]",
    "dry-run",
    "dry_run",
    "prueba",
)
TEST_RECIPIENT_PATTERNS = (
    re.compile(r"(^|@)(example|test|localhost)(\.|$)", re.I),
    re.compile(r"\+test\b", re.I),
    re.compile(r"^(?:0+|123456789|600000000)$"),
)


@dataclass(frozen=True)
class PreviewMessage:
    source: str
    channel: str
    recipient: str
    body: str
    subject: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def fingerprint(self) -> str:
        base = "\x1f".join([
            self.channel.strip().lower(),
            self.recipient.strip().lower(),
            self.subject.strip(),
            " ".join(self.body.split()),
        ])
        return str(abs(hash(base)))


@dataclass(frozen=True)
class PreviewResult:
    deliverable: list[PreviewMessage]
    internal_or_test: list[PreviewMessage]
    blocked: list[dict[str, str]]
    real_send_limit: int

    @property
    def capped_deliverable(self) -> list[PreviewMessage]:
        return self.deliverable[: self.real_send_limit]

    @property
    def over_limit_count(self) -> int:
        return max(0, len(self.deliverable) - self.real_send_limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deliverable_count": len(self.deliverable),
            "capped_deliverable_count": len(self.capped_deliverable),
            "internal_or_test_count": len(self.internal_or_test),
            "blocked_count": len(self.blocked),
            "real_send_limit": self.real_send_limit,
            "over_limit_count": self.over_limit_count,
            "deliverable": [_message_to_dict(m) for m in self.capped_deliverable],
            "internal_or_test": [_message_to_dict(m) for m in self.internal_or_test],
            "blocked": self.blocked,
        }


def _message_to_dict(message: PreviewMessage) -> dict[str, Any]:
    return {
        "source": message.source,
        "channel": message.channel,
        "recipient": message.recipient,
        "subject": message.subject,
        "body": message.body,
        "metadata": message.metadata or {},
        "fingerprint": message.fingerprint,
    }


def read_messages(paths: Iterable[Path | str]) -> list[PreviewMessage]:
    messages: list[PreviewMessage] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        text = path.read_text(encoding="utf-8", errors="replace")
        messages.extend(_parse_artifact(path, text))
    return messages


def _parse_artifact(path: Path, text: str) -> list[PreviewMessage]:
    stripped = text.strip()
    if not stripped:
        return []
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in stripped.splitlines() if line.strip()]
        return [_row_to_message(row, path, index) for index, row in enumerate(rows, start=1)]
    if path.suffix.lower() == ".json":
        payload = json.loads(stripped)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("messages") or payload.get("items") or payload.get("queue") or [payload]
        else:
            rows = []
        return [_row_to_message(row, path, index) for index, row in enumerate(rows, start=1) if isinstance(row, dict)]
    return [PreviewMessage(source=str(path), channel="log", recipient="", body=stripped)]


def _row_to_message(row: dict[str, Any], path: Path, index: int) -> PreviewMessage:
    recipient = str(
        row.get("recipient")
        or row.get("to")
        or row.get("phone")
        or row.get("email")
        or ""
    ).strip()
    body = str(
        row.get("body")
        or row.get("message")
        or row.get("text")
        or row.get("html")
        or ""
    ).strip()
    channel = str(row.get("channel") or row.get("type") or _infer_channel(recipient)).strip().lower()
    subject = str(row.get("subject") or "").strip()
    return PreviewMessage(
        source=f"{path}:{index}",
        channel=channel or "unknown",
        recipient=recipient,
        subject=subject,
        body=body,
        metadata={k: v for k, v in row.items() if k not in {"body", "message", "text", "html"}},
    )


def _infer_channel(recipient: str) -> str:
    if "@" in recipient:
        return "email"
    if recipient:
        return "whatsapp"
    return "unknown"


def is_internal_or_test(message: PreviewMessage) -> bool:
    haystack = " ".join([
        message.channel,
        message.recipient,
        message.subject,
        message.body,
        json.dumps(message.metadata or {}, ensure_ascii=False, sort_keys=True),
    ]).lower()
    if any(marker in haystack for marker in INTERNAL_MARKERS):
        return True
    recipient = message.recipient.strip()
    return any(pattern.search(recipient) for pattern in TEST_RECIPIENT_PATTERNS)


def build_preview(messages: Iterable[PreviewMessage], *, real_send_limit: int = DEFAULT_REAL_SEND_LIMIT) -> PreviewResult:
    if real_send_limit < 1:
        raise ValueError("real_send_limit must be >= 1")
    deliverable: list[PreviewMessage] = []
    internal_or_test: list[PreviewMessage] = []
    blocked: list[dict[str, str]] = []
    seen: set[str] = set()

    for message in messages:
        if is_internal_or_test(message):
            internal_or_test.append(message)
            continue
        blocked_by_secret, reason = should_block_email_send(
            "\n".join([message.subject, message.body, json.dumps(message.metadata or {}, ensure_ascii=False)])
        )
        if blocked_by_secret:
            blocked.append({"source": message.source, "recipient": message.recipient, "reason": reason})
            continue
        if message.fingerprint in seen:
            blocked.append({"source": message.source, "recipient": message.recipient, "reason": "duplicate message"})
            continue
        seen.add(message.fingerprint)
        deliverable.append(message)

    return PreviewResult(
        deliverable=deliverable,
        internal_or_test=internal_or_test,
        blocked=blocked,
        real_send_limit=real_send_limit,
    )


def render_preview_html(result: PreviewResult) -> str:
    parts = [
        "<h1>Previsualización de lote</h1>",
        "<table><tbody>",
        f"<tr><th>Enviables</th><td>{len(result.deliverable)}</td></tr>",
        f"<tr><th>Incluidos por límite</th><td>{len(result.capped_deliverable)}</td></tr>",
        f"<tr><th>Internos/tests separados</th><td>{len(result.internal_or_test)}</td></tr>",
        f"<tr><th>Bloqueados</th><td>{len(result.blocked)}</td></tr>",
        f"<tr><th>Exceso de lote</th><td>{result.over_limit_count}</td></tr>",
        "</tbody></table>",
        "<h2>Candidatos a envío real</h2>",
        _render_message_list(result.capped_deliverable),
        "<h2>Separados: internos/tests</h2>",
        _render_message_list(result.internal_or_test),
        "<h2>Bloqueados</h2>",
        _render_blocked(result.blocked),
    ]
    return compose_html_document("".join(parts))


def _render_message_list(messages: list[PreviewMessage]) -> str:
    if not messages:
        return "<p>Ninguno.</p>"
    rows = []
    for message in messages:
        body = text_to_html_fragment(message.body[:1200])
        rows.append(
            "<tr>"
            f"<td>{text_to_html_fragment(message.channel)}</td>"
            f"<td>{text_to_html_fragment(message.recipient or '(sin destinatario)')}</td>"
            f"<td>{text_to_html_fragment(message.subject or message.source)}</td>"
            f"<td>{body}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>Canal</th><th>Destino</th><th>Asunto/fuente</th><th>Mensaje</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def _render_blocked(blocked: list[dict[str, str]]) -> str:
    if not blocked:
        return "<p>Ninguno.</p>"
    rows = [
        "<tr>"
        f"<td>{text_to_html_fragment(item.get('source', ''))}</td>"
        f"<td>{text_to_html_fragment(item.get('recipient', ''))}</td>"
        f"<td>{text_to_html_fragment(item.get('reason', ''))}</td>"
        "</tr>"
        for item in blocked
    ]
    return "<table><thead><tr><th>Fuente</th><th>Destino</th><th>Motivo</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a safe HTML preview for WhatsApp/email batch candidates.")
    parser.add_argument("paths", nargs="+", help="JSON, JSONL, log, or text artifacts to inspect.")
    parser.add_argument("--limit", type=int, default=DEFAULT_REAL_SEND_LIMIT, help="Maximum real sends allowed in one batch.")
    parser.add_argument("--html-out", required=True, help="Destination HTML preview file.")
    parser.add_argument("--json-out", default="", help="Optional JSON summary destination.")
    args = parser.parse_args(argv)

    result = build_preview(read_messages(args.paths), real_send_limit=args.limit)
    Path(args.html_out).write_text(render_preview_html(result), encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "html_out": args.html_out,
        "json_out": args.json_out,
        "deliverable": len(result.deliverable),
        "capped_deliverable": len(result.capped_deliverable),
        "internal_or_test": len(result.internal_or_test),
        "blocked": len(result.blocked),
        "over_limit": result.over_limit_count,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
