"""Shared email presentation helpers for operator-facing automations.

Agents may produce HTML, but SMTP, artifacts, and Desktop must only consume
normalized/sanitized output from this module.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3",
    "hr", "i", "li", "ol", "p", "pre", "span", "strong", "table", "tbody",
    "td", "th", "thead", "tr", "u", "ul",
}
VOID_TAGS = {"br", "hr"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
SAFE_URL_SCHEMES = {"http", "https", "mailto"}


@dataclass(frozen=True)
class EmailPresentation:
    subject: str
    body_text: str
    body_html: str
    input_format: str

    def to_dict(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "input_format": self.input_format,
        }


class _SafeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"script", "style", "iframe", "object", "embed", "svg", "math"}:
            self._skip_depth += 1
            return
        if self._skip_depth or clean_tag not in ALLOWED_TAGS:
            return
        attr_bits: list[str] = []
        allowed = ALLOWED_ATTRS.get(clean_tag, set())
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").lower().strip()
            if not name or name.startswith("on") or name not in allowed:
                continue
            value = str(raw_value or "").strip()
            if name == "href" and not _safe_href(value):
                continue
            if name in {"colspan", "rowspan"}:
                value = str(max(1, min(12, _safe_int(value, 1))))
            attr_bits.append(f'{name}="{html.escape(value, quote=True)}"')
        suffix = (" " + " ".join(attr_bits)) if attr_bits else ""
        self.parts.append(f"<{clean_tag}{suffix}>")

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"script", "style", "iframe", "object", "embed", "svg", "math"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or clean_tag not in ALLOWED_TAGS or clean_tag in VOID_TAGS:
            return
        self.parts.append(f"</{clean_tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&#{name};")


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _safe_href(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme.lower() not in SAFE_URL_SCHEMES:
        return False
    if not parsed.scheme and value.strip().lower().startswith("javascript:"):
        return False
    return True


def sanitize_html_fragment(raw_html: str) -> str:
    parser = _SafeHtmlParser()
    try:
        parser.feed(str(raw_html or ""))
        parser.close()
    except Exception:
        return ""
    cleaned = "".join(parser.parts)
    cleaned = re.sub(r"\s+javascript\s*:", "", cleaned, flags=re.I)
    return cleaned.strip()


def text_to_html_fragment(text: str) -> str:
    paragraphs = re.split(r"\n{2,}", str(text or "").strip())
    rendered: list[str] = []
    for paragraph in paragraphs:
        clean = html.escape(paragraph.strip(), quote=False)
        if not clean:
            continue
        rendered.append(f"<p>{clean.replace(chr(10), '<br>')}</p>")
    return "".join(rendered) or "<p></p>"


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style|iframe|object|embed|svg|math).*?</\1>", " ", str(raw_html or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h1|h2|h3|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def compose_html_document(fragment: str) -> str:
    safe_fragment = sanitize_html_fragment(fragment)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
        'font-size:14px;color:#222;line-height:1.6;">'
        f"{safe_fragment}</body></html>"
    )


def signature_from_config(config: dict | None, *, fallback: str = "") -> str:
    metadata = (config or {}).get("metadata")
    if not isinstance(metadata, dict):
        account = (config or {}).get("agent_account")
        if isinstance(account, dict):
            metadata = account.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    signature = str(metadata.get("signature") or "").strip()
    return signature or str(fallback or "").strip()


def append_signature_text(body_text: str, signature: str) -> str:
    clean_body = str(body_text or "").strip()
    clean_signature = str(signature or "").strip()
    if not clean_signature:
        return clean_body
    if clean_signature in clean_body[-500:]:
        return clean_body
    return f"{clean_body}\n\n-- \n{clean_signature}".strip()


def append_signature_html(fragment: str, signature: str) -> str:
    clean_signature = str(signature or "").strip()
    if not clean_signature:
        return fragment
    safe_signature = text_to_html_fragment(clean_signature)
    return (
        f"{fragment}"
        '<hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">'
        f'<div style="color:#666;font-size:12px;">{safe_signature}</div>'
    )


def build_email_presentation(
    *,
    subject: str,
    body_text: str = "",
    body_html: str = "",
    signature: str = "",
    include_signature: bool = False,
) -> EmailPresentation:
    clean_subject = " ".join(str(subject or "").split()).strip()
    raw_text = str(body_text or "").strip()
    raw_html = str(body_html or "").strip()
    input_format = "html" if raw_html else "text"
    text = raw_text or html_to_text(raw_html)
    html_fragment = sanitize_html_fragment(raw_html) if raw_html else text_to_html_fragment(text)
    if include_signature:
        text = append_signature_text(text, signature)
        html_fragment = append_signature_html(html_fragment, signature)
    return EmailPresentation(
        subject=clean_subject,
        body_text=text,
        body_html=compose_html_document(html_fragment),
        input_format=input_format,
    )


def normalize_agent_email_payload(payload: dict[str, Any], *, signature: str = "") -> EmailPresentation:
    subject = str(payload.get("subject") or "").strip()
    body_text = str(payload.get("body_text") or payload.get("body") or "").strip()
    body_html = str(payload.get("body_html") or "").strip()
    presentation = build_email_presentation(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        signature=signature,
        include_signature=bool(signature),
    )
    if not presentation.subject or not presentation.body_text:
        raise RuntimeError("Email payload is missing subject/body_text.")
    return presentation


__all__ = [
    "EmailPresentation",
    "append_signature_html",
    "append_signature_text",
    "build_email_presentation",
    "compose_html_document",
    "html_to_text",
    "normalize_agent_email_payload",
    "sanitize_html_fragment",
    "signature_from_config",
    "text_to_html_fragment",
]
