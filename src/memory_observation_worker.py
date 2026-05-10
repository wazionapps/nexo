#!/usr/bin/env python3
from __future__ import annotations
"""Passive Memory Observations v2 worker.

This intentionally runs as a small callable/CLI wrapper around the DB layer.
Phase 2 does not alter normal answers; it only converts queued raw events into
auditable observations for later retrieval/viewer phases.
"""

import argparse
import json

from db import init_db, process_memory_observation_queue


def run_once(limit: int = 25) -> dict:
    init_db()
    return process_memory_observation_queue(limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process pending NEXO memory observations.")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args(argv)
    result = run_once(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
