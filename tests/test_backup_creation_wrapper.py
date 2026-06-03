from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _reload_paths(monkeypatch, nexo_home: Path):
    monkeypatch.setenv("NEXO_HOME", str(nexo_home))
    import paths
    return importlib.reload(paths)


def test_create_backup_dir_uses_runtime_backups_and_unique_prefix(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")
    backup = paths.create_backup_dir("pre-update")

    assert backup.is_dir()
    assert backup.parent == paths.backups_dir()
    assert backup.name.startswith("pre-update-")

    second = paths.create_backup_dir("pre-update")
    assert second.is_dir()
    assert second != backup


def test_create_backup_path_uses_same_space_guard(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")
    target = paths.create_backup_path("pre-import-user-data", ".tar.gz")

    assert target.parent == paths.backups_dir()
    assert target.name.startswith("pre-import-user-data-")
    assert target.name.endswith(".tar.gz")


def test_create_backup_dir_context_post_prunes_after_snapshot(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")
    calls = []

    def fake_prune(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(paths, "run_runtime_backup_prune", fake_prune)
    with paths.create_backup_dir("pre-update") as backup:
        calls.clear()
        (backup / "payload.txt").write_text("snapshot")

    assert calls == [{"backups_root": backup.parent, "protect_paths": [backup]}]


def test_adaptive_backup_cap_scales_with_disk_size(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")

    def small_disk(_path):
        return SimpleNamespace(total=256 * 1024 ** 3, used=0, free=200 * 1024 ** 3)

    monkeypatch.setattr(paths.shutil, "disk_usage", small_disk)
    assert paths.backup_retention_cap_bytes() == int(256 * 1024 ** 3 * 0.05)

    def huge_disk(_path):
        return SimpleNamespace(total=8 * 1024 ** 4, used=0, free=7 * 1024 ** 4)

    monkeypatch.setattr(paths.shutil, "disk_usage", huge_disk)
    assert paths.backup_retention_cap_bytes() == 50 * 1024 ** 3
    assert paths.backup_retention_cap_bytes(configured="5G") == 5 * 1024 ** 3


def test_aggressive_prune_deletes_only_nexo_technical_backups(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")
    root = paths.backups_dir()
    root.mkdir(parents=True)

    for name in ("pre-update-old", "code-tree-old", "runtime-tree-old"):
        d = root / name
        d.mkdir()
        (d / "marker.txt").write_text("technical")

    protected = root / "shopify-backups"
    protected.mkdir()
    (protected / "order.csv").write_text("business")
    weekly = root / "weekly"
    weekly.mkdir()
    (weekly / "weekly-2026-05-19.db").write_text("weekly")
    hourly = root / "nexo-2026-05-19-1829.db"
    hourly.write_text("hourly")

    paths.aggressive_runtime_backup_prune(
        backups_root=root,
        min_free_bytes=10 ** 18,
        reason="test-force-escalation",
    )

    assert not (root / "pre-update-old").exists()
    assert not (root / "code-tree-old").exists()
    assert not (root / "runtime-tree-old").exists()
    assert protected.is_dir()
    assert weekly.is_dir()
    assert hourly.is_file()


def test_prune_protect_keeps_current_snapshot_in_emergency_mode(tmp_path, monkeypatch):
    paths = _reload_paths(monkeypatch, tmp_path / "nexo-home")
    root = paths.backups_dir()
    root.mkdir(parents=True)
    current = root / "pre-autoupdate-2026-06-03-175225"
    current.mkdir()
    (current / "nexo.db").write_text("current")
    old = root / "pre-autoupdate-2026-06-03-160000"
    old.mkdir()
    (old / "nexo.db").write_text("old")

    report = paths.run_runtime_backup_prune(
        backups_root=root,
        delete_all_technical=True,
        protect_paths=[current],
    )

    assert report["ok"] is True
    assert current.is_dir()
    assert not old.exists()
