from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_installer_rejects_assistant_names_containing_product_brand() -> None:
    text = (REPO_ROOT / "bin" / "nexo-brain.js").read_text(encoding="utf-8")
    assert "normalized === reserved || normalized.includes(reserved)" in text
