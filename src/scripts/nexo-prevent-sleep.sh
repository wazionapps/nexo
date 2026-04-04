#!/bin/bash
# NEXO Prevent Sleep — keeps the machine awake so nocturnal processes run.
#
# macOS: uses native /usr/bin/caffeinate for best-effort background availability
# Linux: uses systemd-inhibit or caffeine if available, otherwise no-op
#
# Run as LaunchAgent (KeepAlive) or systemd service.

case "$(uname -s)" in
    Darwin)
        if [[ ! -x /usr/bin/caffeinate ]]; then
            echo "[NEXO] /usr/bin/caffeinate not found. macOS power helper unavailable."
            exit 1
        fi
        # Keep the helper alive as long as this service runs. On laptops with the
        # lid closed, actual behavior still depends on hardware and OS policy.
        exec /usr/bin/caffeinate -d -i -m -s /bin/bash -lc 'while :; do sleep 3600; done'
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
