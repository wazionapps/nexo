from __future__ import annotations

"""Require live evidence before root-cause claims in sensitive domains."""

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal

try:  # Reuse the existing ledger when available, but keep tests injectable.
    from evidence_ledger import search_evidence as _ledger_search
except Exception:  # pragma: no cover
    _ledger_search = None


Verdict = Literal["pass", "needs_evidence", "not_applicable"]
SearchFn = Callable[[str], Iterable[Any]]

_DOMAIN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "provider": (re.compile(r"\b(provider|proveedor|cloudflare|openai|shopify|stripe|google)\b", re.I),),
    "quota": (re.compile(r"\b(quota|cuota|limit|limite|l[ií]mite|billing agotado)\b", re.I),),
    "payment": (re.compile(r"\b(payment|pago|billing|facturaci[oó]n|tarjeta|stripe)\b", re.I),),
    "token": (re.compile(r"\b(token|api[_ -]?key|clave|secret|secreto|credential|credencial)\b", re.I),),
    "firewall": (re.compile(r"\b(firewall|csf|waf|iptables|cloud armor|bloque[oó])\b", re.I),),
    "production": (re.compile(r"\b(prod|producci[oó]n|live|deploy|cloud run|server|servidor)\b", re.I),),
    "customer_impact": (re.compile(r"\b(cliente|customer|venta|pedido|impacto|ca[ií]da|503|5xx)\b", re.I),),
}

_CAUSE_PATTERN = re.compile(
    r"\b(causa|root cause|porque|por\s+que|se debe a|caused by|culpa|fall[oó]\s+por|bloqueado por)\b",
    re.I,
)
_HEDGED_PATTERN = re.compile(
    r"\b(podr[ií]a|parece|posible|hip[oó]tesis|hay que verificar|no confirmado|maybe|might|could)\b|\?$",
    re.I,
)


@dataclass(frozen=True)
class EvidenceMatrixResult:
    verdict: Verdict
    domains: list[str]
    evidence_count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "domains": self.domains,
            "evidence_count": self.evidence_count,
            "reason": self.reason,
        }


def validate_claim(
    text: str,
    *,
    evidence_refs: Iterable[Any] | None = None,
    search: SearchFn | None = None,
) -> EvidenceMatrixResult:
    """Validate a sensitive causal claim against live evidence pointers."""

    claim = text or ""
    domains = _domains_for(claim)
    if not domains or not _CAUSE_PATTERN.search(claim) or _HEDGED_PATTERN.search(claim):
        return EvidenceMatrixResult("not_applicable", domains, 0, "non_causal_or_hedged")

    refs = [ref for ref in (evidence_refs or []) if ref]
    if refs:
        return EvidenceMatrixResult("pass", domains, len(refs), "explicit_evidence_refs")

    search_fn = search or _ledger_search
    if search_fn is not None:
        try:
            hits = list(search_fn(claim) or [])
        except Exception:
            hits = []
        if hits:
            return EvidenceMatrixResult("pass", domains, len(hits), "ledger_evidence")

    return EvidenceMatrixResult(
        "needs_evidence",
        domains,
        0,
        "cita un log, query, endpoint, commit o reproduccion antes de afirmar causa raiz",
    )


def _domains_for(text: str) -> list[str]:
    return [
        domain
        for domain, patterns in _DOMAIN_PATTERNS.items()
        if any(pattern.search(text or "") for pattern in patterns)
    ]

