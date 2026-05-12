from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import time
from pathlib import Path
from typing import Any


def now() -> float:
    return time.time()


def norm_path(path: str | os.PathLike[str]) -> str:
    return str(Path(path).expanduser()).rstrip(os.sep)


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def system_label() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "macos"
    if name == "windows":
        return "windows"
    return name or "unknown"


def redact_path(path: str) -> str:
    home = str(Path.home())
    text = str(path)
    if home and text.startswith(home):
        return "~" + text[len(home):]
    if re.match(r"^[A-Za-z]:\\Users\\[^\\]+", text):
        return re.sub(r"^([A-Za-z]:\\Users\\)[^\\]+", r"\1…", text)
    return text


def quick_fingerprint(path: Path, stat_result: os.stat_result | None = None) -> str:
    st = stat_result or path.stat()
    return f"{int(st.st_size)}:{int(st.st_mtime_ns)}"


def content_hash(path: Path, max_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        remaining = max_bytes
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ@.-]{2,}", text.lower())
