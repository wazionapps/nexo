from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_backup_script_uses_runtime_backups_dir() -> None:
    text = _read("src/scripts/nexo-backup.sh")
    assert 'BACKUP_DIR="$NEXO_HOME/runtime/backups"' in text


def test_backup_script_reconciles_memory_fabric_before_prune() -> None:
    text = _read("src/scripts/nexo-backup.sh")
    assert "reconcile_memory_fabric_before_prune()" in text
    assert "    reconcile_memory_fabric_before_prune" in text
    assert "memory_fabric.reconcile_backup_diaries" in text
    assert text.index("reconcile_memory_fabric_before_prune") < text.index('python3 "$PRUNER"')
    assert text.index("reconcile_memory_fabric_before_prune") < text.index('weekly = base / "weekly"')


def test_deep_sleep_script_uses_runtime_logs_dir() -> None:
    text = _read("src/scripts/nexo-deep-sleep.sh")
    assert 'LOG_DIR="$NEXO_HOME/runtime/logs"' in text


def test_deep_sleep_script_fails_closed_on_phase_errors() -> None:
    text = _read("src/scripts/nexo-deep-sleep.sh")
    assert 'if ! python3 "$SCRIPT_DIR/deep-sleep/extract.py" "$RUN_ID"' in text
    assert 'if ! python3 "$SCRIPT_DIR/deep-sleep/synthesize.py" "$RUN_ID"' in text
    assert 'if ! python3 "$SCRIPT_DIR/deep-sleep/apply_findings.py" "$RUN_ID"' in text
    assert "Synthesis output missing. Watermark NOT updated" in text
    assert "Falling back to extractions only" not in text


def test_sleep_process_lock_is_cleaned_on_exit_and_shutdown_signals() -> None:
    text = _read("src/scripts/nexo-sleep.py")
    assert "atexit.register(_cleanup_process_lock)" in text
    assert "signal.signal(signal.SIGINT, _handle_shutdown_signal)" in text
    assert "signal.signal(signal.SIGTERM, _handle_shutdown_signal)" in text
    assert "PROCESS_LOCK.unlink(missing_ok=True)" in text


def test_cron_wrapper_prefers_personal_config_for_keychain_pass() -> None:
    text = _read("src/scripts/nexo-cron-wrapper.sh")
    assert 'KEYCHAIN_PASS_FILE="$NEXO_HOME/personal/config/.keychain-pass"' in text


def test_capture_session_uses_personal_brain_buffer() -> None:
    text = _read("src/hooks/capture-session.sh")
    assert 'BRAIN_DIR="$NEXO_HOME/personal/brain"' in text
    assert 'BUFFER="$BRAIN_DIR/session_buffer.jsonl"' in text


def test_session_start_uses_runtime_and_personal_layout() -> None:
    text = _read("src/hooks/session-start.sh")
    assert 'BRIEFING_FILE="$COORDINATION_DIR/session-briefing.txt"' in text
    assert 'date +%s > "$OPERATIONS_DIR/.session-start-ts"' in text
    assert 'NEXO_DISABLE_SHELL_HOOK_RECORD' in text
    assert "os.path.join(nexo_home, 'runtime', 'data', 'nexo.db')" in text
    assert "os.path.join(nexo_home, 'runtime', 'logs', 'self-audit-summary.json')" in text
    assert "os.path.join(nexo_home, 'personal', 'brain', 'evolution-objective.json')" in text
    assert "started_at > (strftime('%s','now') - 24*3600)" in text


def test_watchdog_uses_runtime_paths_and_personal_config() -> None:
    text = _read("src/scripts/nexo-watchdog.sh")
    assert 'CONFIG_DIR="$NEXO_HOME/personal/config"' in text
    assert 'LOG_DIR="$NEXO_HOME/runtime/logs"' in text
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in text
    assert 'COGNITIVE_DIR="$NEXO_HOME/runtime/cognitive"' in text
    assert 'BACKUP_DIR="$NEXO_HOME/runtime/backups"' in text
    assert "optionals_file = '$CONFIG_DIR/optionals.json'" in text
    assert "schedule_file = '$CONFIG_DIR/schedule.json'" in text
    assert "stdout_log = logs_dir + '/' + cid + '-stdout.log'" in text
    assert 'COG_DB="$COGNITIVE_DIR/cognitive.db"' in text


def test_watchdog_keeps_alive_in_flight_work_observational() -> None:
    text = _read("src/scripts/nexo-watchdog.sh")
    assert "long-running, process alive; observing" in text
    assert 'status="WARN"\n          details="${details}In-flight for ${stale_age} (long-running' not in text


def test_compaction_and_tool_log_hooks_use_runtime_layout() -> None:
    capture = _read("src/hooks/capture-tool-logs.sh")
    assert 'OPERATIONS_DIR="$NEXO_HOME/runtime/operations"' in capture
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in capture
    assert 'LOG_DIR="$OPERATIONS_DIR/tool-logs"' in capture
    assert 'COUNTER_DIR="$OPERATIONS_DIR/counters"' in capture
    assert 'NEXO_DB="$DATA_DIR/nexo.db"' in capture

    pre_compact = _read("src/hooks/pre-compact.sh")
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in pre_compact
    assert 'OPERATIONS_DIR="$NEXO_HOME/runtime/operations"' in pre_compact
    assert 'NEXO_DB="$DATA_DIR/nexo.db"' in pre_compact
    assert 'LOG_FILE="$OPERATIONS_DIR/tool-logs/${TODAY}.jsonl"' in pre_compact
    assert "import checkpoint_policy" in pre_compact

    post_compact = _read("src/hooks/post-compact.sh")
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in post_compact
    assert 'OPERATIONS_DIR="$NEXO_HOME/runtime/operations"' in post_compact
    assert 'NEXO_DB="$DATA_DIR/nexo.db"' in post_compact
    assert 'LOG_FILE="$OPERATIONS_DIR/tool-logs/${TODAY}.jsonl"' in post_compact
    assert 'AUTONOMY_STATE_FILE="$DATA_DIR/autonomy_mandate.json"' in post_compact


def test_auxiliary_hooks_use_runtime_or_core_locations_first() -> None:
    briefing = _read("src/hooks/daily-briefing-check.sh")
    assert 'OPERATIONS_DIR="$NEXO_HOME/runtime/operations"' in briefing
    assert 'BRIEFING_FILE="$OPERATIONS_DIR/.briefing-last-sent"' in briefing
    assert 'FLAG_FILE="$OPERATIONS_DIR/.briefing-pending"' in briefing

    inbox = _read("src/hooks/inbox-hook.sh")
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in inbox
    assert 'DB="$DATA_DIR/nexo.db"' in inbox

    # Phase 2.3 (operator decision 11-jun) — the dead heartbeat-enforcement
    # trio was removed from src/hooks: its .sh launchers were never
    # registered in client settings, post_tool_use discarded their stdout,
    # and the update cleanup lists already retire them from installs.
    # The contract now pins their ABSENCE so they cannot quietly return.
    import os
    for retired in (
        "src/hooks/heartbeat-enforcement.py",
        "src/hooks/heartbeat-user-msg.sh",
        "src/hooks/heartbeat-posttool.sh",
    ):
        assert not os.path.exists(os.path.join(REPO_ROOT, retired)), (
            f"{retired} was retired (Phase 2.3) and must not reappear"
        )
