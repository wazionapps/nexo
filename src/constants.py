"""Shared constants for NEXO scripts and runtime."""
from __future__ import annotations

# Safety-net timeout (seconds) for Claude CLI / automation subprocess calls.
# Applied across deep-sleep, synthesis, immune, evolution, catchup, and other
# headless scripts that invoke the configured automation backend. Three hours
# is long enough for legitimate long runs but short enough to prevent zombie
# subprocesses from blocking the pipeline indefinitely.
AUTOMATION_SUBPROCESS_TIMEOUT = 10800
