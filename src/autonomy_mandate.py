"""Runtime state + enforcement for operator autonomy mandates.

When the operator states `autonomía total`, `sin esperas`, `todo ya`, or an
equivalent marker, the agent must stop deferring in-scope work to the future
via `nexo_followup_create`. This module captures that mandate as a small
JSON state file under `$NEXO_HOME/runtime/data/autonomy_mandate.json`, and
exposes helpers the CRUD layer uses before creating a followup.

Design notes:

* State is session-scoped, not global — the mandate decays when the session
  ends or after `DEFAULT_TTL_SECONDS` whichever comes first.
* Detection is intentionally simple (substring match on a marker list). The
  S4 incident that motivated this guardrail only needed a coarse trigger.
* The block is surgical: a followup is rejected only when the call pattern
  matches the procrastination shape the operator flagged (owner=user or
  date within 7 days). Long-horizon commitments (>7 days) and shared/agent
  followups pass through, because those are not what the operator was
  pushing back on.
* Three explicit exceptions stay allowed even under the mandate:
  (a) a download >1GB, (b) a credential the operator must physically enter,
  (c) a presence-dependent session with María or Nora. These are declared
  by keywords inside `description` or the explicit `exception` kwarg.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
STATE_PATH = NEXO_HOME / "runtime" / "data" / "autonomy_mandate.json"

# Marker list per NF-DS-45569A27. Case-insensitive substring match.
MARKERS = (
    "autonomía total",
    "autonomia total",
    "sin esperas",
    "todo ya",
    "no esperes",
    "no te escondas",
    "llevo 3 veces",
    "lo quiero ya",
)

# Default mandate TTL. Longer than a normal working session but short enough
# that a stale state does not block followups weeks later.
DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6h

# Exception keywords — when any appear in the description (or the explicit
# `exception` parameter), the followup is allowed even under an active
# mandate.
EXCEPTION_KEYWORDS = (
    ">1gb", ">1 gb", "> 1gb", "> 1 gb",
    "large download", "descarga grande",
    "credential", "credencial", "operator must enter",
    "el operador introduce", "api key rotation",
    "maría", "maria",
    "nora",
    "sesión presencial", "sesion presencial", "in-person",
)

PROCRASTINATION_WINDOW_DAYS = 7


@dataclass
class MandateState:
    active: bool
    session_id: str
    set_at: float
    expires_at: float
    marker: str
    source: str

    def remaining_seconds(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self.expires_at - now)

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "session_id": self.session_id,
            "set_at": self.set_at,
            "expires_at": self.expires_at,
            "marker": self.marker,
            "source": self.source,
        }


def _detect_marker(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    for marker in MARKERS:
        if marker in lowered:
            return marker
    return None


def _ensure_dir() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_state() -> Optional[MandateState]:
    """Return the current mandate state or None if absent/expired."""
    try:
        raw = json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    try:
        st = MandateState(
            active=bool(raw.get("active")),
            session_id=str(raw.get("session_id", "")),
            set_at=float(raw.get("set_at", 0.0)),
            expires_at=float(raw.get("expires_at", 0.0)),
            marker=str(raw.get("marker", "")),
            source=str(raw.get("source", "")),
        )
    except (TypeError, ValueError):
        return None
    if not st.active or st.expires_at <= time.time():
        return None
    return st


def set_mandate(
    session_id: str,
    marker: str,
    source: str = "manual",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> MandateState:
    _ensure_dir()
    now = time.time()
    st = MandateState(
        active=True,
        session_id=str(session_id or "").strip(),
        set_at=now,
        expires_at=now + max(60, int(ttl_seconds)),
        marker=marker,
        source=source,
    )
    STATE_PATH.write_text(json.dumps(st.to_dict(), ensure_ascii=False, indent=2))
    return st


def clear_mandate() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def maybe_ingest_from_text(
    text: str,
    session_id: str,
    source: str = "auto",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Optional[MandateState]:
    """Scan free-form text for a mandate marker and persist if found.

    Used by heartbeat / hook paths that capture operator messages so the
    mandate can be set transparently, without a separate explicit tool.
    Returns the new state when a marker was detected, otherwise None.
    """
    marker = _detect_marker(text or "")
    if not marker:
        return None
    return set_mandate(
        session_id=session_id,
        marker=marker,
        source=source,
        ttl_seconds=ttl_seconds,
    )


def _description_has_exception(description: str, exception: str) -> bool:
    haystack = f"{description or ''}\n{exception or ''}".lower()
    return any(kw in haystack for kw in EXCEPTION_KEYWORDS)


def _parse_target_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _days_until(target: date, today: Optional[date] = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    return (target - today).days


def check_followup_against_mandate(
    *,
    owner: str = "",
    date: str = "",
    description: str = "",
    exception: str = "",
    state: Optional[MandateState] = None,
) -> Optional[str]:
    """Return a human-readable error string when the followup should be
    rejected under an active mandate, otherwise None.

    The caller is responsible for turning the string into a tool-level
    error. `state` is injectable for tests; production callers leave it
    None so the current on-disk state is consulted.
    """
    st = state if state is not None else load_state()
    if st is None:
        return None

    if _description_has_exception(description, exception):
        return None

    owner_norm = (owner or "").strip().lower()
    offending_owner = owner_norm == "user"

    target = _parse_target_date(date)
    within_window = False
    if target is not None:
        days = _days_until(target)
        if 0 <= days < PROCRASTINATION_WINDOW_DAYS:
            within_window = True

    if not (offending_owner or within_window):
        return None

    reasons = []
    if offending_owner:
        reasons.append("owner=user")
    if within_window:
        reasons.append(f"date within {PROCRASTINATION_WINDOW_DAYS} days")

    lines = [
        "ERROR: Autonomy mandate active — nexo_followup_create blocked.",
        f"Marker: '{st.marker}' (source={st.source}, session={st.session_id or 'n/a'}).",
        f"Reason: {' + '.join(reasons)}.",
        "This followup looks like procrastination inside the current scope.",
        "Do the work now via nexo_task_open, or promote it to a goal/workflow.",
        "Exceptions that stay allowed even under the mandate:",
        "  - Download >1GB that must finish before the next step.",
        "  - Credential the operator must physically enter.",
        "  - Presence-dependent session with María or Nora.",
        "Pass exception='<reason>' or reference one of the keywords in the",
        "description to override. Do not use force='true' for this.",
    ]
    return "\n".join(lines)
