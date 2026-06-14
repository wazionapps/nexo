from __future__ import annotations

import csv
import html
import json
import re
import sqlite3
import unicodedata
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from xml.etree import ElementTree

from .privacy import is_local_email_db

MAX_TEXT_BYTES = 512 * 1024
MAX_CHARS = 120_000

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".php",
    ".sql",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".css",
}

SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-~+/]{12,}\b", re.I),
    re.compile(r"\bsk-(?:[a-z]+-)?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bpk-(?:[a-z]+-)?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr|github_pat|glpat|xoxb|xoxp|shpat)_[A-Za-z0-9_]{16,}\b", re.I),
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", re.I),
    re.compile(r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASS)\s*[:=]\s*)['\"]?[A-Za-z0-9._/+=\-]{12,}", re.I),
    re.compile(r"\b(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9._/+=\-]{12,}", re.I),
    re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]", re.I),
)


def contains_secret(text: str) -> bool:
    if not text:
        return False
    sample = text[:MAX_CHARS]
    return any(pattern.search(sample) for pattern in SECRET_PATTERNS)


def _read_text(path: Path) -> str:
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding, errors="replace")[:MAX_CHARS]
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")[:MAX_CHARS]


def _read_text_if_safe(path: Path) -> str:
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    if not data or b"\x00" in data[:8192]:
        return ""
    text = _read_text(path)
    if not text:
        return ""
    printable = sum(1 for char in text[:4096] if char.isprintable() or char.isspace())
    sample_len = max(1, min(len(text), 4096))
    if printable / sample_len < 0.85:
        return ""
    return text


def _extract_csv(path: Path) -> str:
    text = _read_text(path)
    rows = []
    for idx, row in enumerate(csv.reader(text.splitlines())):
        if idx >= 200:
            break
        rows.append(" | ".join(row[:20]))
    return "\n".join(rows)[:MAX_CHARS]


def _extract_email_bytes(data: bytes) -> tuple[str, dict]:
    msg = BytesParser(policy=policy.default).parsebytes(data[:MAX_TEXT_BYTES])
    meta = {
        "subject": str(msg.get("subject") or ""),
        "from": str(msg.get("from") or ""),
        "to": str(msg.get("to") or ""),
        "date": str(msg.get("date") or ""),
    }
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body:
        text = body.get_content()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
    return "\n".join([meta["subject"], meta["from"], meta["to"], text])[:MAX_CHARS], meta


def _extract_eml(path: Path) -> tuple[str, dict]:
    return _extract_email_bytes(path.read_bytes()[:MAX_TEXT_BYTES])


def _extract_emlx(path: Path) -> tuple[str, dict]:
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    first_line, separator, rest = data.partition(b"\n")
    if separator and first_line.strip().isdigit():
        declared = int(first_line.strip() or b"0")
        payload = rest[:declared] if declared > 0 else rest
    else:
        payload = data
    if b"\n<?xml" in payload:
        payload = payload.split(b"\n<?xml", 1)[0]
    text, meta = _extract_email_bytes(payload)
    meta["apple_mail_message"] = True
    return text, meta


def _printable_binary_text(path: Path) -> str:
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    decoded = data.decode("utf-16", errors="ignore") if b"\x00" in data[:2000] else data.decode("latin-1", errors="ignore")
    pieces = re.findall(r"[\wÀ-ÿ@./:=+\- ,;()\\[\\]{}]{4,}", decoded)
    return "\n".join(piece.strip() for piece in pieces if piece.strip())[:MAX_CHARS]


def _extract_msg(path: Path) -> tuple[str, dict]:
    try:
        import extract_msg  # type: ignore
        message = extract_msg.Message(str(path))
        meta = {
            "subject": str(getattr(message, "subject", "") or ""),
            "from": str(getattr(message, "sender", "") or ""),
            "to": str(getattr(message, "to", "") or ""),
            "date": str(getattr(message, "date", "") or ""),
            "extractor": "msg",
        }
        body = str(getattr(message, "body", "") or "")
        close = getattr(message, "close", None)
        if callable(close):
            close()
        return "\n".join([meta["subject"], meta["from"], meta["to"], body])[:MAX_CHARS], meta
    except Exception:
        return _printable_binary_text(path), {"extractor": "msg_fallback"}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _select_existing_columns(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[str]:
    found = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return [column for column in columns if column in found]


def _extract_nexo_email_db(path: Path) -> tuple[str, dict]:
    if not is_local_email_db(str(path)):
        return "", {"extractor": "sqlite_blocked"}
    uri = f"file:{path}?mode=ro"
    parts: list[str] = []
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1)
    except Exception:
        return "", {"extractor": "nexo_email_db", "state": "locked_or_unavailable"}
    try:
        tables = _table_names(conn)
        if "emails" in tables:
            cols = _select_existing_columns(
                conn,
                "emails",
                ["from_addr", "from_name", "subject", "received_at", "status", "body", "response"],
            )
            if not cols:
                return "", {"extractor": "nexo_email_db", "tables": sorted(tables)}
            order = "received_at" if "received_at" in cols else "rowid"
            for row in conn.execute(f"SELECT {', '.join(cols)} FROM emails ORDER BY {order} DESC LIMIT 1000").fetchall():
                parts.append(" | ".join(str(value or "")[:4000] for value in row))
        if "sent_email_events" in tables:
            cols = _select_existing_columns(
                conn,
                "sent_email_events",
                ["sender", "to_addrs", "cc_addrs", "subject", "sent_at", "status", "body_text"],
            )
            if cols:
                order = "sent_at" if "sent_at" in cols else "rowid"
                for row in conn.execute(f"SELECT {', '.join(cols)} FROM sent_email_events ORDER BY {order} DESC LIMIT 1000").fetchall():
                    parts.append(" | ".join(str(value or "")[:4000] for value in row))
    finally:
        conn.close()
    return "\n".join(parts)[:MAX_CHARS], {"extractor": "nexo_email_db", "tables": sorted(tables) if "tables" in locals() else []}


def _zip_xml_text(path: Path, members: list[str]) -> str:
    pieces: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in members:
            if name.endswith("/"):
                continue
            try:
                raw = zf.read(name)
            except Exception:
                continue
            try:
                root = ElementTree.fromstring(raw)
            except Exception:
                continue
            for node in root.iter():
                if node.text and node.text.strip():
                    pieces.append(node.text.strip())
            if sum(len(p) for p in pieces) > MAX_CHARS:
                break
    return "\n".join(pieces)[:MAX_CHARS]


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        members = [name for name in zf.namelist() if name.startswith("word/") and name.endswith(".xml")]
    return _zip_xml_text(path, members)


def _extract_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        members = [name for name in zf.namelist() if name.startswith("ppt/slides/") and name.endswith(".xml")]
    return _zip_xml_text(path, members)


def _extract_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except Exception:
        return ""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets[:5]:
        parts.append(f"# {sheet.title}")
        for ridx, row in enumerate(sheet.iter_rows(values_only=True)):
            if ridx >= 200:
                break
            values = [str(value) for value in row[:20] if value is not None]
            if values:
                parts.append(" | ".join(values))
    return "\n".join(parts)[:MAX_CHARS]


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages[:50]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)[:MAX_CHARS]


def clean_text(text: str) -> str:
    text = text or ""
    # Drop the CONTENT of style/script/head blocks (not just their tags) BEFORE
    # stripping tags, or CSS/JS boilerplate survives as text and poisons chunks,
    # embeddings, NER and facts (e.g. 'mso-table-lspace', 'font-family').
    text = re.sub(r"(?is)<(style|script|head)\b[^>]*>.*?</\1>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS]


def extract_text(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    metadata: dict = {"extractor": suffix.lstrip(".") or "plain"}
    if suffix in TEXT_SUFFIXES:
        text = _read_text(path)
    elif suffix in {".csv", ".tsv"}:
        text = _extract_csv(path)
    elif suffix == ".eml":
        text, metadata = _extract_eml(path)
        metadata["extractor"] = "eml"
    elif suffix == ".emlx":
        text, metadata = _extract_emlx(path)
        metadata["extractor"] = "emlx"
    elif suffix == ".msg":
        text, metadata = _extract_msg(path)
        metadata["extractor"] = metadata.get("extractor") or "msg"
    elif suffix == ".db" and is_local_email_db(str(path)):
        text, metadata = _extract_nexo_email_db(path)
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    elif suffix == ".pptx":
        text = _extract_pptx(path)
    elif suffix == ".xlsx":
        text = _extract_xlsx(path)
    else:
        text = _read_text_if_safe(path)
        if text:
            metadata["extractor"] = "generic_text"
    if contains_secret(text):
        metadata["content_secret_detected"] = True
    return clean_text(text), metadata


def summarize(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:3])[:1200]


def entities(text: str) -> list[str]:
    return [item["name"] for item in entity_mentions(text)[:50]]


def _ascii_fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )


def normalize_entity_alias(value: str) -> str:
    folded = _ascii_fold(value).lower()
    folded = re.sub(r"[^\w@.+-]+", " ", folded, flags=re.UNICODE)
    folded = re.sub(r"\s+", " ", folded).strip(" .-_")
    return folded


def canonical_entity_key(value: str) -> str:
    normalized = normalize_entity_alias(value)
    if not normalized:
        return ""
    if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", normalized):
        return f"email:{normalized}"
    words = [part for part in normalized.split() if part]
    if len(words) >= 2:
        return f"name:{words[-1]}:{words[0][:1]}"
    return f"alias:{normalized}"


def entity_mentions(text: str) -> list[dict]:
    found: dict[str, dict] = {}
    if not text:
        return []
    for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):
        value = match.group(0).strip(".,;:()[]{}<>")
        canonical = canonical_entity_key(value)
        if canonical:
            found[canonical] = {
                "name": value,
                "alias": value,
                "canonical_key": canonical,
                "entity_type": "email",
                "confidence": 0.92,
                "evidence": value[:240],
            }
    name_pattern = re.compile(
        r"\b[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.-]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.-]{2,}){0,3}"
    )
    for match in name_pattern.finditer(text):
        value = match.group(0).strip(".,;:()[]{}<>")
        if len(value) > 80:
            continue
        canonical = canonical_entity_key(value)
        if not canonical:
            continue
        existing = found.get(canonical)
        item = {
            "name": value,
            "alias": value,
            "canonical_key": canonical,
            "entity_type": "entity",
            "confidence": 0.68 if " " in value else 0.55,
            "evidence": value[:240],
        }
        if not existing or len(value) > len(str(existing.get("name") or "")):
            found[canonical] = item
    return sorted(found.values(), key=lambda item: (-float(item.get("confidence") or 0), str(item.get("name") or "").lower()))[:80]


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(text):
            break
        start += max(1, chunk_size - overlap)
    return chunks[:80]
