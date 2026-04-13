#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _is_repo_root(candidate: Path) -> bool:
    return (
        (candidate / "package.json").is_file()
        and (candidate / "release-contracts").is_dir()
        and (candidate / "scripts" / "verify_release_readiness.py").is_file()
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


def _resolve_repo_root() -> Path:
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

    script_root = _normalize_candidate(Path(__file__).resolve().parents[3])
    if _is_repo_root(script_root):
        return script_root
    return script_root


ROOT = _resolve_repo_root()


def _package_version() -> str:
    payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = str(payload.get("version", "") or "").strip()
    if not version:
        raise SystemExit("[release-final-audit] package.json missing version")
    return version


def _parse_bool(raw: str, default: bool) -> bool:
    text = (raw or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"[release-final-audit] invalid boolean: {raw}")


def _resolve_contract(version: str, raw: str) -> Path | None:
    choice = (raw or "auto").strip()
    if not choice or choice.lower() == "auto":
        candidate = ROOT / "release-contracts" / f"v{version}.json"
        if not candidate.is_file():
            raise SystemExit(
                f"[release-final-audit] missing auto contract for v{version}: {candidate}"
            )
        return candidate
    if choice.lower() in {"none", "skip", "off"}:
        return None

    candidate = Path(choice).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT / candidate).resolve()
    if not candidate.is_file():
        raise SystemExit(f"[release-final-audit] contract not found: {candidate}")
    return candidate


def _resolve_smoke_runner(version: str) -> Path | None:
    parts = version.split(".")
    if len(parts) < 2:
        return None
    candidate = ROOT / "scripts" / f"run_v{parts[0]}_{parts[1]}_smoke.py"
    return candidate if candidate.is_file() else None


def _env(nexo_home: str) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "").strip()
    src_path = str(ROOT / "src")
    env["PYTHONPATH"] = (
        f"{src_path}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else src_path
    )
    env["NEXO_CODE"] = src_path
    if nexo_home.strip():
        env["NEXO_HOME"] = str(Path(nexo_home).expanduser())
    return env


def _command_succeeds(cmd: list[str], *, env: dict[str, str]) -> bool:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _resolve_python(env: dict[str, str]) -> str:
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
        if _command_succeeds([candidate, "-m", "pytest", "--version"], env=env):
            return candidate

    raise SystemExit(
        "[release-final-audit] no Python interpreter with pytest available. "
        "Set NEXO_RELEASE_PYTHON or install pytest in the active runtime."
    )


def _run(cmd: list[str], *, env: dict[str, str]) -> None:
    print(f"[release-final-audit] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _looks_like_nexo_home(raw: str) -> bool:
    if not raw.strip():
        return False
    candidate = Path(raw).expanduser()
    return (candidate / "skills-runtime").is_dir() or (candidate / "operations" / "tool-logs").is_dir()


def _parse_optional_args(argv: list[str]) -> tuple[str, str, str, str]:
    website_root = argv[5] if len(argv) > 5 else ""
    nexo_home = argv[6] if len(argv) > 6 else ""
    final_closeout = argv[7] if len(argv) > 7 else ""
    protocol_task_id = argv[8] if len(argv) > 8 else ""
    if website_root and not nexo_home and _looks_like_nexo_home(website_root):
        nexo_home, website_root = website_root, ""
    return website_root, nexo_home, final_closeout, protocol_task_id


def main() -> int:
    contract_arg = sys.argv[1] if len(sys.argv) > 1 else "auto"
    require_contract_complete = _parse_bool(sys.argv[2] if len(sys.argv) > 2 else "true", True)
    include_smoke = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else "true", True)
    ci = _parse_bool(sys.argv[4] if len(sys.argv) > 4 else "false", False)
    website_root, nexo_home, final_closeout_raw, protocol_task_id = _parse_optional_args(sys.argv)
    final_closeout = _parse_bool(final_closeout_raw, False)

    version = _package_version()
    contract_path = _resolve_contract(version, contract_arg)
    env = _env(nexo_home)
    python_bin = _resolve_python(env)

    print(f"[release-final-audit] version={version}")
    print(f"[release-final-audit] contract={contract_path or 'none'}")
    print(f"[release-final-audit] include_smoke={include_smoke} ci={ci} final_closeout={final_closeout}")
    print(f"[release-final-audit] python={python_bin}")
    if final_closeout and protocol_task_id.strip():
        print(f"[release-final-audit] protocol_task_id={protocol_task_id.strip()}")

    if include_smoke:
        smoke_runner = _resolve_smoke_runner(version)
        if smoke_runner is None:
            print(f"[release-final-audit] smoke runner skipped for v{version} (not found)")
        else:
            smoke_output = ROOT / "release-contracts" / "smoke" / f"v{version}.json"
            _run([python_bin, str(smoke_runner), "--output", str(smoke_output)], env=env)

    readiness_cmd = [python_bin, "scripts/verify_release_readiness.py"]
    if ci:
        readiness_cmd.append("--ci")
    if website_root.strip():
        readiness_cmd.extend(["--website-root", website_root.strip()])
    if nexo_home.strip():
        readiness_cmd.extend(["--nexo-home", nexo_home.strip()])
    if contract_path is not None:
        readiness_cmd.extend(["--contract", str(contract_path)])
        if require_contract_complete:
            readiness_cmd.append("--require-contract-complete")
    elif require_contract_complete:
        print("[release-final-audit] require_contract_complete ignored because contract=none")
    if final_closeout:
        readiness_cmd.append("--final-closeout")
        if protocol_task_id.strip():
            readiness_cmd.extend(["--protocol-task-id", protocol_task_id.strip()])

    _run(readiness_cmd, env=env)
    print("[release-final-audit] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
