"""Tests for v7.2.0 Guardian activation: G1 enforcer + G3 SSH wrapper + persist.

Coverage goals:
- G1: off / shadow / hard return paths, grace window, rate limit,
  fulfillment heuristic (cortex_evaluations / confidence_checks).
- G3 SSH: classifier positive/negative cases, gate shadow vs hard on Bash
  tool use.
- Guardian runtime config resolver: env > file > default priority.
- Auto-update persist: creates file with hard defaults, preserves
  operator customization, respects opt-out env, ephemeral skip.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# guardian_runtime_config resolver
# ---------------------------------------------------------------------------


@pytest.fixture
def overrides_home(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "personal" / "config").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import importlib
    import guardian_runtime_config
    importlib.reload(guardian_runtime_config)
    return home, guardian_runtime_config


def test_resolver_default_when_no_env_and_no_file(overrides_home):
    _, cfg = overrides_home
    cfg.invalidate_cache()
    assert cfg.resolve_guardian_flag("G1_ENFORCER_ACTIVE") == "shadow"
    assert cfg.resolve_guardian_flag("SOMETHING_ELSE", default="off") == "off"


def test_resolver_file_used_when_present(overrides_home, monkeypatch):
    home, cfg = overrides_home
    (home / "personal" / "config" / "guardian-runtime-overrides.json").write_text(
        json.dumps({"G1_ENFORCER_ACTIVE": "hard", "G4_ENFORCE_GUARD_CHECK": "off"})
    )
    cfg.invalidate_cache()
    monkeypatch.delenv("NEXO_G1_ENFORCER_ACTIVE", raising=False)
    monkeypatch.delenv("NEXO_G4_ENFORCE_GUARD_CHECK", raising=False)
    assert cfg.resolve_guardian_flag("G1_ENFORCER_ACTIVE") == "hard"
    assert cfg.resolve_guardian_flag("G4_ENFORCE_GUARD_CHECK") == "off"


def test_resolver_env_wins_over_file(overrides_home, monkeypatch):
    home, cfg = overrides_home
    (home / "personal" / "config" / "guardian-runtime-overrides.json").write_text(
        json.dumps({"G1_ENFORCER_ACTIVE": "hard"})
    )
    cfg.invalidate_cache()
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "off")
    assert cfg.resolve_guardian_flag("G1_ENFORCER_ACTIVE") == "off"


def test_resolver_tolerates_malformed_file(overrides_home, monkeypatch):
    home, cfg = overrides_home
    (home / "personal" / "config" / "guardian-runtime-overrides.json").write_text("{not valid json")
    cfg.invalidate_cache()
    monkeypatch.delenv("NEXO_G1_ENFORCER_ACTIVE", raising=False)
    assert cfg.resolve_guardian_flag("G1_ENFORCER_ACTIVE", default="shadow") == "shadow"


# ---------------------------------------------------------------------------
# G3 SSH remote-write classifier
# ---------------------------------------------------------------------------


SSH_POSITIVE_CASES = [
    ('ssh host "cat > /tmp/x"', "ssh_remote_shell_write"),
    ('ssh -o Foo=bar host "tee /etc/hosts"', "ssh_remote_shell_write"),
    ('ssh host "sed -i s/a/b/ file"', "ssh_remote_shell_write"),
    ('ssh host "echo hi > /tmp/y"', "ssh_remote_shell_write"),
    ('ssh host "rm -rf /tmp/z"', "ssh_remote_shell_write"),
    ('ssh host "cd /tmp && tee bar"', "ssh_remote_shell_write"),
    ("scp /local/file host:/remote/path", "scp_remote_write"),
    ("rsync -a /local host:/remote", "rsync_remote_write"),
    ("sftp -b /tmp/batch host", "sftp_batch_remote_write"),
]

SSH_NEGATIVE_CASES = [
    "ssh host ls /etc",
    'ssh host "ls -la /home"',
    "scp host:/remote/file /local/path",
    "rsync -a host:/remote /local",
    "sftp host",
    "ls -la",
    "git status",
]


@pytest.mark.parametrize("cmd,expected", SSH_POSITIVE_CASES)
def test_ssh_classifier_positive(cmd, expected):
    from hook_guardrails import _classify_ssh_remote_write
    assert _classify_ssh_remote_write(cmd) == expected


@pytest.mark.parametrize("cmd", SSH_NEGATIVE_CASES)
def test_ssh_classifier_negative(cmd):
    from hook_guardrails import _classify_ssh_remote_write
    assert _classify_ssh_remote_write(cmd) is None


# ---------------------------------------------------------------------------
# G1 enforcer — integration over a test sqlite DB
# ---------------------------------------------------------------------------


@pytest.fixture
def g1_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "runtime" / "data").mkdir(parents=True)
    db = home / "runtime" / "data" / "nexo.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS protocol_tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            goal TEXT DEFAULT '',
            response_mode TEXT DEFAULT '',
            opened_at TEXT DEFAULT '',
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS protocol_debt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            debt_type TEXT NOT NULL,
            severity TEXT DEFAULT 'warn',
            status TEXT DEFAULT 'open',
            evidence TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS cortex_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS confidence_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEXO_HOME", str(home))

    # Reload modules so their cached ``NEXO_HOME`` and overrides match.
    import importlib
    import guardian_runtime_config
    importlib.reload(guardian_runtime_config)
    import sys as _sys
    _sys.path.insert(0, str(SRC / "hooks"))
    import g1_enforcer
    importlib.reload(g1_enforcer)
    return home, db, g1_enforcer


def _iso_ago(seconds: int) -> str:
    """Format a naive UTC ISO string (protocol_tasks.opened_at style) N seconds ago."""
    import datetime as _dt
    moment = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _insert_task(db: Path, *, task_id: str, sid: str, mode: str, opened_at: str, status: str = "open") -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO protocol_tasks (task_id, session_id, goal, response_mode, opened_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, sid, "test task", mode, opened_at, status),
    )
    conn.commit()
    conn.close()


def test_g1_off_returns_none(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="defer", opened_at=_iso_ago(600))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "off")
    assert g1.check_response_contract_gate("sid-1") is None


def test_g1_grace_window_suppresses(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="defer", opened_at=_iso_ago(10))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    assert g1.check_response_contract_gate("sid-1") is None


def test_g1_hard_fires_when_contract_unfulfilled(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="defer", opened_at=_iso_ago(600))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    msg = g1.check_response_contract_gate("sid-1")
    assert msg is not None
    assert "G1 gate" in msg
    assert "defer" in msg
    # debt row written
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT debt_type, severity FROM protocol_debt WHERE session_id=?",
        ("sid-1",),
    ).fetchall()
    conn.close()
    assert ("g1_response_contract_unfulfilled", "warn") in rows


def test_g1_shadow_logs_debt_but_no_message(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="verify", opened_at=_iso_ago(600))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "shadow")
    msg = g1.check_response_contract_gate("sid-1")
    assert msg is None
    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM protocol_debt WHERE session_id=? AND debt_type=?",
        ("sid-1", "g1_response_contract_unfulfilled"),
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_g1_fulfilled_via_cortex_evaluation_suppresses_fire(g1_env, monkeypatch):
    home, db, g1 = g1_env
    opened = _iso_ago(600)
    _insert_task(db, task_id="T1", sid="sid-1", mode="defer", opened_at=opened)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cortex_evaluations (session_id, created_at) VALUES (?, datetime('now'))",
        ("sid-1",),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    assert g1.check_response_contract_gate("sid-1") is None


def test_g1_fulfilled_via_confidence_check_suppresses_fire(g1_env, monkeypatch):
    home, db, g1 = g1_env
    opened = _iso_ago(600)
    _insert_task(db, task_id="T1", sid="sid-1", mode="verify", opened_at=opened)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO confidence_checks (session_id, created_at) VALUES (?, datetime('now'))",
        ("sid-1",),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    assert g1.check_response_contract_gate("sid-1") is None


def test_g1_answer_mode_never_fires(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="answer", opened_at=_iso_ago(600))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    assert g1.check_response_contract_gate("sid-1") is None


def test_g1_rate_limit_suppresses_second_fire(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="ask", opened_at=_iso_ago(600))
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    first = g1.check_response_contract_gate("sid-1")
    assert first is not None
    second = g1.check_response_contract_gate("sid-1")
    assert second is None  # rate limited


def test_g1_closed_task_does_not_fire(g1_env, monkeypatch):
    home, db, g1 = g1_env
    _insert_task(db, task_id="T1", sid="sid-1", mode="defer", opened_at=_iso_ago(600), status="closed")
    monkeypatch.setenv("NEXO_G1_ENFORCER_ACTIVE", "hard")
    assert g1.check_response_contract_gate("sid-1") is None


# ---------------------------------------------------------------------------
# Auto-update persist Guardian hard defaults
# ---------------------------------------------------------------------------


@pytest.fixture
def persist_env(tmp_path, monkeypatch):
    home = tmp_path / "nexo"
    (home / "personal" / "config").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    import importlib
    import auto_update
    importlib.reload(auto_update)
    return home, auto_update


def test_persist_creates_file_with_hard_defaults(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_GUARDIAN_PERSIST_HARD", raising=False)
    # Force non-ephemeral path
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    persisted, msg = au._persist_guardian_hard_defaults(home)
    assert persisted is True
    assert msg is None
    config_path = home / "personal" / "config" / "guardian-runtime-overrides.json"
    assert config_path.is_file()
    payload = json.loads(config_path.read_text())
    assert payload["G1_ENFORCER_ACTIVE"] == "hard"
    assert payload["G3_ENFORCE_DESTRUCTIVE"] == "hard"
    assert payload["G3_SSH_ENFORCE_REMOTE_WRITE"] == "hard"
    assert payload["G4_ENFORCE_GUARD_CHECK"] == "hard"


def test_persist_preserves_operator_customization(persist_env, monkeypatch):
    home, au = persist_env
    config_path = home / "personal" / "config" / "guardian-runtime-overrides.json"
    config_path.write_text(json.dumps({
        "G1_ENFORCER_ACTIVE": "shadow",         # operator kept this intentionally soft
        "G3_ENFORCE_DESTRUCTIVE": "hard",
        # Missing keys should still be filled in with defaults.
    }))
    monkeypatch.delenv("NEXO_GUARDIAN_PERSIST_HARD", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    persisted, msg = au._persist_guardian_hard_defaults(home)
    assert persisted is True  # at least one key added
    payload = json.loads(config_path.read_text())
    assert payload["G1_ENFORCER_ACTIVE"] == "shadow"  # NOT overwritten
    assert payload["G3_ENFORCE_DESTRUCTIVE"] == "hard"
    assert payload["G3_SSH_ENFORCE_REMOTE_WRITE"] == "hard"  # added
    assert payload["G4_ENFORCE_GUARD_CHECK"] == "hard"       # added


def test_persist_idempotent_when_already_at_defaults(persist_env, monkeypatch):
    home, au = persist_env
    config_path = home / "personal" / "config" / "guardian-runtime-overrides.json"
    config_path.write_text(json.dumps({
        "G1_ENFORCER_ACTIVE": "hard",
        "G3_ENFORCE_DESTRUCTIVE": "hard",
        "G3_SSH_ENFORCE_REMOTE_WRITE": "hard",
        "G4_ENFORCE_GUARD_CHECK": "hard",
    }))
    monkeypatch.delenv("NEXO_GUARDIAN_PERSIST_HARD", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    mtime_before = config_path.stat().st_mtime_ns
    time.sleep(0.01)
    persisted, _msg = au._persist_guardian_hard_defaults(home)
    assert persisted is False
    # File untouched.
    assert config_path.stat().st_mtime_ns == mtime_before


def test_persist_respects_operator_opt_out_env(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.setenv("NEXO_GUARDIAN_PERSIST_HARD", "off")
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    persisted, msg = au._persist_guardian_hard_defaults(home)
    assert persisted is False
    assert msg is not None and "operator-opt-out" in msg
    config_path = home / "personal" / "config" / "guardian-runtime-overrides.json"
    assert not config_path.is_file()


def test_persist_skips_ephemeral_runtime(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: True)
    monkeypatch.delenv("NEXO_GUARDIAN_PERSIST_HARD", raising=False)
    persisted, msg = au._persist_guardian_hard_defaults(home)
    assert persisted is False
    assert msg is not None and "ephemeral" in msg


# ---------------------------------------------------------------------------
# Auto-update empirical adaptive-weights promotion during `nexo update`
# ---------------------------------------------------------------------------


def _write_adaptive_state(home: Path, payload: dict) -> Path:
    state_path = home / "personal" / "brain" / "adaptive_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return state_path


def _iso_utc(days_ago: int = 0) -> str:
    import datetime as _dt
    moment = _dt.datetime.utcnow() - _dt.timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


_SAMPLE_SHADOW = {
    "vibe": 0.2402,
    "corrections": 0.262,
    "brevity": 0.1345,
    "topic": 0.0962,
    "tool_errors": 0.1345,
    "git_diff": 0.1326,
}


def test_adaptive_promote_happy_path_activates_weights(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    _write_adaptive_state(home, {
        "shadow_weights": _SAMPLE_SHADOW,
        "shadow_weights_samples": 245,
        "learned_weights_first_date": _iso_utc(days_ago=3),
    })

    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is True
    assert msg is None

    state_path = home / "personal" / "brain" / "adaptive_state.json"
    data = json.loads(state_path.read_text())
    assert data["learned_weights"] == _SAMPLE_SHADOW
    assert data["learned_weights_samples"] == 245
    assert data["learned_weights_promoted_by"] == "nexo_update_empirical_v7_2_0"
    assert data["learned_weights_promoted_at"]


def test_adaptive_promote_noop_when_already_active(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    _write_adaptive_state(home, {
        "shadow_weights": _SAMPLE_SHADOW,
        "shadow_weights_samples": 245,
        "learned_weights": _SAMPLE_SHADOW,
        "learned_weights_first_date": _iso_utc(days_ago=30),
    })

    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is False
    assert msg is None


def test_adaptive_promote_skips_when_samples_below_threshold(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    _write_adaptive_state(home, {
        "shadow_weights": _SAMPLE_SHADOW,
        "shadow_weights_samples": 50,  # below the 200 bar
        "learned_weights_first_date": _iso_utc(days_ago=5),
    })

    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is False
    assert msg is None  # quiet no-op, not a "skipped:" message


def test_adaptive_promote_skips_when_days_below_threshold(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    _write_adaptive_state(home, {
        "shadow_weights": _SAMPLE_SHADOW,
        "shadow_weights_samples": 500,
        "learned_weights_first_date": _iso_utc(days_ago=1),
    })

    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is False
    assert msg is None


def test_adaptive_promote_respects_operator_opt_out(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.setenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", "off")
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)

    _write_adaptive_state(home, {
        "shadow_weights": _SAMPLE_SHADOW,
        "shadow_weights_samples": 245,
        "learned_weights_first_date": _iso_utc(days_ago=3),
    })

    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is False
    assert msg is not None and "operator-opt-out" in msg


def test_adaptive_promote_handles_missing_state_file_quietly(persist_env, monkeypatch):
    home, au = persist_env
    monkeypatch.delenv("NEXO_ADAPTIVE_EMPIRICAL_PROMOTION", raising=False)
    monkeypatch.setattr(au, "_is_ephemeral_runtime_install", lambda _dest: False)
    # No adaptive_state.json at all — fresh install.
    promoted, msg = au._maybe_promote_adaptive_weights_empirically(home)
    assert promoted is False
    assert msg is None
