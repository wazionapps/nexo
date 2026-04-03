#!/usr/bin/env python3
"""Skill script template.

This script is meant to be referenced by a Skill v2 definition.
It should use the stable NEXO CLI rather than importing internal DB modules.
"""

import argparse
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="")
    args = parser.parse_args()

    nexo_code = os.environ.get("NEXO_CODE", "")
    if not nexo_code:
        print("NEXO_CODE not set", file=sys.stderr)
        return 1

    cli_py = os.path.join(nexo_code, "cli.py")
    cmd = [
        sys.executable,
        cli_py,
        "scripts",
        "call",
        "nexo_learning_search",
        "--input",
        '{"query": %r}' % args.query,
    ]
    result = subprocess.run(cmd, text=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
