from __future__ import annotations

import os
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

EMAIL_RUNTIME_DB_NAMES = {
    "email.db",
    "email-tracker.db",
    "emails.db",
    "monitor.db",
    "nexo-email.db",
}

EMAIL_ATTACHMENT_SUFFIXES = {
    ".csv",
    ".docx",
    ".eml",
    ".emlx",
    ".html",
    ".md",
    ".pdf",
    ".pptx",
    ".txt",
    ".xlsx",
}

EMAIL_EXTRACTABLE_SUFFIXES = {".eml", ".emlx", ".msg"}
OUTLOOK_MAC_INVENTORY_SUFFIXES = {
    ".olk15message",
    ".olk15msgsource",
    ".olk15msgattach",
    ".olk15event",
    ".olk15contact",
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

PRODUCT_ARTIFACT_PARTS = {
    "brain-bundle",
    "nexo desktop qa backups",
    "nexo desktop qa.app",
    "nexo desktop.app",
    "nexo desktop beta.app",
    "nexo desktop support.app",
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
}

SYSTEM_PATH_MARKERS = {
    "/bin",
    "/cores",
    "/dev",
    "/library/caches",
    "/library/logs",
    "/private/tmp",
    "/private/var/db",
    "/private/var/folders",
    "/private/var/log",
    "/private/var/vm",
    "/proc",
    "/run",
    "/sbin",
    "/sys",
    "/system",
    "/tmp",
    "/usr",
    "/var/folders",
    "/var/tmp",
}

TEMP_SYSTEM_PATH_MARKERS = {
    "/private/var/folders",
    "/tmp",
    "/var/folders",
    "/var/tmp",
}

HOME_PRIVATE_PROFILE_MARKERS = {
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


def _normalized(path: str) -> str:
    return str(Path(path)).replace("\\", "/").lower()


def _parts(path: str) -> set[str]:
    return {part for part in _normalized(path).replace(":", "/").split("/") if part}


def _contains_path_marker(lowered: str, markers: set[str]) -> bool:
    return any(marker in lowered for marker in markers)


def _is_under_marker(lowered: str, marker: str) -> bool:
    marker = marker.strip("/").lower()
    if not marker:
        return False
    return lowered.endswith("/" + marker) or f"/{marker}/" in lowered


def _has_absolute_marker(lowered: str, marker: str) -> bool:
    marker = "/" + marker.strip("/").lower()
    return lowered == marker or lowered.endswith(marker) or f"{marker}/" in lowered


def _is_system_path(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if parts & SYSTEM_PARTS:
        return True
    matched_markers = {marker for marker in SYSTEM_PATH_MARKERS if _has_absolute_marker(lowered, marker)}
    if not matched_markers:
        return False
    if (
        matched_markers <= TEMP_SYSTEM_PATH_MARKERS
        and os.environ.get("NEXO_LOCAL_INDEX_ALLOW_BLOCKED_ROOTS", "").strip().lower() in {"1", "true", "yes"}
        and "pytest-" in lowered
    ):
        return False
    return True


def _is_app_bundle_path(path: str) -> bool:
    lowered = _normalized(path)
    return lowered.endswith(".app") or ".app/" in lowered


def _is_inside_windows_mail_package(lowered: str) -> bool:
    return "/appdata/local/packages/microsoft.windowscommunicationsapps" in lowered


def _is_inside_outlook_mac_profile(lowered: str) -> bool:
    return "/library/group containers/ubf8t346g9.office/outlook" in lowered


def is_local_email_tree(path: str) -> bool:
    lowered = _normalized(path)
    if _is_inside_windows_mail_package(lowered) or _is_inside_outlook_mac_profile(lowered):
        return True
    return any(
        _is_under_marker(lowered, marker)
        for marker in (
            "library/mail",
            ".nexo/runtime/nexo-email",
            "documents/outlook files",
            "appdata/local/microsoft/outlook",
            "appdata/roaming/microsoft/outlook",
            "appdata/local/packages/microsoft.windowscommunicationsapps",
            ".thunderbird",
            ".mozilla-thunderbird",
        )
    )


def is_local_email_db(path: str) -> bool:
    p = Path(path)
    return is_local_email_tree(path) and p.name.lower() in EMAIL_RUNTIME_DB_NAMES


def is_allowed_local_email_file(path: str) -> bool:
    if not is_local_email_tree(path):
        return False
    p = Path(path)
    lowered = _normalized(path)
    suffix = p.suffix.lower()
    if is_sensitive_path(path):
        return False
    if _is_under_marker(lowered, ".nexo/runtime/nexo-email"):
        if is_local_email_db(path):
            return True
        if _is_under_marker(lowered, ".nexo/runtime/nexo-email/attachments"):
            return suffix in EMAIL_ATTACHMENT_SUFFIXES
        return suffix in {".eml", ".emlx"}
    if _is_under_marker(lowered, "library/mail"):
        return suffix in {".eml", ".emlx"}
    if any(
        _is_under_marker(lowered, marker)
        for marker in (
            "library/group containers/ubf8t346g9.office/outlook",
            "documents/outlook files",
            "appdata/local/microsoft/outlook",
            "appdata/roaming/microsoft/outlook",
            "appdata/local/packages/microsoft.windowscommunicationsapps",
        )
    ) or _is_inside_windows_mail_package(lowered) or _is_inside_outlook_mac_profile(lowered):
        return suffix in {".eml", ".msg", ".pst", ".ost"} | OUTLOOK_MAC_INVENTORY_SUFFIXES
    if _is_under_marker(lowered, ".thunderbird") or _is_under_marker(lowered, ".mozilla-thunderbird"):
        return suffix in {".eml", ".mbox", ""}
    return False


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


def _name_has_sensitive_marker(name: str, stem: str) -> bool:
    """Token match (not substring) so 'secret' does not flag 'secretaria'/'secreto'."""
    import re

    norm = re.sub(r"[^a-z0-9]+", "_", f"{name}_{stem}".lower()).strip("_")
    if not norm:
        return False
    tokens = set(norm.split("_"))
    padded = f"_{norm}_"
    for marker in SENSITIVE_NAME_MARKERS:
        if "_" in marker:
            if f"_{marker}_" in padded:
                return True
        elif marker in tokens:
            return True
    return False


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
    if _name_has_sensitive_marker(name, stem):
        return True
    return _contains_path_marker(lowered, SENSITIVE_PARTS)


def is_private_profile_path(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    global_parts = PRIVATE_PROFILE_PARTS - HOME_PRIVATE_PROFILE_MARKERS
    if parts & global_parts:
        return True
    if _contains_path_marker(lowered, global_parts):
        return True
    if (_is_home_hidden_path(path) or "/users/" in lowered) and _contains_path_marker(lowered, HOME_PRIVATE_PROFILE_MARKERS):
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

    if is_local_email_tree(path) and (Path(path).suffix == "" or is_allowed_local_email_file(path)):
        return 2, "normal", "local_email_path"
    if is_sensitive_path(path):
        return 1, "sensitive_inventory_only", "sensitive_path"
    if is_private_profile_path(path):
        return 0, "private_profile_blocked", "private_profile_path"
    if _is_system_path(path):
        return 0, "system_blocked", "system_path"
    if parts & PRODUCT_ARTIFACT_PARTS:
        return 1, "inventory_only", "product_artifact"
    if _is_app_bundle_path(path):
        return 1, "inventory_only", "app_bundle"
    if parts & NOISY_PARTS or _has_transient_project_part(path) or _has_hidden_dir_part(path):
        return 1, "inventory_only", "noisy_tree"
    return 2, "normal", "default"


def should_skip_tree(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if is_local_email_tree(path):
        return False
    if _is_system_path(path):
        return True
    if is_sensitive_path(path) or is_private_profile_path(path):
        return True
    return bool(
        parts & PRODUCT_ARTIFACT_PARTS
        or _is_app_bundle_path(path)
        or parts & NOISY_PARTS
        or _has_transient_project_part(path)
        or _has_hidden_dir_part(path)
    )


def should_skip_file(path: str) -> bool:
    lowered = _normalized(path)
    parts = _parts(path)
    if is_local_email_tree(path):
        return not is_allowed_local_email_file(path)
    if _is_system_path(path):
        return True
    if is_sensitive_path(path) or is_private_profile_path(path):
        return True
    return bool(
        parts & PRODUCT_ARTIFACT_PARTS
        or _is_app_bundle_path(path)
        or parts & NOISY_PARTS
        or _has_transient_project_part(path)
        or _has_hidden_dir_part(path)
    )


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
    if is_local_email_db(path):
        return True
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
        ".emlx",
        ".msg",
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
    }:
        return True
    return False
