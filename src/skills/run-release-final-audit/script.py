#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_repo_root() -> Path:
    env_code = os.environ.get("NEXO_CODE", "").strip()
    if env_code:
        candidate = Path(env_code).expanduser().resolve()
        if candidate.is_file():
            candidate = candidate.parent
        if (candidate / "cli.py").is_file():
            return candidate.parent if candidate.name == "src" else candidate
    return Path(__file__).resolve().parents[3]


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
    env.setdefault("NEXO_CODE", str(ROOT / "src"))
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


def main() -> int:
    contract_arg = sys.argv[1] if len(sys.argv) > 1 else "auto"
    require_contract_complete = _parse_bool(sys.argv[2] if len(sys.argv) > 2 else "true", True)
    include_smoke = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else "true", True)
    ci = _parse_bool(sys.argv[4] if len(sys.argv) > 4 else "false", False)
    website_root = sys.argv[5] if len(sys.argv) > 5 else ""
    nexo_home = sys.argv[6] if len(sys.argv) > 6 else ""

    version = _package_version()
    contract_path = _resolve_contract(version, contract_arg)
    env = _env(nexo_home)
    python_bin = _resolve_python(env)

    print(f"[release-final-audit] version={version}")
    print(f"[release-final-audit] contract={contract_path or 'none'}")
    print(f"[release-final-audit] include_smoke={include_smoke} ci={ci}")
    print(f"[release-final-audit] python={python_bin}")

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

    _run(readiness_cmd, env=env)
    print("[release-final-audit] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
