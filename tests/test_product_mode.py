from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_product_mode(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    import paths
    import product_mode

    importlib.reload(paths)
    importlib.reload(product_mode)
    return product_mode


def test_enforce_desktop_product_contract_persists_marker_and_retires_evolution(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "brain").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXO_DESKTOP_MANAGED", "1")
    product_mode = _reload_product_mode(monkeypatch, home)

    report = product_mode.enforce_desktop_product_contract(source="test")

    assert report["applied"] is True
    mode_payload = json.loads(Path(report["mode_path"]).read_text())
    assert mode_payload["desktop_managed"] is True
    assert "evolution" in mode_payload["disabled_features"]

    objective = json.loads((home / "brain" / "evolution-objective.json").read_text())
    assert objective["evolution_enabled"] is False
    assert objective["evolution_mode"] == product_mode.DESKTOP_EVOLUTION_SUPPORT_MODE
    assert objective["support_ticket_mode"] is False
    assert objective["disabled_reason"] == product_mode.DESKTOP_EVOLUTION_RETIRED_REASON
    assert objective["disabled_by"] == "desktop_product"


def test_filter_blocked_crons_hides_desktop_managed_crons(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    product_mode = _reload_product_mode(monkeypatch, home)

    crons = [
        {"id": "evolution"},
        {"id": "dashboard"},
        {"id": "deep-sleep"},
    ]
    filtered = product_mode.filter_blocked_crons(crons)

    assert [cron["id"] for cron in filtered] == ["deep-sleep"]


def test_runtime_core_path_detection_is_scoped_to_install_core(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    core_target = home / "core" / "scripts" / "nexo-email-monitor.py"
    core_target.parent.mkdir(parents=True, exist_ok=True)
    core_target.write_text("#!/usr/bin/env python3\n")
    repo_target = tmp_path / "repo" / "src" / "server.py"
    repo_target.parent.mkdir(parents=True, exist_ok=True)
    repo_target.write_text("print('ok')\n")

    product_mode = _reload_product_mode(monkeypatch, home)

    assert product_mode.is_protected_runtime_core_path(str(core_target)) is True
    assert product_mode.is_protected_runtime_core_path(str(repo_target)) is False


def test_desktop_product_requested_detects_installed_desktop_app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    app_dir = home / "Applications" / "NEXO Desktop.app"
    app_dir.mkdir(parents=True, exist_ok=True)

    product_mode = _reload_product_mode(monkeypatch, tmp_path / "nexo-home")

    assert product_mode.desktop_product_install_detected(home) is True
