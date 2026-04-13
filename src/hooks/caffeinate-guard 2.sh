#!/bin/bash
# NEXO Caffeinate Guard — keeps the Mac awake so nocturnal processes run on schedule.
# Runs as a LaunchAgent with KeepAlive=true. If killed, launchd restarts it.
#
# Uses the native macOS caffeinate helper. Closed-lid behavior remains
# best-effort and depends on the host setup.

exec /usr/bin/caffeinate -d -i -m -s /bin/bash -lc 'while :; do sleep 3600; done'
