from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_watchdog_repair_uses_core_prompt_catalog() -> None:
    shell = (ROOT / "src" / "scripts" / "nexo-watchdog.sh").read_text(encoding="utf-8")
    assert "watchdog-repair" in shell
    assert "render_core_prompt(" in shell
