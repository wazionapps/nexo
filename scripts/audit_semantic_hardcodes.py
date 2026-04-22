#!/usr/bin/env python3
"""Audit semantic hardcodes — list every Spanish/English keyword or regex
that currently drives a decision which would be better served by the
local zero-shot classifier (Block D.1 directive, Francisco 2026-04-22).

The goal is not to fix anything in this script; it is to produce a
prioritized worklist the operator (or a future automation) can walk
through a file at a time. Findings are grouped by file + category so
the output stays actionable even when the codebase grows.

Usage (from the repo root):

    python3 scripts/audit_semantic_hardcodes.py           # all categories
    python3 scripts/audit_semantic_hardcodes.py --json    # machine-readable
    python3 scripts/audit_semantic_hardcodes.py --category language-keywords

Exit codes:
    0 — audit completed (findings may still exist; the script is informational)
    2 — internal error (bad arguments, missing directories, etc.)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("src",)
DEFAULT_EXCLUDE_DIRS = (
    "__pycache__",
    ".git",
    "node_modules",
    "dist",
    "build",
    "venv",
    ".venv",
    "tests",  # test fixtures legitimately contain keywords
)


@dataclass(frozen=True)
class Pattern:
    """A pattern the audit flags. ``hint`` tells the reader *why* this
    pattern is a candidate for classifier replacement; ``suggestion``
    suggests the refactor path."""

    name: str
    category: str
    regex: re.Pattern
    hint: str
    suggestion: str


@dataclass
class Finding:
    path: str
    line: int
    pattern: Pattern
    excerpt: str


def _build_patterns() -> list[Pattern]:
    """Curated list of keyword/regex patterns that are semantic decisions
    in disguise. Extend carefully — each entry must have a concrete
    refactor path (LLM classifier, embeddings, structural signal)."""

    raw: list[dict] = [
        {
            "name": "session-end-phrase-list",
            "category": "language-keywords",
            "regex": r"\b(?:gracias|adios|adi[oó]s|bye|thanks)\b.*\b(?:close|end|fin|cierro)\b",
            "hint": "Keyword list for implicit session-end detection.",
            "suggestion": "Use session_end_intent classifier (templates/core-prompts/session-end-intent-question.md).",
        },
        {
            "name": "user-decision-verb-list",
            "category": "language-keywords",
            "regex": r"['\"](revisa|revisar|aprueba|aprobar|decide|decidir|confirma|confirmar|approve|review|confirm|validate)['\"]",
            "hint": "Hardcoded user-action verbs (ES/EN).",
            "suggestion": "Route through local zero-shot classifier (see backfill_task_owner classifier hook).",
        },
        {
            "name": "waiting-phrase-list",
            "category": "language-keywords",
            "regex": r"esperando\s+(?:respuesta|a\s+\w+|confirmaci[oó]n)|waiting\s+(?:for|on)\s+\w+",
            "hint": "Fixed waiting/blocked phrase regex in the source.",
            "suggestion": "Prefer classifier over regex for intent; regex only as fallback.",
        },
        {
            "name": "correction-phrase-list",
            "category": "language-keywords",
            "regex": r"['\"](?:est[áa]s?\s+mal|te\s+equivocaste|no\s+es\s+correcto|that'?s?\s+wrong|incorrect)['\"]",
            "hint": "Hardcoded correction-detection phrases.",
            "suggestion": "cognitive_sentiment classifier already knows corrections; wire it in and drop the regex.",
        },
        {
            "name": "explicit-decision-phrase-list",
            "category": "language-keywords",
            "regex": r"['\"](?:decidido|hag[aá]moslo|lo\s+hago|let'?s\s+do\s+it|go\s+ahead|proceder?|s[ií]\s+adelante)['\"]",
            "hint": "Hardcoded explicit-decision phrases.",
            "suggestion": "autonomy_mandate classifier + cognitive_sentiment intent field.",
        },
        {
            "name": "agent-verb-list",
            "category": "language-keywords",
            "regex": r"['\"](?:cron|auto\s?ejecut|check\s+every|monitoriz[ao]|verifica\s+cada|run\s+every)['\"]",
            "hint": "Hardcoded agent/cron verb list.",
            "suggestion": "Classifier label agent_automation_cron (see backfill_task_owner).",
        },
        {
            "name": "priority-word-list",
            "category": "language-keywords",
            "regex": r"['\"](?:urgente|urgent|inmediato|immediately|importante|important)['\"]",
            "hint": "Priority detection via keyword matching.",
            "suggestion": "cognitive_sentiment intent label or explicit priority field on the row.",
        },
        {
            "name": "language-literal-es",
            "category": "locale",
            "regex": r"language=['\"]es['\"]",
            "hint": "Hardcoded language=es locale; should read operator calibration.",
            "suggestion": "Read user.language from calibration.json (user_context.get_context()).",
        },
        {
            "name": "spanish-ui-string",
            "category": "locale",
            "regex": r"['\"](Esperando|Provincia|Municipio|Para ti|Seguimiento|Ver detalle|Aceptar|Cancelar)['\"]",
            "hint": "Hardcoded Spanish UI string; should route through i18n.",
            "suggestion": "Add to renderer/i18n/{en,es,...}.json + t('key') lookup.",
        },
        {
            "name": "destructive-bash-keyword",
            "category": "destructive-commands",
            "regex": r"\b(rm\s+-rf|push\s+--force|drop\s+table|truncate)\b",
            "hint": "Destructive bash/SQL keyword — candidate for pre_tool_guard bloqueo (Block K G3).",
            "suggestion": "Enforce via PreToolUse + nexo_cortex_decide gate.",
        },
    ]

    return [
        Pattern(
            name=item["name"],
            category=item["category"],
            regex=re.compile(item["regex"]),
            hint=item["hint"],
            suggestion=item["suggestion"],
        )
        for item in raw
    ]


def _iter_source_files(roots: Iterable[Path], exclude_dirs: set[str]) -> Iterable[Path]:
    extensions = {".py", ".js", ".ts", ".sh", ".md"}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            if path.suffix not in extensions:
                continue
            if any(part in exclude_dirs for part in path.parts):
                continue
            yield path


def _scan(path: Path, patterns: list[Pattern]) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            if pattern.regex.search(line):
                excerpt = line.strip()
                if len(excerpt) > 160:
                    excerpt = excerpt[:157] + "..."
                findings.append(
                    Finding(
                        path=str(path.relative_to(REPO_ROOT)),
                        line=lineno,
                        pattern=pattern,
                        excerpt=excerpt,
                    )
                )
    return findings


def _format_text(findings: list[Finding]) -> str:
    if not findings:
        return "[audit_semantic_hardcodes] clean — no semantic hardcodes detected.\n"
    out: list[str] = []
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.path, []).append(f)

    summary: dict[str, int] = {}
    for f in findings:
        summary[f.pattern.category] = summary.get(f.pattern.category, 0) + 1

    out.append("[audit_semantic_hardcodes] findings by category:")
    for cat, n in sorted(summary.items(), key=lambda x: -x[1]):
        out.append(f"  {cat}: {n}")
    out.append("")

    for file_path in sorted(by_file):
        out.append(file_path)
        for f in by_file[file_path]:
            out.append(f"  L{f.line} [{f.pattern.name}] {f.excerpt}")
            out.append(f"    -> {f.pattern.suggestion}")
        out.append("")
    return "\n".join(out)


def _format_json(findings: list[Finding]) -> str:
    payload = [
        {
            "path": f.path,
            "line": f.line,
            "pattern": f.pattern.name,
            "category": f.pattern.category,
            "hint": f.pattern.hint,
            "suggestion": f.pattern.suggestion,
            "excerpt": f.excerpt,
        }
        for f in findings
    ]
    return json.dumps({"findings": payload, "total": len(findings)}, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=None,
        help=f"root directory to scan (relative to repo). Repeatable. Default: {DEFAULT_ROOTS}.",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="limit to patterns in a given category (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON on stdout instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    patterns = _build_patterns()
    if args.category:
        requested = set(args.category)
        patterns = [p for p in patterns if p.category in requested]
        if not patterns:
            print(
                f"[audit_semantic_hardcodes] no patterns match category filter: {sorted(requested)}",
                file=sys.stderr,
            )
            return 2

    root_strs = args.root or list(DEFAULT_ROOTS)
    roots = [REPO_ROOT / r for r in root_strs]
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)

    findings: list[Finding] = []
    for path in _iter_source_files(roots, exclude_dirs):
        findings.extend(_scan(path, patterns))

    if args.json:
        print(_format_json(findings))
    else:
        print(_format_text(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
