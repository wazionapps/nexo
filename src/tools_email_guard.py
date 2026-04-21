"""R06 email_send secret filter — Plan Consolidado Fase B.

Before any ``nexo_email_send`` / ``nexo_send`` actually ships a message,
callers invoke ``should_block_email_send(body, classifier=None)`` here.
If the helper returns ``(blocked=True, reason)``, the email is refused
and the operator gets a structured error instead of a leaked secret
landing in the outbox.

Two-layer detection:
  1. Regex pre-filter for high-confidence secret shapes: Bearer tokens,
     sk-/pk-/api_key/api-key/AWS-access-key/JWT/GitHub PAT/Shopify token,
     private keys, ``password=`` lines, MySQL ``-p<pass>`` on the same
     line. Mirrors the redaction patterns used by ``enforcement_engine``
     for Bash output (`_redact_for_log`) so what we refuse to ship is
     exactly what we already refuse to log.
  2. Optional LLM classifier (fail-closed): if ``classifier`` is passed
     and the regex did NOT fire, the classifier confirms before the
     email leaves. A classifier exception or ``unknown`` collapses to
     ``blocked=False`` so the message is allowed — the regex layer is
     the hard floor.

Separate from R23g (log redaction) because the outbound message is a
different surface: logs are private, emails are public.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional


# High-confidence secret shapes. Covered by tests.
SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bpk_(live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bshpat_[A-Za-z0-9]{32,}\b"),           # Shopify access token
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),             # GitHub PAT
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                 # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),  # JWT
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{12,}", re.I),
    re.compile(r"\bmysql\s+[^\n]*\s-p[^\s'\"]{3,}", re.I),
    re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]", re.I),
)

_LOCAL_SECRET_CONFIDENCE_THRESHOLD = float(
    os.environ.get("NEXO_EMAIL_GUARD_LOCAL_CONFIDENCE", "0.72")
)
_LOCAL_SECRET_LABELS = (
    "This email body contains a real secret, credential, API token, password, private key, or sensitive auth material.",
    "This email body is normal operational text and does not contain a secret or credential.",
)
_LOCAL_SECRET_CUES = (
    "secret",
    "credential",
    "credentials",
    "token",
    "api key",
    "auth",
    "password",
    "passwd",
    "pwd",
    "private key",
    "bearer",
    "jwt",
    "access key",
    "login",
    "contraseña",
    "clave",
    "credencial",
    "credenciales",
)
_LOCAL_SECRET_CLASSIFIER = None


def _regex_match(body: str) -> Optional[str]:
    if not isinstance(body, str) or not body:
        return None
    for p in SECRET_PATTERNS:
        m = p.search(body)
        if m:
            return m.group(0)[:80]
    return None


def _get_local_secret_classifier():
    global _LOCAL_SECRET_CLASSIFIER
    if _LOCAL_SECRET_CLASSIFIER is not None:
        return _LOCAL_SECRET_CLASSIFIER
    try:
        from classifier_local import LocalZeroShotClassifier  # type: ignore
    except Exception:
        _LOCAL_SECRET_CLASSIFIER = False  # type: ignore[assignment]
        return None
    try:
        _LOCAL_SECRET_CLASSIFIER = LocalZeroShotClassifier(
            confidence_floor=_LOCAL_SECRET_CONFIDENCE_THRESHOLD,
        )
    except Exception:
        _LOCAL_SECRET_CLASSIFIER = False  # type: ignore[assignment]
        return None
    return _LOCAL_SECRET_CLASSIFIER


def _classify_secret_with_local_model(body: str) -> Optional[bool]:
    if not isinstance(body, str) or len(body.strip()) < 24:
        return None
    lowered = body.lower()
    if not any(cue in lowered for cue in _LOCAL_SECRET_CUES):
        return None
    clf = _get_local_secret_classifier()
    if not clf or not clf.is_available():
        return None
    try:
        result = clf.classify(body, list(_LOCAL_SECRET_LABELS))
    except Exception:
        return None
    if result is None or float(result.confidence or 0.0) < _LOCAL_SECRET_CONFIDENCE_THRESHOLD:
        return None
    if result.label == _LOCAL_SECRET_LABELS[0]:
        return True
    if result.label == _LOCAL_SECRET_LABELS[1]:
        return False
    return None


def should_block_email_send(
    body: str,
    *,
    classifier: Optional[Callable[[str, str], Any]] = None,
) -> tuple[bool, str]:
    """Return ``(blocked, reason)``.

    Regex layer wins when it matches — the message is blocked.
    The classifier is only consulted when the regex is silent; it is an
    optional second opinion, fail-closed to "allow" so we never punish
    an honest email because the SDK was unavailable.
    """
    hit = _regex_match(body)
    if hit is not None:
        return True, f"secret pattern matched: {hit}"
    if classifier is None:
        local_verdict = _classify_secret_with_local_model(body)
        if local_verdict is True:
            return True, "local classifier flagged secret"
        return False, "ok"
    try:
        verdict = classifier(
            "Does the following email body contain a real secret "
            "(API key, password, private key, JWT, Bearer token, AWS "
            "key, etc.) that should NOT be transmitted? yes or no.",
            body,
        )
    except Exception:
        return False, "classifier unavailable"
    if verdict is True or verdict == "yes":
        return True, "classifier flagged secret"
    return False, "ok"


__all__ = ["SECRET_PATTERNS", "should_block_email_send"]
