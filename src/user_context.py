"""User context singleton — loads operator/user identity from calibration.json."""
from __future__ import annotations
import json
import os
from pathlib import Path

_ctx = None
DEFAULT_ASSISTANT_NAME = "Nova"

class UserContext:
    """Cached user/operator identity loaded once from calibration.json."""

    def __init__(self):
        nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        try:
            from paths import brain_dir

            cal_path = brain_dir() / "calibration.json"
        except Exception:
            cal_path = nexo_home / "brain" / "calibration.json"
        ver_path = nexo_home / "version.json"

        self.assistant_name = DEFAULT_ASSISTANT_NAME
        self.user_name = ""
        self.user_language = "en"

        # calibration.json has operator_name + user info.
        # v5.4.0+: tolerate both nested ({user:{name,language}}) and legacy flat
        # ({user_name, language}) shapes. Nested wins when both exist.
        if cal_path.exists():
            try:
                cal = json.loads(cal_path.read_text())
                user_block = cal.get("user") if isinstance(cal.get("user"), dict) else {}

                self.assistant_name = (
                    user_block.get("assistant_name", "")
                    or cal.get("operator_name", "")
                    or cal.get("assistant_name", "")
                    or DEFAULT_ASSISTANT_NAME
                )
                self.user_name = (
                    user_block.get("name", "")
                    or cal.get("user_name", "")
                    or cal.get("name", "")
                    or ""
                )
                self.user_language = (
                    user_block.get("language", "")
                    or cal.get("language", "")
                    or cal.get("lang", "")
                    or "en"
                )
            except Exception:
                pass

        # Fallback: version.json also has operator_name
        if self.assistant_name == DEFAULT_ASSISTANT_NAME and ver_path.exists():
            try:
                ver = json.loads(ver_path.read_text())
                self.assistant_name = ver.get("operator_name", "") or DEFAULT_ASSISTANT_NAME
            except Exception:
                pass


def get_context() -> UserContext:
    """Get or create the singleton UserContext."""
    global _ctx
    if _ctx is None:
        _ctx = UserContext()
    return _ctx
