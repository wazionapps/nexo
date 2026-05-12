from __future__ import annotations

from pathlib import Path

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".boto",
    ".pgpass",
    ".my.cnf",
    ".git-credentials",
    ".mcp_publisher_token",
    ".mcpregistry_github_token",
    ".mcpregistry_registry_token",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
    "authorized_keys",
    "cookies.sqlite",
    "login data",
    "keychain-2.db",
}

SENSITIVE_NAME_MARKERS = {
    "api_key",
    "apikey",
    "auth_token",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "oauth",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
}

SENSITIVE_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".kdbx",
}

SENSITIVE_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
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
    ".venv",
    "venv",
    "env",
    ".cache",
    "cache",
    "coverage",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".parcel-cache",
    ".bun",
    ".gradle",
    "$tmp",
    "target",
}

TRANSIENT_PARTS = {"tmp", "temp"}

PRIVATE_PROFILE_PARTS = {
    ".nexo",
    ".claude",
    ".codex",
    ".gemini",
    ".cursor",
    ".config",
    ".local",
    ".npm",
    ".yarn",
    ".pnpm-store",
    ".ollama",
    ".docker",
    ".vscode",
    ".idea",
    "appdata",
    "application data",
    "library/application support",
    "library/containers",
    "library/group containers",
    "library/keychains",
    "library/logs",
    "library/mail",
    "library/messages",
    "library/safari",
    "library/saved application state",
}

PROFILE_HIDDEN_FILE_NAMES = {
    ".aider.chat.history.md",
    ".aider.input.history",
    ".bash_history",
    ".bash_profile",
    ".bashrc",
    ".claude.json",
    ".codex.json",
    ".cursorignore",
    ".ds_store",
    ".gitconfig",
    ".gitignore_global",
    ".lesshst",
    ".python_history",
    ".sqlite_history",
    ".viminfo",
    ".wget-hsts",
    ".zprofile",
    ".zsh_history",
    ".zshrc",
}

ALLOWED_HIDDEN_FILE_NAMES = set()

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


def _normalized(path: str) -> str:
    return str(Path(path)).replace("\\", "/").lower()


def _parts(path: str) -> set[str]:
    return {part for part in _normalized(path).replace(":", "/").split("/") if part}


def _contains_path_marker(lowered: str, markers: set[str]) -> bool:
    return any(marker in lowered for marker in markers)


def _has_transient_project_part(path: str) -> bool:
    parts = list(_normalized(path).replace(":", "/").split("/"))
    for index, part in enumerate(parts):
        if part in TRANSIENT_PARTS and index >= 2:
            return True
    return False


def _has_hidden_dir_part(path: str) -> bool:
    parts = [part for part in _normalized(path).replace(":", "/").split("/") if part]
    return any(part.startswith(".") and part not in {".", ".."} for part in parts[:-1])


def _is_home_hidden_path(path: str) -> bool:
    try:
        p = Path(path).expanduser()
        home = Path.home().expanduser()
        rel = p.relative_to(home)
    except Exception:
        return False
    return bool(rel.parts) and rel.parts[0].startswith(".")


def is_sensitive_path(path: str) -> bool:
    p = Path(path)
    lowered = _normalized(path)
    name = p.name.lower()
    stem = p.stem.lower()
    parts = _parts(path)
    if name in SENSITIVE_FILE_NAMES:
        return True
    if name.startswith(".") and name not in ALLOWED_HIDDEN_FILE_NAMES:
        return True
    if name.startswith("~$"):
        return True
    if name.endswith((".tmp", ".swp", ".swo")):
        return True
    if p.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if parts & SENSITIVE_PARTS:
        return True
    if any(marker in name or marker in stem for marker in SENSITIVE_NAME_MARKERS):
        return True
    return _contains_path_marker(lowered, SENSITIVE_PARTS)


def is_private_profile_path(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if parts & PRIVATE_PROFILE_PARTS:
        return True
    if _contains_path_marker(lowered, PRIVATE_PROFILE_PARTS):
        return True
    name = Path(path).name.lower()
    if name in PROFILE_HIDDEN_FILE_NAMES:
        return True
    if _is_home_hidden_path(path):
        return True
    return False


def classify_path(path: str) -> tuple[int, str, str]:
    """Return (depth, privacy_class, reason)."""
    lowered = _normalized(path)
    parts = _parts(path)

    if is_sensitive_path(path):
        return 1, "sensitive_inventory_only", "sensitive_path"
    if is_private_profile_path(path):
        return 0, "private_profile_blocked", "private_profile_path"
    if any(item in lowered for item in SYSTEM_PARTS):
        return 0, "system_blocked", "system_path"
    if parts & NOISY_PARTS or _has_transient_project_part(path) or _has_hidden_dir_part(path):
        return 1, "inventory_only", "noisy_tree"
    return 2, "normal", "default"


def should_skip_tree(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if any(item in lowered for item in SYSTEM_PARTS):
        return True
    if is_sensitive_path(path) or is_private_profile_path(path):
        return True
    return bool(parts & NOISY_PARTS or _has_transient_project_part(path) or _has_hidden_dir_part(path))


def should_skip_file(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if any(item in lowered for item in SYSTEM_PARTS):
        return True
    if is_sensitive_path(path) or is_private_profile_path(path):
        return True
    return bool(parts & NOISY_PARTS or _has_transient_project_part(path) or _has_hidden_dir_part(path))


def is_queryable_path(path: str, privacy_class: str = "") -> bool:
    if privacy_class and privacy_class != "normal":
        return False
    return not should_skip_file(path)


def should_extract(path: str, depth: int) -> bool:
    if depth < 2:
        return False
    if should_skip_file(path):
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
