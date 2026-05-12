from __future__ import annotations

import csv
import html
import json
import re
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from xml.etree import ElementTree

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


def _extract_csv(path: Path) -> str:
    text = _read_text(path)
    rows = []
    for idx, row in enumerate(csv.reader(text.splitlines())):
        if idx >= 200:
            break
        rows.append(" | ".join(row[:20]))
    return "\n".join(rows)[:MAX_CHARS]


def _extract_eml(path: Path) -> tuple[str, dict]:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes()[:MAX_TEXT_BYTES])
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
    text = html.unescape(text or "")
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
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix == ".docx":
        text = _extract_docx(path)
    elif suffix == ".pptx":
        text = _extract_pptx(path)
    elif suffix == ".xlsx":
        text = _extract_xlsx(path)
    else:
        text = ""
    return clean_text(text), metadata


def summarize(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:3])[:1200]


def entities(text: str) -> list[str]:
    found = set()
    for match in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.-]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.-]{2,}){0,3}", text):
        value = match.group(0).strip()
        if len(value) <= 80:
            found.add(value)
    for match in re.finditer(r"[\w.-]+@[\w.-]+\.[A-Za-z]{2,}", text):
        found.add(match.group(0))
    return sorted(found)[:50]


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
