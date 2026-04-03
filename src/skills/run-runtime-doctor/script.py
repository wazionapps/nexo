#!/usr/bin/env python3
import os
import subprocess
import sys


def main() -> int:
    tier = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "runtime"
    nexo_code = os.environ.get("NEXO_CODE", "")
    if not nexo_code:
        print("NEXO_CODE not set", file=sys.stderr)
        return 1

    cli_py = os.path.join(nexo_code, "cli.py")
    cmd = [sys.executable, cli_py, "doctor", "--tier", tier, "--json"]
    result = subprocess.run(cmd, text=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
