from __future__ import annotations

"""Daily cost/secret sweep queue builder."""

import glob
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    from evidence_ledger import _SECRET_PATTERNS
except Exception:  # pragma: no cover
    _SECRET_PATTERNS = (
        re.compile(r"(token\s*[=:]\s*)[^\s'\"]{8,}", re.I),
        re.compile(r"(password\s*[=:]\s*)[^\s'\"]{6,}", re.I),
        re.compile(r"(api[_-]?key\s*[=:]\s*)[^\s'\"]{8,}", re.I),
    )


_CATEGORY_RULES: tuple[tuple[str, re.Pattern[str], int, int, str], ...] = (
    ("billing_exhausted", re.compile(r"\b(billing|saldo|quota|cuota).{0,40}\b(agot|exhaust|insufficient|sin saldo)", re.I), 3, 1, "revisar billing y recarga"),
    ("pasted_key", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{30,})\b"), 2, 3, "rotar clave pegada y mover a gestor"),
    ("bridge_token", re.compile(r"\b(bridge|mcp|internal).{0,30}\b(token|api[_-]?key)\b", re.I), 2, 2, "rotar token puente y revisar scope"),
    ("env_secret", re.compile(r"\b(env|\.env|log|stdout|trace).{0,30}\b(token|password|secret|api[_-]?key)\b", re.I), 2, 3, "mover secreto fuera de env/logs expuestos"),
    ("rotation_pending", re.compile(r"(?=.*\b(rotar|rotation|comprometid|expuest|leak|public)\b)(?=.*\b(token|clave|secret|password|credencial|credential|api[_-]?key)\b)", re.I | re.S), 2, 2, "cerrar rotacion con evidencia"),
)


@dataclass(frozen=True)
class SweepItem:
    category: str
    source: str
    summary: str
    economic_impact: int
    exposure: int
    priority: int
    recommendation: str


def _first_matching_line(pattern: re.Pattern[str], text: str) -> str | None:
    """Return the first individual line that matches the pattern, else None.

    Matching per-line (instead of across the whole 20k blob with ``re.S``)
    stops the multi-token lookahead categories from conflating trigger words
    that live on unrelated lines far apart in a large log file. That blob-wide
    conflation was the source of the ``rotation_pending`` false positives
    (e.g. a HN headline with "public" on one line and "Token" on another).
    """

    for line in (text.splitlines() or [text]):
        if pattern.search(line):
            return line
    return None


def build_sweep_queue(records: Iterable[dict]) -> list[SweepItem]:
    """Return one prioritized queue sorted by economic impact x exposure."""

    items: list[SweepItem] = []
    for record in records:
        text = str(record.get("text") or record.get("summary") or "")
        source = str(record.get("source") or "unknown")
        for category, pattern, economic, exposure, recommendation in _CATEGORY_RULES:
            matched_line = _first_matching_line(pattern, text)
            if matched_line is None:
                continue
            item = SweepItem(
                category=category,
                source=source,
                summary=redact_secrets(matched_line)[:500],
                economic_impact=int(record.get("economic_impact") or economic),
                exposure=int(record.get("exposure") or exposure),
                priority=int(record.get("economic_impact") or economic) * int(record.get("exposure") or exposure),
                recommendation=recommendation,
            )
            items.append(item)
            break
    return sorted(items, key=lambda item: (-item.priority, item.category, item.source))


def collect_text_sources(paths: Iterable[str]) -> list[dict]:
    records: list[dict] = []
    for pattern in paths:
        for filename in glob.glob(pattern):
            path = Path(filename)
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            records.append({"source": str(path), "text": text[:20000]})
    return records


def run_sweep(*, records: Iterable[dict] = (), paths: Iterable[str] = ()) -> dict:
    all_records = list(records)
    all_records.extend(collect_text_sources(paths))
    queue = build_sweep_queue(all_records)
    return {
        "generated_at": int(time.time()),
        "count": len(queue),
        "queue": [asdict(item) for item in queue],
    }


def default_paths(nexo_home: str | None = None) -> list[str]:
    home = Path(nexo_home or os.environ.get("NEXO_HOME") or "~/.nexo").expanduser()
    return [
        str(home / "runtime" / "logs" / "*.log"),
        str(home / "runtime" / "operations" / "*.jsonl"),
        str(home / ".env"),
    ]


def append_jsonl_report(report: dict, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")


def redact_secrets(text: str) -> str:
    redacted = str(text or "")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_redact_match, redacted)
    return redacted


def _redact_match(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"
