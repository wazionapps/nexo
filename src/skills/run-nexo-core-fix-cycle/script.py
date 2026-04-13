#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


AREA_MAP = {
    "protocol": {
        "files": [
            "src/plugins/protocol.py",
            "src/db/_protocol.py",
            "src/db/__init__.py",
            "tests/test_protocol.py",
        ],
        "tests": [
            "tests/test_protocol.py",
        ],
    },
    "plane": {
        "files": [
            "src/doctor/orchestrator.py",
            "src/plugins/doctor.py",
            "src/server.py",
            "tests/test_doctor.py",
        ],
        "tests": [
            "tests/test_doctor.py",
        ],
    },
    "guard": {
        "files": [
            "src/hook_guardrails.py",
            "tests/test_hook_guardrails.py",
        ],
        "tests": [
            "tests/test_hook_guardrails.py",
        ],
    },
    "cortex": {
        "files": [
            "src/plugins/cortex.py",
            "tests/test_cortex_decisions.py",
        ],
        "tests": [
            "tests/test_cortex_decisions.py",
        ],
    },
    "release": {
        "files": [
            "scripts/verify_release_readiness.py",
            "src/skills/run-release-final-audit/script.py",
            "tests/test_verify_release_readiness.py",
            "tests/test_release_readiness_baseline.py",
        ],
        "tests": [
            "tests/test_verify_release_readiness.py",
            "tests/test_release_readiness_baseline.py",
        ],
    },
}


def _is_repo_root(candidate: Path) -> bool:
    return (
        (candidate / "package.json").is_file()
        and (candidate / "src" / "server.py").is_file()
        and (candidate / "tests").is_dir()
    )


def _normalize_candidate(raw: str | Path) -> Path:
    candidate = Path(raw).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if candidate.name == "src" and (candidate / "server.py").is_file():
        candidate = candidate.parent
    return candidate


def _resolve_repo_root_from_atlas() -> Path | None:
    homes = []
    env_home = os.environ.get("NEXO_HOME", "").strip()
    if env_home:
        homes.append(Path(env_home).expanduser())
    homes.extend((Path.home() / ".nexo", Path.home() / "claude"))

    seen = set()
    for home in homes:
        key = str(home)
        if key in seen:
            continue
        seen.add(key)
        atlas_path = home / "brain" / "project-atlas.json"
        if not atlas_path.is_file():
            continue
        try:
            payload = json.loads(atlas_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        nexo = payload.get("nexo") if isinstance(payload, dict) else None
        locations = nexo.get("locations") if isinstance(nexo, dict) else None
        source = locations.get("mcp_server", "") if isinstance(locations, dict) else ""
        if not isinstance(source, str) or not source.strip():
            continue
        candidate = _normalize_candidate(source)
        if _is_repo_root(candidate):
            return candidate
    return None


def _resolve_repo_root(explicit_root: str = "") -> Path:
    if explicit_root.strip():
        candidate = _normalize_candidate(explicit_root)
        if _is_repo_root(candidate):
            return candidate

    env_code = os.environ.get("NEXO_CODE", "").strip()
    if env_code:
        candidate = _normalize_candidate(env_code)
        if _is_repo_root(candidate):
            return candidate

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _is_repo_root(candidate):
            return candidate

    atlas_repo = _resolve_repo_root_from_atlas()
    if atlas_repo is not None:
        return atlas_repo

    script_root = Path(__file__).resolve().parents[3]
    if _is_repo_root(script_root):
        return script_root
    return script_root


def _parse_bool(raw: str, default: bool) -> bool:
    text = (raw or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"[core-fix-cycle] invalid boolean: {raw}")


def _normalize_areas(raw: str) -> list[str]:
    text = (raw or "protocol,plane").replace("+", ",")
    parts = [item.strip().lower() for item in text.split(",") if item.strip()]
    if not parts:
        return ["protocol", "plane"]
    unknown = [item for item in parts if item not in AREA_MAP]
    if unknown:
        raise SystemExit(
            f"[core-fix-cycle] unknown area(s): {', '.join(unknown)}. Expected one of: {', '.join(sorted(AREA_MAP))}"
        )
    seen = set()
    ordered = []
    for item in parts:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _env(root: Path, nexo_home: str) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(root / "src")
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{src_path}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else src_path
    )
    env["NEXO_CODE"] = src_path
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if nexo_home.strip():
        env["NEXO_HOME"] = str(Path(nexo_home).expanduser())
    return env


def _command_succeeds(cmd: list[str], *, env: dict[str, str], root: Path) -> bool:
    result = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _resolve_python(root: Path, env: dict[str, str]) -> str:
    candidates = []
    override = os.environ.get("NEXO_RELEASE_PYTHON", "").strip()
    if override:
        candidates.append(override)

    for name in ("python3", "python"):
        path = shutil.which(name)
        if path:
            candidates.append(path)

    candidates.append(sys.executable)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if _command_succeeds([candidate, "-m", "pytest", "--version"], env=env, root=root):
            return candidate

    raise SystemExit(
        "[core-fix-cycle] no Python interpreter with pytest available. "
        "Set NEXO_RELEASE_PYTHON or install pytest in the active runtime."
    )


def _run(cmd: list[str], *, env: dict[str, str], root: Path) -> None:
    print(f"[core-fix-cycle] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=root, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> int:
    areas = _normalize_areas(sys.argv[1] if len(sys.argv) > 1 else "protocol,plane")
    run_tests = _parse_bool(sys.argv[2] if len(sys.argv) > 2 else "true", True)
    repo_root = _resolve_repo_root(sys.argv[3] if len(sys.argv) > 3 else "")
    nexo_home = sys.argv[4] if len(sys.argv) > 4 else ""
    env = _env(repo_root, nexo_home)

    files: list[str] = []
    tests: list[str] = []
    for area in areas:
        for path in AREA_MAP[area]["files"]:
            if path not in files:
                files.append(path)
        for path in AREA_MAP[area]["tests"]:
            if path not in tests:
                tests.append(path)

    print(f"[core-fix-cycle] repo_root={repo_root}")
    print(f"[core-fix-cycle] areas={','.join(areas)}")
    print("[core-fix-cycle] file-map:")
    for path in files:
        label = "OK" if (repo_root / path).exists() else "missing"
        print(f"[core-fix-cycle]   - {path} [{label}]")

    if not run_tests:
        print("[core-fix-cycle] tests skipped")
        print("[core-fix-cycle] OK")
        return 0

    existing_tests = [path for path in tests if (repo_root / path).is_file()]
    if not existing_tests:
        print("[core-fix-cycle] no tests found for selected areas")
        print("[core-fix-cycle] OK")
        return 0

    python_bin = _resolve_python(repo_root, env)
    print(f"[core-fix-cycle] python={python_bin}")
    _run([python_bin, "-m", "pytest", "-q", *existing_tests], env=env, root=repo_root)
    print("[core-fix-cycle] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
