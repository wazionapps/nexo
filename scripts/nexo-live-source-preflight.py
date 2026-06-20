#!/usr/bin/env python3
"""Validate the live-source matrix before firm diagnosis or deploy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "config" / "live-source-matrix.json"
REQUIRED_FIELDS = ("repo", "branch", "server", "cloud_project", "live_table", "time_window")


def load_matrix(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"matrix file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"matrix file is not valid JSON: {exc}") from exc
    domains = data.get("domains")
    if not isinstance(domains, dict):
        raise SystemExit("matrix must contain a 'domains' object")
    return domains


def validate_entry(domain: str, entry: dict, expected: dict[str, str]) -> list[str]:
    failures: list[str] = []
    for field in REQUIRED_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(f"{domain}: missing required field '{field}'")
    for field, expected_value in expected.items():
        if expected_value and str(entry.get(field, "")).strip() != expected_value:
            failures.append(
                f"{domain}: {field} mismatch; matrix={entry.get(field)!r}, expected={expected_value!r}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True, help="Domain key, e.g. wazion or vicshop.systeam.es")
    parser.add_argument("--matrix-file", default=str(DEFAULT_MATRIX))
    parser.add_argument("--repo", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--server", default="")
    parser.add_argument("--cloud-project", default="", dest="cloud_project")
    parser.add_argument("--table", default="", dest="live_table")
    parser.add_argument("--window", default="", dest="time_window")
    parser.add_argument("--print", action="store_true", dest="print_entry")
    args = parser.parse_args(argv)

    domains = load_matrix(Path(args.matrix_file).expanduser())
    entry = domains.get(args.domain)
    if not isinstance(entry, dict):
        print(f"FAIL: domain '{args.domain}' is not present in live-source matrix", file=sys.stderr)
        return 2

    expected = {
        "repo": args.repo,
        "branch": args.branch,
        "server": args.server,
        "cloud_project": args.cloud_project,
        "live_table": args.live_table,
        "time_window": args.time_window,
    }
    failures = validate_entry(args.domain, entry, expected)
    if failures:
        print("FAIL: live-source preflight did not pass", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 2

    if args.print_entry:
        print(json.dumps({args.domain: entry}, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        print(
            "OK: live-source matrix fixed "
            f"domain={args.domain} repo={entry['repo']} branch={entry['branch']} "
            f"server={entry['server']} cloud_project={entry['cloud_project']} "
            f"live_table={entry['live_table']} time_window={entry['time_window']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
