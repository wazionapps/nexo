#!/usr/bin/env python3
"""Inspect sensitive files without dumping secret values."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable


SENSITIVE_NAME_RE = re.compile(
    r"(^\.env($|\.)|secret|credential|token|apikey|api_key|password|passwd|id_rsa|\.pem$|\.p12$|\.pfx$|key\.json$)",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]{1,120})\s*[:=]\s*(.*)$")
BLOCKED_READERS = {"cat", "head", "sed"}


def is_sensitive_path(path: str | Path) -> bool:
    p = Path(path)
    return any(SENSITIVE_NAME_RE.search(part) for part in p.parts)


def mask_value(value: str) -> str:
    stripped = value.strip().strip("'\"")
    if not stripped:
        return "<empty>"
    digest = hashlib.sha256(stripped.encode("utf-8", "ignore")).hexdigest()[:12]
    if len(stripped) <= 8:
        return f"<masked len={len(stripped)} sha256={digest}>"
    return f"{stripped[:2]}...{stripped[-2:]} <len={len(stripped)} sha256={digest}>"


def iter_env_like(lines: Iterable[str]) -> Iterable[tuple[str, str]]:
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT_RE.match(line)
        if match:
            yield match.group(1), mask_value(match.group(2))


def iter_json_like(text: str) -> Iterable[tuple[str, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return

    def walk(prefix: str, obj) -> Iterable[tuple[str, str]]:
        if isinstance(obj, dict):
            for key, value in obj.items():
                name = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, (dict, list)):
                    yield from walk(name, value)
                else:
                    yield name, mask_value(str(value))
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                name = f"{prefix}[{idx}]"
                if isinstance(value, (dict, list)):
                    yield from walk(name, value)
                else:
                    yield name, mask_value(str(value))

    yield from walk("", data)


def inspect_file(path: Path) -> int:
    if not path.exists():
        print(f"FAIL: file not found: {path}", file=sys.stderr)
        return 2
    if not is_sensitive_path(path):
        print(f"FAIL: refusing to inspect non-sensitive path with this helper: {path}", file=sys.stderr)
        return 2
    data = path.read_text(encoding="utf-8", errors="replace")
    rows = list(iter_json_like(data)) if path.suffix.lower() == ".json" else []
    if not rows:
        rows = list(iter_env_like(data.splitlines()))
    if not rows:
        digest = hashlib.sha256(data.encode("utf-8", "ignore")).hexdigest()[:12]
        print(f"_file <opaque len={len(data)} sha256={digest}>")
        return 0
    for name, masked in rows:
        print(f"{name}={masked}")
    return 0


def check_command(command: str) -> int:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        print(f"FAIL: cannot parse command: {exc}", file=sys.stderr)
        return 2
    if not parts:
        print("OK: empty command")
        return 0
    reader = Path(parts[0]).name
    paths = [part for part in parts[1:] if not part.startswith("-") and part not in {"-n", "-e"}]
    if reader in BLOCKED_READERS and any(is_sensitive_path(path) for path in paths):
        print(f"BLOCKED: {reader} would dump a sensitive file; use nexo-safe-secret-view.py --file", file=sys.stderr)
        return 2
    print("OK: command does not match a blocked full-read pattern")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Sensitive file to inspect safely")
    parser.add_argument("--check-command", help="Reject dangerous full-read commands")
    args = parser.parse_args(argv)
    if bool(args.file) == bool(args.check_command):
        parser.error("pass exactly one of --file or --check-command")
    if args.check_command:
        return check_command(args.check_command)
    return inspect_file(Path(args.file).expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
