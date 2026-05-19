from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


GiB = 1024 ** 3


def _load_boot(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path / "nexo-home"))
    import paths
    from doctor.providers import boot

    paths = importlib.reload(paths)
    boot = importlib.reload(boot)
    return paths, boot


def test_doctor_purges_nexo_backups_before_alerting_and_stays_silent_if_recovered(tmp_path, monkeypatch):
    paths, boot = _load_boot(monkeypatch, tmp_path)
    usages = iter([
        SimpleNamespace(total=100 * GiB, used=97 * GiB, free=3 * GiB),
        SimpleNamespace(total=100 * GiB, used=94 * GiB, free=6 * GiB),
    ])
    sweep_calls = []

    monkeypatch.setattr(boot.shutil, "disk_usage", lambda _path: next(usages))
    monkeypatch.setattr(paths, "aggressive_runtime_backup_prune", lambda **_kwargs: {"steps": [{"step": "delete-all-technical"}]})
    monkeypatch.setattr(boot, "_post_disk_recovery_sweep", lambda *_args, **kwargs: sweep_calls.append(kwargs) or {"ok": True})

    result = boot.check_disk_space()

    assert result.status == "healthy"
    assert result.fixed is True
    assert not result.escalation_prompt
    assert sweep_calls


def test_doctor_alerts_only_after_nexo_self_cleanup_cannot_recover(tmp_path, monkeypatch):
    paths, boot = _load_boot(monkeypatch, tmp_path)
    usages = iter([
        SimpleNamespace(total=100 * GiB, used=97 * GiB, free=3 * GiB),
        SimpleNamespace(total=100 * GiB, used=98 * GiB, free=2 * GiB),
    ])

    monkeypatch.setattr(boot.shutil, "disk_usage", lambda _path: next(usages))
    monkeypatch.setattr(paths, "aggressive_runtime_backup_prune", lambda **_kwargs: {"steps": [{"step": "delete-all-technical"}]})
    monkeypatch.setattr(boot, "_post_disk_recovery_sweep", lambda *_args, **_kwargs: {"ok": True})

    result = boot.check_disk_space()

    assert result.status == "degraded"
    assert "NEXO has already cleaned up its own backups" in result.summary
    assert result.escalation_prompt


def test_doctor_runs_sweep_on_previous_low_to_ok_transition(tmp_path, monkeypatch):
    _paths, boot = _load_boot(monkeypatch, tmp_path)
    import paths

    state_file = paths.runtime_state_dir() / "disk-recovery-state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text('{"low": true}', encoding="utf-8")
    monkeypatch.setattr(boot.shutil, "disk_usage", lambda _path: SimpleNamespace(total=100 * GiB, used=90 * GiB, free=10 * GiB))
    sweep_calls = []
    monkeypatch.setattr(boot, "_post_disk_recovery_sweep", lambda *_args, **kwargs: sweep_calls.append(kwargs) or {"ok": True})

    result = boot.check_disk_space()

    assert result.status == "healthy"
    assert result.fixed is True
    assert sweep_calls
