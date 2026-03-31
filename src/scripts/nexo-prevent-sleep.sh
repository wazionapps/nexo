#!/bin/bash
# NEXO Prevent Sleep — keeps the machine awake so nocturnal processes run.
#
# macOS: uses caffeinate -s -i (prevent system + idle sleep)
# Linux: uses systemd-inhibit or caffeine if available, otherwise no-op
#
# Run as LaunchAgent (KeepAlive) or systemd service.

case "$(uname -s)" in
    Darwin)
        exec caffeinate -s -i -w $$
        ;;
    Linux)
        if command -v systemd-inhibit &>/dev/null; then
            exec systemd-inhibit --what=idle:sleep --who=nexo --why="NEXO nocturnal processes" sleep infinity
        elif command -v caffeine &>/dev/null; then
            exec caffeine
        else
            echo "[NEXO] No sleep prevention tool found. Install systemd-inhibit or caffeine."
            echo "[NEXO] Nocturnal processes may not run if the system sleeps."
            # Keep running so launchd/systemd doesn't restart loop
            exec sleep infinity
        fi
        ;;
    *)
        echo "[NEXO] Unsupported platform: $(uname -s)"
        exit 1
        ;;
esac
