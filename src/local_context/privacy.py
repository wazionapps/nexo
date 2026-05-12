from __future__ import annotations

from pathlib import Path

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "cookies.sqlite",
    "login data",
    "keychain-2.db",
}

SENSITIVE_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    "password",
    "passwords",
    "1password",
    "lastpass",
    "bitwarden",
    "cookies",
    "browser profile",
}

NOISY_PARTS = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".git",
    ".cache",
    "cache",
    "coverage",
    "__pycache__",
}

SYSTEM_PARTS = {
    "system volume information",
    "$recycle.bin",
    "windows",
    "program files",
    "program files (x86)",
    "library/caches",
    "system/library",
    "/proc",
    "/sys",
}


def classify_path(path: str) -> tuple[int, str, str]:
    """Return (depth, privacy_class, reason)."""
    p = Path(path)
    lowered = str(p).replace("\\", "/").lower()
    name = p.name.lower()
    parts = {part.lower() for part in p.parts}

    if name in SENSITIVE_FILE_NAMES or parts & SENSITIVE_PARTS:
        return 1, "sensitive_inventory_only", "sensitive_path"
    if any(item in lowered for item in SYSTEM_PARTS):
        return 0, "system_blocked", "system_path"
    if parts & NOISY_PARTS:
        return 1, "inventory_only", "noisy_tree"
    return 2, "normal", "default"


def should_extract(path: str, depth: int) -> bool:
    if depth < 2:
        return False
    suffix = Path(path).suffix.lower()
    if suffix in {
        ".txt",
        ".md",
        ".markdown",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".php",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".css",
        ".csv",
        ".tsv",
        ".eml",
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
    }:
        return True
    return False
