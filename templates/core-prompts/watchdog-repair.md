WATCHDOG LEVEL 2 REPAIR — Automated diagnostic session.

The watchdog detected services that failed mechanical repair (launchctl/systemctl re-registration).
Your job: diagnose the root cause and fix it. Do NOT ask the user anything — resolve autonomously.

Each failure is tagged [core] or [personal]:
- [core] = part of NEXO public package — fix may need propagation to public repo
- [personal] = user-specific service — fix stays local only

FAILURES:
[[fail_details]]

STEPS:
1. Read the service config (plist on macOS, systemd unit on Linux) to understand the service
2. Check stderr/stdout logs for the actual error
3. Fix the root cause (missing file, bad config, dependency issue, etc.)
4. Reload the service and verify it is running (launchctl on macOS, systemctl on Linux)
5. Log what you did to [[nexo_home]]/runtime/logs/watchdog-repair-result.log
[[propagate_block]]

CONSTRAINTS:
- Do NOT modify CLAUDE.md, AGENTS.md, or any protected file
- Do NOT start interactive conversations
- Keep it under 5 minutes
- Log what you did to [[nexo_home]]/runtime/logs/watchdog-repair-result.log
