"""NEXO Script Registry — discovery, metadata, validation for personal scripts.

Scripts live in NEXO_HOME/scripts/. Core scripts (from manifest) are filtered by default.
Personal scripts use CLI as stable interface, never direct DB access.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parent)))

# Internal artifacts to always ignore
_IGNORED_FILES = {
    ".watchdog-hashes",
    ".watchdog-fails",
    ".watchdog-nexo-repair.lock",
    "nexo-cron-wrapper.sh",
}
_IGNORED_DIRS = {"deep-sleep", "__pycache__"}

# Forbidden patterns — direct DB access from personal scripts
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bsqlite3\b"),
    re.compile(r"\bnexo\.db\b"),
    re.compile(r"\bcognitive\.db\b"),
    re.compile(r"/data/nexo\.db"),
    re.compile(r"/data/cognitive\.db"),
    re.compile(r"\bimport\s+db\b"),
    re.compile(r"\bfrom\s+db\s+import\b"),
    re.compile(r"\bimport\s+cognitive\b"),
    re.compile(r"\bfrom\s+cognitive\s+import\b"),
]

METADATA_KEYS = {"name", "description", "runtime", "timeout", "requires", "tools", "hidden"}


def get_nexo_home() -> Path:
    return NEXO_HOME


def get_scripts_dir() -> Path:
    return NEXO_HOME / "scripts"


def load_core_script_names() -> set[str]:
    """Load script names from crons/manifest.json (these are core, not personal)."""
    names: set[str] = set()
    for manifest_path in [NEXO_CODE / "crons" / "manifest.json", NEXO_HOME / "crons" / "manifest.json"]:
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                for cron in data.get("crons", []):
                    script = cron.get("script", "")
                    # script is like "scripts/nexo-immune.py" — extract filename
                    names.add(Path(script).name)
                break
            except Exception:
                continue
    return names


def parse_inline_metadata(path: Path) -> dict:
    """Parse # nexo: key=value metadata from first 25 lines."""
    meta: dict[str, str] = {}
    try:
        lines = path.read_text(errors="ignore").splitlines()[:25]
    except Exception:
        return meta

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("# nexo:"):
            continue
        payload = stripped[len("# nexo:"):].strip()
        if "=" not in payload:
            continue
        key, value = payload.split("=", 1)
        k = key.strip()
        if k in METADATA_KEYS:
            meta[k] = value.strip()
    return meta


def _detect_shebang(path: Path) -> str | None:
    """Read first line for shebang."""
    try:
        first = path.read_text(errors="ignore").split("\n", 1)[0]
        if first.startswith("#!"):
            return first
    except Exception:
        pass
    return None


def classify_runtime(path: Path, metadata: dict) -> str:
    """Detect script runtime: python, shell, or unknown."""
    # 1. Metadata
    rt = metadata.get("runtime", "").lower()
    if rt in ("python", "shell"):
        return rt

    # 2. Shebang
    shebang = _detect_shebang(path)
    if shebang:
        if "python" in shebang:
            return "python"
        if "bash" in shebang or "/sh" in shebang:
            return "shell"

    # 3. Extension
    ext = path.suffix.lower()
    if ext == ".py":
        return "python"
    if ext == ".sh":
        return "shell"

    return "unknown"


def _is_ignored(path: Path) -> bool:
    """Check if file should be ignored entirely."""
    if path.name in _IGNORED_FILES:
        return True
    if path.name.startswith("."):
        return True
    for parent in path.relative_to(get_scripts_dir()).parents:
        if parent.name in _IGNORED_DIRS:
            return True
    return False


def list_scripts(include_core: bool = False) -> list[dict]:
    """List scripts in NEXO_HOME/scripts/.

    By default only personal scripts. With include_core=True, also shows core/cron scripts.
    """
    scripts_dir = get_scripts_dir()
    if not scripts_dir.is_dir():
        return []

    core_names = load_core_script_names()
    results = []

    for f in sorted(scripts_dir.iterdir()):
        if not f.is_file():
            continue
        if _is_ignored(f):
            continue

        is_core = f.name in core_names
        if is_core and not include_core:
            continue

        meta = parse_inline_metadata(f)
        runtime = classify_runtime(f, meta)
        name = meta.get("name", f.stem)
        hidden = meta.get("hidden", "").lower() in ("true", "1", "yes")

        if hidden and not include_core:
            continue

        results.append({
            "name": name,
            "runtime": runtime,
            "description": meta.get("description", ""),
            "path": str(f),
            "core": is_core,
            "metadata": meta,
        })

    return results


def resolve_script(name: str) -> dict | None:
    """Find a script by name (metadata name or filename stem)."""
    scripts_dir = get_scripts_dir()
    if not scripts_dir.is_dir():
        return None

    for f in scripts_dir.iterdir():
        if not f.is_file() or _is_ignored(f):
            continue
        meta = parse_inline_metadata(f)
        script_name = meta.get("name", f.stem)
        if script_name == name or f.stem == name:
            runtime = classify_runtime(f, meta)
            return {
                "name": script_name,
                "runtime": runtime,
                "description": meta.get("description", ""),
                "path": str(f),
                "core": f.name in load_core_script_names(),
                "metadata": meta,
            }
    return None


def doctor_script(path_or_name: str) -> dict:
    """Validate a single script. Returns dict with pass/warn/fail items."""
    # Resolve
    p = Path(path_or_name)
    if not p.is_file():
        info = resolve_script(path_or_name)
        if not info:
            return {"status": "fail", "items": [{"level": "fail", "msg": f"Script not found: {path_or_name}"}]}
        p = Path(info["path"])

    items: list[dict] = []
    meta = parse_inline_metadata(p)
    runtime = classify_runtime(p, meta)
    core_names = load_core_script_names()
    is_core = p.name in core_names

    # File exists
    if p.is_file():
        items.append({"level": "pass", "msg": f"File exists: {p.name}"})
    else:
        items.append({"level": "fail", "msg": f"File missing: {p.name}"})
        return {"status": "fail", "items": items}

    # Name collision with core
    name = meta.get("name", p.stem)
    if not is_core:
        for core in core_names:
            core_stem = Path(core).stem
            if name == core_stem:
                items.append({"level": "fail", "msg": f"Name collision with core script: {core}"})

    # Runtime recognized
    if runtime == "unknown":
        items.append({"level": "warn", "msg": "Runtime not recognized (no shebang, no extension match)"})
    else:
        items.append({"level": "pass", "msg": f"Runtime: {runtime}"})

    # Shebang for shell scripts
    if runtime == "shell":
        shebang = _detect_shebang(p)
        if not shebang:
            items.append({"level": "warn", "msg": "Shell script without shebang"})
        else:
            items.append({"level": "pass", "msg": f"Shebang: {shebang}"})

    # Executable bit for shell scripts
    if runtime == "shell":
        mode = p.stat().st_mode
        if not (mode & stat.S_IXUSR):
            items.append({"level": "warn", "msg": "Shell script missing executable bit"})
        else:
            items.append({"level": "pass", "msg": "Executable bit set"})

    # Timeout parse
    timeout_str = meta.get("timeout", "")
    if timeout_str:
        try:
            int(timeout_str)
            items.append({"level": "pass", "msg": f"Timeout: {timeout_str}s"})
        except ValueError:
            items.append({"level": "fail", "msg": f"Invalid timeout value: {timeout_str}"})

    # Requires check
    requires = meta.get("requires", "")
    if requires:
        for cmd in requires.split(","):
            cmd = cmd.strip()
            if cmd and not shutil.which(cmd):
                items.append({"level": "fail", "msg": f"Required command not in PATH: {cmd}"})
            elif cmd:
                items.append({"level": "pass", "msg": f"Required command found: {cmd}"})

    # Forbidden patterns (only for personal scripts)
    if not is_core:
        try:
            content = p.read_text(errors="ignore")
            for pat in _FORBIDDEN_PATTERNS:
                match = pat.search(content)
                if match:
                    items.append({"level": "fail", "msg": f"Forbidden DB pattern found: {match.group()}"})
        except Exception:
            pass

    # Determine overall status
    levels = [i["level"] for i in items]
    if "fail" in levels:
        status = "fail"
    elif "warn" in levels:
        status = "warn"
    else:
        status = "pass"

    return {"status": status, "items": items, "name": name, "path": str(p)}


def doctor_all_scripts() -> list[dict]:
    """Run doctor on all personal scripts."""
    results = []
    for script in list_scripts(include_core=False):
        result = doctor_script(script["path"])
        results.append(result)
    return results
