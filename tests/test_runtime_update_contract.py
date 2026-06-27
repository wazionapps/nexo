from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _reload_auto_update(monkeypatch, home: Path, *, allow_ephemeral: bool = True):
    monkeypatch.setenv("NEXO_HOME", str(home))
    if allow_ephemeral:
        monkeypatch.setenv("NEXO_ALLOW_EPHEMERAL_INSTALL", "1")
    else:
        monkeypatch.delenv("NEXO_ALLOW_EPHEMERAL_INSTALL", raising=False)
    sys.modules.pop("auto_update", None)
    import auto_update as au

    return importlib.reload(au)


def _install_post_sync_stubs(monkeypatch, au, *, sync_ok: bool):
    monkeypatch.setattr(au, "_emit_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(au, "_parse_runtime_init_payload", lambda raw: {})
    monkeypatch.setattr(au, "_personal_schedule_reconcile_summary", lambda payload: ([], ""))
    monkeypatch.setattr(au, "_maybe_migrate_to_f06_layout", lambda: None)
    monkeypatch.setattr(au, "_ensure_f06_legacy_shims", lambda: None)
    monkeypatch.setattr(au, "_rewrite_f06_launch_agents", lambda: 0)
    monkeypatch.setattr(au, "_reinstall_runtime_pip_deps", lambda dest: True)
    monkeypatch.setattr(au, "_heal_deep_sleep_runtime", lambda dest: [])
    monkeypatch.setattr(au, "_migrate_effort_to_resonance", lambda dest: [])
    monkeypatch.setattr(au, "_bootstrap_profile_from_calibration_meta", lambda dest: [])
    monkeypatch.setattr(au, "_relocate_resonance_tiers_contract", lambda dest: [])
    monkeypatch.setattr(au, "_runtime_code_dir", lambda runtime_root: runtime_root / "core")

    sync_calls: list[dict] = []
    classifier_calls: list[str] = []

    monkeypatch.setitem(
        sys.modules,
        "runtime_power",
        SimpleNamespace(apply_power_policy=lambda: {"ok": True, "action": "noop"}),
    )
    monkeypatch.setitem(
        sys.modules,
        "client_sync",
        SimpleNamespace(
            sync_all_clients=lambda **kwargs: sync_calls.append(kwargs) or {"ok": sync_ok},
            sync_claude_code_model=lambda *_args, **_kwargs: {"ok": True, "action": "noop"},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "client_preferences",
        SimpleNamespace(normalize_client_preferences=lambda payload: payload),
    )
    monkeypatch.setitem(
        sys.modules,
        "model_defaults",
        SimpleNamespace(heal_runtime_profiles=lambda payload: (payload, [])),
    )
    monkeypatch.setitem(
        sys.modules,
        "calibration_migration",
        SimpleNamespace(apply_v6_purge=lambda nexo_home: {"status": "noop"}),
    )
    monkeypatch.setattr(
        au,
        "_maybe_install_local_classifier",
        lambda: classifier_calls.append("classifier"),
    )

    def _fake_run(cmd, *args, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "-c":
            return SimpleNamespace(returncode=0, stdout="{}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(au.subprocess, "run", _fake_run)
    return sync_calls, classifier_calls


def test_runtime_post_sync_keeps_client_sync_and_classifier_bootstrap_enabled(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    sync_calls, classifier_calls = _install_post_sync_stubs(monkeypatch, au, sync_ok=True)

    ok, actions = au._run_runtime_post_sync(tmp_path)

    assert ok is True
    assert "db+personal-sync" in actions
    assert "layout-heal" in actions
    assert "pip-deps" in actions
    assert "client-sync" in actions
    assert "classifier-install" in actions
    assert "runtime-repair-baseline" in actions
    assert (tmp_path / "operations" / "last-repair-baseline.json").is_file()
    assert classifier_calls == ["classifier"]
    assert len(sync_calls) == 1
    assert sync_calls[0]["nexo_home"] == tmp_path
    assert sync_calls[0]["runtime_root"] == tmp_path / "core"
    assert sync_calls[0]["auto_install_missing_claude"] is True


def test_runtime_post_sync_allows_bounded_memory_fabric_repair_time(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    _install_post_sync_stubs(monkeypatch, au, sync_ok=True)
    timeouts: list[int] = []

    def _fake_run(cmd, *args, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "-c":
            timeouts.append(kwargs.get("timeout"))
            return SimpleNamespace(returncode=0, stdout="{}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(au.subprocess, "run", _fake_run)

    ok, actions = au._run_runtime_post_sync(tmp_path)

    assert ok is True
    assert "db+personal-sync" in actions
    assert timeouts
    assert timeouts[0] == au.RUNTIME_POST_SYNC_TIMEOUT_SECONDS
    assert au.RUNTIME_POST_SYNC_TIMEOUT_SECONDS >= 180


def test_runtime_post_sync_reports_memory_fabric_repair_and_unreconciled_rows(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    _install_post_sync_stubs(monkeypatch, au, sync_ok=True)
    monkeypatch.setattr(
        au,
        "_parse_runtime_init_payload",
        lambda raw: {
            "memory_fabric": {
                "ok": False,
                "transcripts": {"indexed": 2},
                "backups": {"inserted": 1},
                "health": {
                    "historical_diaries": {"backup_rows_unreconciled": 4},
                    "issues": [{"code": "backup_diaries_not_reconciled"}],
                },
            }
        },
    )

    ok, actions = au._run_runtime_post_sync(tmp_path)

    assert ok is True
    assert "memory-fabric-repaired:3" in actions
    assert "memory-fabric-unreconciled:4" in actions
    assert "memory-fabric-warning" in actions


def test_runtime_post_sync_does_not_skip_classifier_when_client_sync_warns(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    sync_calls, classifier_calls = _install_post_sync_stubs(monkeypatch, au, sync_ok=False)

    ok, actions = au._run_runtime_post_sync(tmp_path)

    assert ok is True
    assert "client-sync-warning" in actions
    assert "classifier-install" in actions
    assert classifier_calls == ["classifier"]
    assert len(sync_calls) == 1


def test_runtime_post_sync_skips_classifier_on_ephemeral_runtime(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    au = _reload_auto_update(monkeypatch, tmp_path, allow_ephemeral=False)
    sync_calls, classifier_calls = _install_post_sync_stubs(monkeypatch, au, sync_ok=True)

    ok, actions = au._run_runtime_post_sync(tmp_path)

    assert ok is True
    assert "classifier-install" in actions
    assert classifier_calls == []
    assert len(sync_calls) == 1


def test_desktop_managed_dependency_repair_requires_python_312_venv():
    text = (Path(__file__).resolve().parents[1] / "src" / "auto_update.py").read_text()

    assert "desktop_product_requested" in text
    assert "enforce_desktop_product_contract" in text
    assert "DESKTOP_EVOLUTION_RETIRED_REASON" in text
    assert "def _managed_venv_python_supported(python_bin: Path | str) -> bool:" in text
    assert "return version[:2] == (3, 12)" in text
    assert "def _resolve_managed_venv_base_python() -> str:" in text
    assert '"/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"' in text
    assert 'shutil.which("python3.12")' in text
    assert "def _archive_incompatible_runtime_venv" in text
    assert "_archive_incompatible_runtime_venv(runtime_root, reason=reason)" in text
    assert "managed venv unavailable for Desktop dependency repair" in text
    assert "elif not desktop_product_requested():" in text
