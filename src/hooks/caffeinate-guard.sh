#!/bin/bash
# NEXO Caffeinate Guard — keeps the Mac awake so nocturnal processes run on schedule.
# Runs as a LaunchAgent with KeepAlive=true. If killed, launchd restarts it.
#
# Uses caffeinate -s (prevent system sleep) with -i (prevent idle sleep).
# The Mac screen can turn off but the system stays awake.

exec caffeinate -s -i -w $$
