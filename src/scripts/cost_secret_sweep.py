#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cost_secret_sweep import append_jsonl_report, default_paths, run_sweep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the daily cost/secret priority queue.")
    parser.add_argument("--path", action="append", default=[], help="Glob to scan. May be repeated.")
    parser.add_argument("--output", default="", help="JSONL output path.")
    args = parser.parse_args(argv)

    paths = args.path or default_paths()
    report = run_sweep(paths=paths)
    if args.output:
        append_jsonl_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

