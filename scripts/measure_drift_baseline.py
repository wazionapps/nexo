#!/usr/bin/env python3
"""measure_drift_baseline — Fase 2 spec item 0.15.

Reads the last 90 session diaries from ``~/.nexo/brain/session_archive/``
(fallback to ``~/.nexo/brain/diaries/``), counts occurrences of known drift
patterns per rule, and writes an aggregated JSON report to
``~/.nexo/reports/drift-baseline-<YYYY-MM-DD>.json``.

The baseline is a prerequisite for Fase F ("reducción >50% por regla en 30
días"): without a pre-Guardian baseline per rule, the "reduction" KPI has
nothing to compare against. It must be run ONCE before landing Fase F
aggregates so the later delta has meaning.

Invariants:
  - Pure reader. Never writes inside the diary tree.
  - Respects ``NEXO_HOME`` when set (tests override via tmp_path).
  - If a diary file is unreadable, skip it and count it under
    ``unreadable_count``; never crash the whole run.
  - Output path is created with ``mkdir -p`` on ``~/.nexo/reports/``.
  - Fail-closed: if no diaries are found AT ALL, return non-zero exit so
    the caller knows the baseline is unusable (Rule #249).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


# Rule-id → regex patterns. Derived from learning catalog + Plan Consolidado
# R13-R33 spec. Patterns are intentionally broad (diary text is free-form);
# they intentionally over-match to surface the drift count, not to enforce.
DRIFT_PATTERNS: dict[str, list[re.Pattern]] = {
    "R13_pre_edit_guard": [
        re.compile(r"editar sin guard", re.I),
        re.compile(r"sin guard_check", re.I),
        re.compile(r"edit without guard", re.I),
    ],
    "R14_correction_learning": [
        re.compile(r"no capturé\w* learning", re.I),
        re.compile(r"learning_add.*(olvid|skip)", re.I),
        re.compile(r"correction.*(skip|no save)", re.I),
    ],
    "R16_declared_done": [
        re.compile(r"dije (listo|hecho|done|fixed) sin verificar", re.I),
        re.compile(r"declared.*done.*without", re.I),
        re.compile(r"marked.*done.*(no evidence|sin evidencia)", re.I),
    ],
    "R17_promise_debt": [
        re.compile(r"promet(í|ed) .*(sin ejecutar|without exec)", re.I),
        re.compile(r"mañana.*(lo hago|hago)", re.I),
    ],
    "R19_project_grep": [
        re.compile(r"edit(é|ed) sin grep", re.I),
        re.compile(r"no hice grep antes", re.I),
    ],
    "R20_constant_change": [
        re.compile(r"cambi(é|ed) constante sin grep", re.I),
    ],
    "R25_nora_maria_read_only": [
        re.compile(r"(Nora|Mar[ií]a).*(escrib|write)", re.I),
        re.compile(r"tocaste? (Nora|Mar[ií]a)", re.I),
    ],
    "R26_jargon_filter": [
        re.compile(r"demasiad\w+ jerga", re.I),
        re.compile(r"en cristiano", re.I),
        re.compile(r"no entiend(o|es).*(técnic|jerga)", re.I),
    ],
    "R27_short_answers": [
        re.compile(r"resumen demasiado largo", re.I),
        re.compile(r"ve al grano", re.I),
    ],
    "R30_pre_done_evidence": [
        re.compile(r"sin evidencia", re.I),
        re.compile(r"no verified", re.I),
    ],
    "R31_never_assume": [
        re.compile(r"no asum(es|as)", re.I),
        re.compile(r"asumiste", re.I),
    ],
}


def _nexo_home() -> Path:
    env = os.environ.get("NEXO_HOME")
    if env:
        return Path(env)
    return Path.home() / ".nexo"


def _find_diary_files(home: Path, max_files: int = 90) -> list[Path]:
    """Return up to `max_files` most-recent diary files.

    Looks in two candidate roots to tolerate the pre/post-F0 layout:
      - ``<home>/brain/session_archive/``
      - ``<home>/brain/diaries/``
    Older files ranked by mtime, newest first.
    """
    candidates: list[Path] = []
    for subdir in ("brain/session_archive", "brain/diaries"):
        d = home / subdir
        if d.is_dir():
            candidates.extend(d.rglob("*.md"))
            candidates.extend(d.rglob("*.json"))
    if not candidates:
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return candidates[:max_files]


def _iter_text(files: Iterable[Path]) -> Iterable[tuple[Path, str]]:
    for path in files:
        try:
            yield path, path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue


def _count_patterns(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for rule_id, patterns in DRIFT_PATTERNS.items():
        total = 0
        for p in patterns:
            total += len(p.findall(text))
        if total:
            out[rule_id] = total
    return out


def measure(*, home: Path | None = None, today: str | None = None) -> dict:
    home = home if home is not None else _nexo_home()
    diaries = _find_diary_files(home)
    rule_counts: dict[str, int] = {k: 0 for k in DRIFT_PATTERNS}
    per_file: list[dict] = []
    unreadable = 0

    for path, text in _iter_text(diaries):
        counts = _count_patterns(text)
        if not counts:
            continue
        per_file.append({"path": str(path), "counts": counts})
        for rule_id, n in counts.items():
            rule_counts[rule_id] = rule_counts.get(rule_id, 0) + n

    # Count unreadable = candidates - successfully iterated
    _iterated = {Path(e["path"]) for e in per_file}
    for path in diaries:
        try:
            path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable += 1

    return {
        "generated_at": (today or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        "nexo_home": str(home),
        "diaries_scanned": len(diaries),
        "diaries_with_hits": len(per_file),
        "unreadable_count": unreadable,
        "rule_counts": rule_counts,
        "per_file_sample": per_file[:20],  # cap so the JSON does not balloon
    }


def write_report(result: dict, *, home: Path | None = None) -> Path:
    home = home if home is not None else _nexo_home()
    reports_dir = home / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = reports_dir / f"drift-baseline-{date}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    result = measure()
    if result["diaries_scanned"] == 0:
        sys.stderr.write(
            "measure_drift_baseline: no diaries found; baseline unusable.\n"
            f"Expected under {result['nexo_home']}/brain/session_archive or diaries.\n"
        )
        return 2
    path = write_report(result)
    sys.stdout.write(
        f"measure_drift_baseline: scanned {result['diaries_scanned']} diaries, "
        f"hits in {result['diaries_with_hits']}, wrote {path}\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
