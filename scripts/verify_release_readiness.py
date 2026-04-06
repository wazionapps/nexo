#!/usr/bin/env python3
"""Repository-scoped release readiness checks for NEXO.

This is the public counterpart of the local release validator used in day-to-day
operations. It keeps release discipline inside the repo instead of relying only
on operator memory or external personal scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_WEBSITE_ROOT = ROOT.parent / "nexo-gh-pages"
DEFAULT_NEXO_HOME_CANDIDATES = (
    Path.home() / ".nexo",
    Path.home() / "claude",
)


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"[release-readiness] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _package_version() -> str:
    payload = json.loads(PACKAGE_JSON.read_text())
    version = str(payload.get("version", "") or "").strip()
    if not version:
        raise SystemExit("[release-readiness] package.json missing version")
    return version


def _resolve_nexo_home(explicit_home: str = "") -> Path:
    candidates = []
    if explicit_home.strip():
        candidates.append(Path(explicit_home).expanduser())
    env_home = os.environ.get("NEXO_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser())
    candidates.extend(DEFAULT_NEXO_HOME_CANDIDATES)

    best = candidates[0] if candidates else Path.home() / ".nexo"
    best_score = -1
    for candidate in candidates:
        score = 0
        if (candidate / "data" / "nexo.db").is_file():
            score += 2
        if (candidate / "operations" / "tool-logs").is_dir():
            score += 1
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _check_changelog(version: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## \[([^\]]+)\]", text, flags=re.MULTILINE)
    if not match:
        raise SystemExit("[release-readiness] CHANGELOG.md missing release headings")
    top_version = match.group(1).strip()
    if top_version != version:
        raise SystemExit(
            f"[release-readiness] top changelog version {top_version} != package.json {version}"
        )
    print(f"[release-readiness] changelog OK ({version})")


def _check_website(version: str, website_root: Path) -> None:
    if not website_root.is_dir():
        print(f"[release-readiness] website skipped (missing {website_root})")
        return
    changelog_page = website_root / "changelog" / "index.html"
    home_page = website_root / "index.html"
    missing = []
    if changelog_page.is_file() and f"v{version}" not in changelog_page.read_text(encoding="utf-8"):
        missing.append(f"{changelog_page} missing v{version}")
    if home_page.is_file() and version not in home_page.read_text(encoding="utf-8"):
        missing.append(f"{home_page} missing {version}")
    if missing:
        raise SystemExit("[release-readiness] website drift:\n- " + "\n- ".join(missing))
    print(f"[release-readiness] website OK ({website_root})")


def _run_runtime_doctor(nexo_home: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["NEXO_CODE"] = str(ROOT)
    env["NEXO_HOME"] = str(nexo_home)
    print(f"[release-readiness] runtime doctor home: {nexo_home}")
    _run(
        [
            sys.executable,
            "-c",
            (
                "from doctor.orchestrator import run_doctor; "
                "report = run_doctor(tier='runtime', fix=False); "
                "import sys; "
                "bad = [c for c in report.checks if c.status in ('degraded','critical')]; "
                "print(f'runtime doctor: {report.overall_status} ({len(bad)} issues)'); "
                "sys.exit(1 if any(c.status == 'critical' for c in bad) else 0)"
            ),
        ],
        env=env,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify NEXO release readiness.")
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Run repo-only checks suitable for CI (skip local runtime doctor).",
    )
    parser.add_argument(
        "--website-root",
        default=str(DEFAULT_WEBSITE_ROOT),
        help="Path to the website worktree. Skipped if missing.",
    )
    parser.add_argument(
        "--nexo-home",
        default="",
        help="Override the runtime NEXO_HOME used by the local doctor check.",
    )
    args = parser.parse_args()

    version = _package_version()
    nexo_home = _resolve_nexo_home(args.nexo_home)
    _check_changelog(version)
    _run([sys.executable, "scripts/sync_release_artifacts.py", "--check"])
    _run([sys.executable, "scripts/verify_client_parity.py"])
    _check_website(version, Path(args.website_root).expanduser())
    if not args.ci:
        _run_runtime_doctor(nexo_home)
    print("[release-readiness] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
