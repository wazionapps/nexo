#!/usr/bin/env python3
"""Guardian telemetry summary — Fase F F.3.

Prints a per-rule efficacy snapshot from ~/.nexo/logs/guardian-telemetry.ndjson.
Fase F.3 plan calls for a proper internal dashboard; this CLI is the
dev-machine spot-check until that dashboard ships.

Usage:
    python3 scripts/guardian_telemetry_summary.py
    python3 scripts/guardian_telemetry_summary.py --rule R16_declared_done
    python3 scripts/guardian_telemetry_summary.py --path /tmp/custom.ndjson --json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import guardian_telemetry as gt  # noqa: E402


def _collect_rule_ids(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        rid = str(entry.get("rule_id") or "").strip()
        if rid:
            seen.add(rid)
    return sorted(seen)


def _format_row(rule: str, counts: dict[str, int], eff: float | None) -> str:
    eff_str = "—" if eff is None else f"{eff * 100:5.1f}%"
    return (
        f"{rule:<40} trig={counts['trigger']:>4} "
        f"inj={counts['injection']:>4} comp={counts['compliance']:>4} "
        f"fp={counts['false_positive']:>4} eff={eff_str}"
    )


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Summarize Guardian telemetry")
    parser.add_argument("--rule", type=str, default=None, help="Focus on one rule_id")
    parser.add_argument("--path", type=str, default=None, help="Override telemetry NDJSON path")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path) if args.path else gt._telemetry_path()
    rules = [args.rule] if args.rule else _collect_rule_ids(path)

    rows: list[dict] = []
    for rule in rules:
        counts = gt.summarize_rule(rule, path=path)
        eff = gt.efficacy(rule, path=path)
        rows.append({
            "rule": rule,
            "counts": counts,
            "efficacy": eff,
        })

    if args.json:
        print(json.dumps({"source": str(path), "rules": rows}, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print(f"No telemetry entries found at {path}")
        return 0

    header = f"{'rule':<40} {'trigger':>6} {'inject':>6} {'comply':>6} {'fp':>6} {'efficacy':>10}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(_format_row(row["rule"], row["counts"], row["efficacy"]))
    print("-" * len(header))
    print(f"source: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
