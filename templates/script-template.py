#!/usr/bin/env python3
# nexo: name=example-script
# nexo: description=Example personal script using the stable NEXO CLI
# nexo: category=automation
# nexo: runtime=python
# nexo: timeout=60
# nexo: tools=nexo_learning_search,nexo_schedule_status
# nexo: interval_seconds=300
# nexo: schedule_required=false

"""Example personal script for NEXO.

This template demonstrates:
- Inline metadata for auto-discovery
- Safe CLI calls through nexo_helper
- Optional agent calls through the configured automation backend
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
    from nexo_helper import call_tool_text, run_automation_text
except ImportError:
    import os
    sys.path.insert(0, os.path.join(os.environ.get("NEXO_HOME", "~/.nexo"), "templates"))
    from nexo_helper import call_tool_text, run_automation_text

# If this script ever needs an autonomous model call:
#   1. use resolve_user_model() to get the user's configured model
#   2. pass it to run_automation_text(...)
#   3. DO NOT hardcode model names — the user picks their model once
# Example:
#   from client_preferences import resolve_user_model
#   result = run_automation_text(
#       "Summarize pending issues",
#       model=resolve_user_model(),
#   )


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
