#!/usr/bin/env python3
"""Nudge background sync apps after disk pressure has been relieved."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import paths
from disk_recovery.registry import run_sweep


def _network_snapshot() -> dict:
    # Best-effort and intentionally coarse: enough to confirm activity changed
    # without collecting destinations or user content.
    try:
        import psutil  # type: ignore

        counters = psutil.net_io_counters()
        return {"bytes_sent": int(counters.bytes_sent), "bytes_recv": int(counters.bytes_recv)}
    except Exception:
        return {"bytes_sent": None, "bytes_recv": None}


def _network_delta(before: dict, after: dict) -> dict:
    out: dict[str, int | None] = {}
    for key in ("bytes_sent", "bytes_recv"):
        a = after.get(key)
        b = before.get(key)
        out[key] = int(a) - int(b) if isinstance(a, int) and isinstance(b, int) else None
    return out


def append_log(payload: dict) -> None:
    log_path = paths.operations_dir() / "post-disk-recovery-sweep.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run post-disk-recovery sync sweep")
    parser.add_argument("--platform", help="Override platform for tests: darwin/windows")
    parser.add_argument("--dry-run", action="store_true", help="Plan actions without running commands")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--network-window-seconds", type=float, default=0, help="Seconds between network snapshots")
    parser.add_argument("--reason", default="manual", help="Reason written to the operations log")
    args = parser.parse_args(argv)

    before = _network_snapshot()
    report = run_sweep(platform=args.platform, dry_run=args.dry_run)
    if args.network_window_seconds > 0:
        time.sleep(args.network_window_seconds)
    after = _network_snapshot()
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reason": args.reason,
        "network_window_seconds": args.network_window_seconds,
        "network_delta": _network_delta(before, after),
        **report,
    }
    append_log(payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
