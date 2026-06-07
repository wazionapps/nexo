from __future__ import annotations

from copy import deepcopy
import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_product_surface_alignment.py"
REPO_SRC = REPO_ROOT / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

spec = importlib.util.spec_from_file_location("verify_product_surface_alignment", SCRIPT_PATH)
assert spec is not None
surface_alignment = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(surface_alignment)


def _backend_fixture(tmp_path: Path, *, extra_route: str = "") -> Path:
    routes = tmp_path / "routes"
    routes.mkdir()
    route_lines: list[str] = []
    for surface in surface_alignment.EXPECTED_BACKEND_SURFACES:
        for fragment in surface["route_fragments"]:
            route_lines.append(f"Route::get('{fragment}', [FakeController::class, 'index']);")
    if extra_route:
        route_lines.append(extra_route)
    (routes / "web.php").write_text("\n".join(route_lines), encoding="utf-8")
    return tmp_path


def test_product_surface_alignment_accepts_catalog_and_backend_fixture(tmp_path: Path):
    result = surface_alignment.validate_product_surface_alignment(
        backend_root=_backend_fixture(tmp_path),
        require_backend=True,
    )

    assert result["ok"] is True, result
    assert result["surfaces_checked"] == len(surface_alignment.EXPECTED_BACKEND_SURFACES)


def test_product_surface_alignment_rejects_unmapped_managed_backend_prefix(tmp_path: Path):
    backend = _backend_fixture(
        tmp_path,
        extra_route="Route::get('nexo-new-provider/resources', [FakeController::class, 'index']);",
    )

    result = surface_alignment.validate_product_surface_alignment(
        backend_root=backend,
        require_backend=True,
    )

    assert result["ok"] is False
    assert "unmapped managed backend route prefixes: nexo-new-provider" in result["errors"]


def test_product_surface_alignment_rejects_missing_catalog_source_ref(tmp_path: Path):
    from product_knowledge import load_product_catalog

    catalog = deepcopy(load_product_catalog())
    capability = next(
        item
        for item in catalog["capabilities"]
        if item["id"] == "nexo_managed_communications_providers"
    )
    capability["source_refs"] = [
        ref for ref in capability["source_refs"] if "NexoTwilioController" not in ref
    ]

    result = surface_alignment.validate_product_surface_alignment(
        catalog=catalog,
        backend_root=_backend_fixture(tmp_path),
        require_backend=True,
    )

    assert result["ok"] is False
    assert any("NexoTwilioController" in error for error in result["errors"])
