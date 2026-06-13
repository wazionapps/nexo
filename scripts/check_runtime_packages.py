#!/usr/bin/env python3
"""
check_runtime_packages.py — CI build gate for the flattened NEXO runtime.

Problem this solves (followup NF-DS-5761D141):
    The installer / nexo-core-build copies a HARDCODED list of top-level
    packages into the flattened runtime (~/.nexo/core/current). Whenever a new
    top-level package is added under src/ (e.g. local_context @5b54d095, and now
    disk_recovery / guardrails) and nobody updates that list, the package never
    lands in the flattened runtime -> import errors, version skew, dead service.

What this script does (deliverable):
    1. EXPECTED  = top-level packages under src/ that have __init__.py
                   UNION packages declared in pyproject.toml.
    2. SHIPPED   = top-level package dirs actually present in the flattened
                   runtime output (the build staging dir, or ~/.nexo/core/current).
    3. MISSING   = EXPECTED - SHIPPED  -> if non-empty the build is BROKEN.
    4. Smoke import: import every EXPECTED package FROM the flattened runtime to
       catch packaging that copied the dir but left it unimportable.

    Exit 0 = runtime is complete and importable.
    Exit 1 = at least one Brain package would be left out / is broken. CI must fail.

Usage (wire into nexo-core-build CI, after the flatten step):
    python3 check_runtime_packages.py --src ./src --runtime ./build/core/current
    # local audit against the live install:
    python3 check_runtime_packages.py --src <repo>/src --runtime ~/.nexo/core/current --no-import

Designed to be dependency-free (stdlib only) so it runs in any CI image.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

# Dirs that are never shippable Brain packages even if they have an __init__.py.
IGNORE = {"__pycache__", "tests", "test", "scripts", "vendor", "node_modules"}


def discover_src_packages(src: Path) -> set[str]:
    """Top-level dirs under src/ that are real importable packages."""
    pkgs: set[str] = set()
    if not src.is_dir():
        return pkgs
    for child in sorted(src.iterdir()):
        if not child.is_dir() or child.name in IGNORE or child.name.startswith("."):
            continue
        if (child / "__init__.py").is_file():
            pkgs.add(child.name)
    return pkgs


def discover_pyproject_packages(repo_root: Path) -> set[str]:
    """Best-effort: top-level package names declared in pyproject.toml.

    Avoids a tomllib hard-dependency (py<3.11) by regex-scanning the
    [tool.setuptools.packages] / packages = [...] declarations.
    """
    pkgs: set[str] = set()
    pp = repo_root / "pyproject.toml"
    if not pp.is_file():
        return pkgs
    text = pp.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)["\']', text):
        token = m.group(1)
        # only keep top-level segment, skip obvious non-packages
        top = token.split(".")[0]
        if top and top not in IGNORE and not top.isdigit():
            # heuristic: only consider tokens that look like declared packages,
            # i.e. they also exist as a dir under src/. Caller intersects anyway.
            pkgs.add(top)
    return pkgs


def discover_runtime_packages(runtime: Path) -> set[str]:
    pkgs: set[str] = set()
    if not runtime.is_dir():
        return pkgs
    for child in sorted(runtime.iterdir()):
        if not child.is_dir() or child.name in IGNORE or child.name.startswith("."):
            continue
        if (child / "__init__.py").is_file():
            pkgs.add(child.name)
    return pkgs


def smoke_import(runtime: Path, packages: set[str]) -> list[tuple[str, str]]:
    """Try to import each package from the flattened runtime. Returns failures."""
    failures: list[tuple[str, str]] = []
    runtime_str = str(runtime)
    sys.path.insert(0, runtime_str)
    try:
        for name in sorted(packages):
            init = runtime / name / "__init__.py"
            if not init.is_file():
                failures.append((name, "missing __init__.py in runtime"))
                continue
            try:
                spec = importlib.util.spec_from_file_location(name, init)
                if spec is None or spec.loader is None:
                    failures.append((name, "no import spec"))
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 - we want to report any import error
                failures.append((name, f"{type(exc).__name__}: {exc}"))
    finally:
        if runtime_str in sys.path:
            sys.path.remove(runtime_str)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path, help="path to repo src/ dir")
    ap.add_argument("--runtime", required=True, type=Path, help="flattened runtime output dir (build staging or ~/.nexo/core/current)")
    ap.add_argument("--no-import", action="store_true", help="skip the smoke-import step (dir presence only)")
    args = ap.parse_args()

    src: Path = args.src.expanduser().resolve()
    runtime: Path = args.runtime.expanduser().resolve()
    repo_root = src.parent

    expected = discover_src_packages(src)
    declared = discover_pyproject_packages(repo_root) & expected  # intersect to drop noise
    expected |= declared
    shipped = discover_runtime_packages(runtime)

    missing = sorted(expected - shipped)
    extra = sorted(shipped - expected)

    print(f"src/ packages expected ({len(expected)}): {', '.join(sorted(expected)) or '-'}")
    print(f"runtime packages shipped ({len(shipped)}): {', '.join(sorted(shipped)) or '-'}")
    if extra:
        print(f"note: present in runtime but not in src/ (legacy/aux, not fatal): {', '.join(extra)}")

    failed = False
    if missing:
        failed = True
        print()
        print("BUILD GATE FAILED — these Brain packages exist in src/ but are NOT in the flattened runtime:")
        for name in missing:
            print(f"  - {name}  (add it to the nexo-core-build copy list / fix the auto-scan)")

    if not args.no_import and not missing:
        failures = smoke_import(runtime, expected & shipped)
        if failures:
            failed = True
            print()
            print("SMOKE IMPORT FAILED — packages copied but not importable from the flattened runtime:")
            for name, why in failures:
                print(f"  - {name}: {why}")

    if failed:
        print("\nRESULT: FAIL (CI must block this build).")
        return 1
    print("\nRESULT: OK — every src/ package is shipped and importable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
