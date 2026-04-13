#!/usr/bin/env python3
from __future__ import annotations

"""Rehydrate archived markdown learnings back into the NEXO learnings table.

The original Evolution #5 incident found an empty learnings table while the
historical archive still existed as markdown grouped by domain. This helper
parses the archive format used in those files:

- markdown tables with `Error | Solucion`
- dated sections with bullet/numbered operational learnings

Dry-run is the default. Pass `--apply` to insert missing learnings.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[1]
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from db import create_learning, get_db, init_db  # noqa: E402
from runtime_home import export_resolved_nexo_home  # noqa: E402

NEXO_HOME = export_resolved_nexo_home()
TABLE_HEADER_TITLES = {"error", "problema", "issue"}
DEFAULT_ARCHIVE_DIRS = (
    NEXO_HOME / "claude" / "operations" / "archive" / "learnings",
    Path.home() / "claude" / "operations" / "archive" / "learnings",
    Path.home() / ".claude" / "operations" / "archive" / "learnings",
)


@dataclass(frozen=True)
class LearningCandidate:
    category: str
    title: str
    content: str
    reasoning: str
    prevention: str
    status: str = "active"


def _strip_markdown(text: str) -> str:
    text = text.replace("**", "").replace("__", "")
    text = text.replace("~~", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" |")
    return text.strip()


def _derive_title(text: str) -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if not first_sentence:
        first_sentence = text.strip()
    return first_sentence[:180].rstrip(" .")


def _derive_prevention(text: str) -> str:
    match = re.search(r"(Regla:\s*.*|SIEMPRE\s+.*|NUNCA\s+.*)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text[:500].strip()


def _candidate_reasoning(path: Path, section: str) -> str:
    section_note = f" [{section}]" if section and section != path.stem else ""
    return f"Rehydrated from markdown archive {path.name}{section_note}"


def _parse_table_row(path: Path, section: str, line: str) -> LearningCandidate | None:
    parts = [_strip_markdown(cell) for cell in line.strip().strip("|").split("|")]
    if len(parts) < 2:
        return None
    title, prevention = parts[0], parts[1]
    if title.lower() in TABLE_HEADER_TITLES or set(title) <= {"-"}:
        return None
    if not title or not prevention:
        return None
    status = "superseded" if "obsoleto" in prevention.lower() or "obsoleto" in title.lower() else "active"
    content = f"{title}. {prevention}"
    return LearningCandidate(
        category=path.stem,
        title=title,
        content=content,
        reasoning=_candidate_reasoning(path, section),
        prevention=prevention,
        status=status,
    )


def _consume_bullet_block(lines: list[str], start: int) -> tuple[str, int]:
    pieces = [re.sub(r"^([-*]|\d+\.)\s+", "", lines[start].strip())]
    idx = start + 1
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            break
        if stripped.startswith("## ") or stripped.startswith("|"):
            break
        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            break
        pieces.append(stripped)
        idx += 1
    return _strip_markdown(" ".join(pieces)), idx


def _parse_bullet(path: Path, section: str, text: str) -> LearningCandidate | None:
    if len(text) < 12:
        return None
    title = _derive_title(text)
    prevention = _derive_prevention(text)
    status = "superseded" if "obsoleto" in text.lower() else "active"
    return LearningCandidate(
        category=path.stem,
        title=title,
        content=text,
        reasoning=_candidate_reasoning(path, section),
        prevention=prevention,
        status=status,
    )


def parse_archive_file(path: Path) -> list[LearningCandidate]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    section = path.stem
    results: list[LearningCandidate] = []
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped.startswith("## "):
            section = _strip_markdown(stripped[3:])
            idx += 1
            continue
        if stripped.startswith("|") and not re.match(r"^\|\s*-", stripped):
            row = _parse_table_row(path, section, stripped)
            if row is not None:
                results.append(row)
            idx += 1
            continue
        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            block, next_idx = _consume_bullet_block(lines, idx)
            row = _parse_bullet(path, section, block)
            if row is not None:
                results.append(row)
            idx = next_idx
            continue
        idx += 1
    return results


def parse_archive_dir(archive_dir: Path) -> list[LearningCandidate]:
    candidates: list[LearningCandidate] = []
    for path in sorted(archive_dir.glob("*.md")):
        candidates.extend(parse_archive_file(path))

    deduped: list[LearningCandidate] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item.category.lower(), item.title.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def resolve_archive_dir(explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(f"archive dir not found: {path}")
        return path
    for candidate in DEFAULT_ARCHIVE_DIRS:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "No learnings archive found. Tried: "
        + ", ".join(str(path) for path in DEFAULT_ARCHIVE_DIRS)
    )


def apply_candidates(candidates: list[LearningCandidate], *, apply: bool) -> dict:
    init_db()
    conn = get_db()
    existing = {
        (row[0].lower(), row[1].lower())
        for row in conn.execute("SELECT category, title FROM learnings").fetchall()
    }
    inserted = 0
    skipped = 0
    for item in candidates:
        key = (item.category.lower(), item.title.lower())
        if key in existing:
            skipped += 1
            continue
        if apply:
            create_learning(
                item.category,
                item.title,
                item.content,
                reasoning=item.reasoning,
                prevention=item.prevention,
                status=item.status,
            )
        existing.add(key)
        inserted += 1
    return {
        "parsed": len(candidates),
        "inserted": inserted,
        "skipped_existing": skipped,
        "mode": "apply" if apply else "dry-run",
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default="", help="Override archive directory")
    parser.add_argument("--apply", action="store_true", help="Insert parsed learnings into the DB")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        archive_dir = resolve_archive_dir(args.archive_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    candidates = parse_archive_dir(archive_dir)
    summary = apply_candidates(candidates, apply=args.apply)
    print(
        f"{summary['mode']}: archive={archive_dir} parsed={summary['parsed']} "
        f"inserted={summary['inserted']} skipped_existing={summary['skipped_existing']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
