from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_modules(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    import paths
    import product_mode
    import plugins.evolution as evolution_plugin

    importlib.reload(paths)
    importlib.reload(product_mode)
    importlib.reload(evolution_plugin)
    return product_mode, evolution_plugin


def test_plugins_evolution_surface_is_retired_when_desktop_managed(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    (home / "brain" / "evolution-objective.json").write_text(json.dumps({
        "evolution_enabled": True,
        "dimensions": {"autonomy": {"current": 80, "target": 90}},
    }))

    _, evolution_plugin = _reload_modules(monkeypatch, home)

    assert evolution_plugin.TOOLS == []
    assert evolution_plugin.handle_evolution_status() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_history() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_propose() == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_approve(1, "ok") == "Evolution has been removed from NEXO Desktop."
    assert evolution_plugin.handle_evolution_reject(1, "no") == "Evolution has been removed from NEXO Desktop."


def test_standalone_evolution_runner_is_removed_from_runtime():
    assert (REPO_SRC / "scripts" / "nexo-evolution-run.py").exists() is False
    assert (REPO_SRC / "evolution_cycle.py").exists() is False
