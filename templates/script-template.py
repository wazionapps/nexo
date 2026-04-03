#!/usr/bin/env python3
# nexo: name=example-script
# nexo: description=Example personal script using the stable NEXO CLI
# nexo: runtime=python
# nexo: timeout=60
# nexo: tools=nexo_learning_search,nexo_schedule_status

"""Example personal script for NEXO.

This template demonstrates:
- Inline metadata for auto-discovery
- Safe CLI calls through nexo_helper
- Timeout handling (via metadata)
- argparse for user arguments
- No direct DB access
- Clean exit codes
"""

import argparse
import sys

# nexo_helper.py is in NEXO_HOME/templates/ — copy it next to your script
# or add the templates dir to your path
try:
    from nexo_helper import call_tool_text
except ImportError:
    import os
    sys.path.insert(0, os.path.join(os.environ.get("NEXO_HOME", "~/.nexo"), "templates"))
    from nexo_helper import call_tool_text


def main():
    parser = argparse.ArgumentParser(description="Example NEXO personal script")
    parser.add_argument("--query", default="cron", help="Search query for learnings")
    args = parser.parse_args()

    print(f"Searching learnings for: {args.query}")
    result = call_tool_text("nexo_learning_search", {"query": args.query})
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
