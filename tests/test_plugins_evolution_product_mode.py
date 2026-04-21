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


def test_plugins_evolution_surface_is_disabled_when_desktop_managed(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    (home / "brain" / "evolution-objective.json").write_text(json.dumps({
        "evolution_enabled": False,
        "disabled_reason": "Disabled by NEXO Desktop product contract",
    }))

    product_mode, evolution_plugin = _reload_modules(monkeypatch, home)
    expected = f"Evolution is DISABLED: {product_mode.DESKTOP_EVOLUTION_DISABLED_REASON}"

    monkeypatch.setattr(evolution_plugin, "get_latest_metrics", lambda: {"autonomy": {"score": 90, "delta": 1}})
    monkeypatch.setattr(evolution_plugin, "get_evolution_history", lambda limit: [{"id": 1}])
    monkeypatch.setattr(evolution_plugin, "update_evolution_log_status", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not update logs when disabled")))

    assert evolution_plugin.handle_evolution_status() == expected
    assert evolution_plugin.handle_evolution_history() == expected
    assert evolution_plugin.handle_evolution_propose() == expected
    assert evolution_plugin.handle_evolution_approve(1, "ok") == expected
    assert evolution_plugin.handle_evolution_reject(1, "no") == expected
