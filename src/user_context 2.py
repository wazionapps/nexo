"""User context singleton — loads operator/user identity from calibration.json."""
from __future__ import annotations
import json
import os
from pathlib import Path

_ctx = None

class UserContext:
    """Cached user/operator identity loaded once from calibration.json."""

    def __init__(self):
        nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
        cal_path = nexo_home / "brain" / "calibration.json"
        ver_path = nexo_home / "version.json"

        self.assistant_name = "NEXO"
        self.user_name = ""
        self.user_language = "en"

        # calibration.json has operator_name + user info
        if cal_path.exists():
            try:
                cal = json.loads(cal_path.read_text())
                self.assistant_name = cal.get("operator_name", "") or \
                    cal.get("user", {}).get("assistant_name", "") or "NEXO"
                self.user_name = cal.get("user", {}).get("name", "")
                self.user_language = cal.get("user", {}).get("language", "en")
            except Exception:
                pass

        # Fallback: version.json also has operator_name
        if self.assistant_name == "NEXO" and ver_path.exists():
            try:
                ver = json.loads(ver_path.read_text())
                self.assistant_name = ver.get("operator_name", "") or "NEXO"
            except Exception:
                pass


def get_context() -> UserContext:
    """Get or create the singleton UserContext."""
    global _ctx
    if _ctx is None:
        _ctx = UserContext()
    return _ctx
