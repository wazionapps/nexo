from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_backup_script_uses_runtime_backups_dir() -> None:
    text = _read("src/scripts/nexo-backup.sh")
    assert 'BACKUP_DIR="$NEXO_HOME/runtime/backups"' in text


def test_deep_sleep_script_uses_runtime_logs_dir() -> None:
    text = _read("src/scripts/nexo-deep-sleep.sh")
    assert 'LOG_DIR="$NEXO_HOME/runtime/logs"' in text


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
    assert "os.path.join(nexo_home, 'runtime', 'data', 'nexo.db')" in text
    assert "os.path.join(nexo_home, 'runtime', 'logs', 'self-audit-summary.json')" in text
    assert "os.path.join(nexo_home, 'personal', 'brain', 'evolution-objective.json')" in text


def test_watchdog_uses_runtime_paths_and_personal_config() -> None:
    text = _read("src/scripts/nexo-watchdog.sh")
    assert 'CONFIG_DIR="$NEXO_HOME/personal/config"' in text
    assert 'LOG_DIR="$NEXO_HOME/runtime/logs"' in text
    assert 'DATA_DIR="$NEXO_HOME/runtime/data"' in text
    assert 'BACKUP_DIR="$NEXO_HOME/runtime/backups"' in text
    assert "optionals_file = '$CONFIG_DIR/optionals.json'" in text
    assert "schedule_file = '$CONFIG_DIR/schedule.json'" in text
    assert "stdout_log = logs_dir + '/' + cid + '-stdout.log'" in text
    assert 'COG_DB="$DATA_DIR/cognitive.db"' in text
